# Architecture

## Principle
`storyboard.json` is the only contract. Every stage is a pure-ish function of
(storyboard fields it reads, upstream files) → files in `outputs/`. No stage calls another stage's
model. A stage calls only the path functions of upstream stages (`keyframe_path`, `narration_path`, ...),
which compute where an output *would* be for the current inputs.

## Stages

| Stage | Module | Reads | Writes | Runs on |
|---|---|---|---|---|
| validate | `common.validate_storyboard` | storyboard, schema | — | local |
| script | `script.py` | `story/notes.md`, `DESIGN.md`, schema | `storyboard.draft.json` or narration.text | Gemini |
| refsheet | `keyframes.run_refsheets` | style, characters | `outputs/refsheet/<char>-<key>.png` | Workers AI |
| keyframes | `keyframes.run_keyframes` | style, characters, shot.keyframe, refsheets* | `outputs/keyframes/<shot>-<key>.png` (square master) | Workers AI |
| shots | `shots.py` | keyframe, shot.motion, models.video | `outputs/shots/wan/…mp4` (from Kaggle) or `outputs/shots/kenburns/…mp4` | Kaggle GPU / local FFmpeg |
| music | `music.py` | music, film duration, music cues | `outputs/music/m<i>-<key>.wav`, placeholder | Kaggle GPU / local FFmpeg |
| narration | `narration.py` | voice, shot.narration.text | `outputs/narration/<shot>-<key>.wav` | Gemini TTS |
| assemble | `assemble.py` | everything above | `outputs/build/…`, `outputs/final/…` | local FFmpeg + Pillow |

\* With FLUX.2 models, the character's reference sheet is sent as a reference image. Its hash then becomes
part of the keyframe key, so a new reference sheet invalidates exactly the keyframes that feature that character.

## Contracts
- **Schema** (`schema/storyboard.schema.json`, JSON Schema 2020-12, `additionalProperties: false`
  everywhere so typos fail loudly). Cross-field rules in code: unique shot ids, characters exist, `wan`
  shots have a motion prompt, real people need `consent_ref`, `music.chosen` names a candidate, and
  a warning when the film is outside 60–90 s or a line is machine-translated and unreviewed.
- **Square master.** Keyframes and Wan clips are square. Assembly crops each to 16:9 and 9:16 around
  `shot.focus` (0–1). One generation therefore serves both exports, and keyframe prompts ask for a centred subject.
- **Kaggle hand-off.** `jobs.json` carries the content key for each job. The notebook names each output
  `<shot>-<key>.mp4` and logs `runs_kaggle.jsonl` in the same format as `runs.jsonl`. Import accepts
  only files whose key matches the current storyboard. Stale clips are refused, not silently used.

## Caching
`content_key(...)` = first 12 hex characters of sha256 over the JSON of every input that affects the output.
Files are included by content digest, not by path. The key is part of the filename, so:
- cache hit = the file exists; there is no manifest that can drift out of date;
- change one shot's prompt → only that shot's keyframe key changes → its Wan key, Ken Burns key and
  assembly segment key change → everything else is reused;
- changing the global style changes every key (correct: every image depends on it);
- old outputs are never overwritten, so A/B comparisons stay on disk.
File digests are memoised on (size, mtime) in `outputs/.digests.json`, so large videos are hashed once.
Cache versions (`"keyframe-v1"`, `"seg-v1"`, …) are part of each key. Bumping one invalidates a
stage when its code changes meaningfully.

## Failure handling
- **Retries:** `common.logged_call` wraps every model call. On HTTP 429/5xx or a network error it uses
  exponential backoff with jitter, and honours `Retry-After` and Gemini's `retryDelay`. A **daily** quota
  error is detected and not retried. Every attempt, including retries, is one line in `runs.jsonl`.
- **Missing keys** raise `ConfigError` and fail fast. They are not logged as model calls.
- **Degrade, don't stop:** a failed keyframe becomes a labelled grey slate; a missing Wan clip becomes a
  Ken Burns pan; a missing chosen score becomes a synthesised placeholder pad; a failed TTS line is
  skipped, but its subtitle still shows. Each stage prints a summary (generated / cached / failed), so
  `film` always produces a watchable cut and says what is missing.
- **Model drift:** `"auto"` model ids resolve to the newest matching Gemini model from the live
  model list, so a retired preview does not break the run. The resolved id is logged with every call.
- **Fit checks:** narration prints a warning when a line is longer than its shot. Assembly slows a
  short clip by at most 1.5x, then holds the last frame.

## Assembly details
- Per shot: `setpts` stretch → crop to aspect at focus → Lanczos scale → fps → `tpad` clone → trim.
- Audio: narration lines placed with `adelay` at shot start + offset; the score is side-chain
  compressed by the narration (ducking), then `loudnorm` to `format.loudness_lufs` (−14 LUFS default).
  The mix is shared by both aspect ratios.
- Subtitles: English chunks timed in proportion to their length within each narration WAV's duration,
  rendered to PNG with Pillow and overlaid. This avoids libass/fontconfig differences across operating
  systems and allows any .ttf (e.g. an Ethiopic font). An `.srt` sidecar is written for platforms.
- End card: Pillow-rendered "Made with AI" + credits. The MP4 metadata comment also names the tools.
- `outputs/final/edl-<aspect>.json` records, per shot, which engine (wan/kenburns) and which
  segment went into the cut, plus which music track.

## Logging schema (`runs.jsonl`)
`ts, run_id, stage, shot_id, provider, model, params, seed, input_key, attempt, status (ok|retry|error),
latency_s, cost_usd, http_status?, error?, output?, usage?, audio_s?, gpu_seconds? (Kaggle),
gpu?, peak_vram_gb?, call_id? (Kaggle, used to de-duplicate on import)`.
