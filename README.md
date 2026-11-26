# The Heritage Short

A small film studio in code. One file, [`storyboard.json`](storyboard.json), describes a 60–90 second
family-heritage film: shots, durations, image and motion prompts, narration in the family's language,
English subtitles and music cues. Five stages read that file and write into `outputs/`:

```
storyboard.json ──► refsheet ─► keyframes ─► shots ─┐
   (validated)                                       ├─► assemble ─► film-vertical.mp4 (9:16)
                ──► narration (Gemini TTS) ──────────┤               film-horizontal.mp4 (16:9)
                ──► music (ACE-Step / placeholder) ──┘               film.en.srt, edl-*.json
```

Every model call is logged to [`runs.jsonl`](runs.jsonl). Every output is named by a hash of its inputs, so
a re-run only regenerates what changed: edit one shot's prompt, and only that shot is regenerated.

## Run it

```bash
git clone <this repo> && cd the-heritage-short
python -m pip install -r requirements.txt     # Python 3.10+; ffmpeg is bundled via imageio-ffmpeg
cp .env.example .env                          # add your free keys (see below)
python run.py film                            # or: make film
```

`python run.py film` validates the storyboard, then runs refsheet → keyframes → shots → narration → assemble,
and writes `outputs/final/film-vertical.mp4` and `outputs/final/film-horizontal.mp4`.

**From a clean clone with the committed `outputs/`, the film rebuilds with zero model calls.** Every stage
finds its cached output. Without keys, the pipeline still builds a film: missing keyframes become labelled
slates, and missing Wan clips become Ken Burns pans.

### Keys (all free, no card)
| Variable | Where |
|---|---|
| `GEMINI_API_KEY` | Google AI Studio → Get API key |
| `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN` | Cloudflare dashboard → Workers AI → Use REST API |

`.env` is git-ignored. Run `python run.py smoke` to call each provider once and see what works.

## Commands

| Command | What it does |
|---|---|
| `python run.py validate` | JSON Schema + cross-field checks (unique ids, known characters, consent for real people, 60–90 s) |
| `python run.py smoke` | one call per provider, latency logged, lists available Gemini TTS models |
| `python run.py script draft` | Gemini drafts `storyboard.draft.json` from `story/notes.md` + `DESIGN.md`; invalid drafts are sent back with the validator's errors |
| `python run.py script translate` | fills empty `narration.text` from `narration.english`, marked `machine_translated` |
| `python run.py refsheet` | character reference sheet per character |
| `python run.py keyframes [--only s03] [--force]` | square master keyframe per shot (FLUX on Workers AI) |
| `python run.py shots --bundle` | packs Wan 2.2 jobs whose clip is missing → `outputs/kaggle/shots_bundle.zip` |
| `python run.py shots --import DIR` | imports clips from Kaggle and merges the notebook's call log into `runs.jsonl` |
| `python run.py shots` | resolves each shot: current Wan clip, otherwise Ken Burns pan/zoom (FFmpeg) |
| `python run.py music [--bundle \| --import DIR]` | placeholder score, or ACE-Step candidates via Kaggle |
| `python run.py narration [--only s03]` | one WAV per line, Gemini TTS |
| `python run.py radio` | narration + placeholder score on black with subtitles (first user test) |
| `python run.py assemble [--aspect vertical]` | the edit, both aspect ratios |
| `python run.py costs` | per stage/model call counts, latency, GPU minutes, list-price equivalent |

GPU stages (Wan 2.2, ACE-Step 1.5) run on Kaggle: see [kaggle/README.md](kaggle/README.md).

## Regenerate one shot (walkthrough demo)
```bash
# change shots[2].keyframe.prompt in storyboard.json, then:
python run.py film
#  [keyframes] generated 1, cached 9      <- one new model call
tail -1 runs.jsonl                         # the single new call, with model, seed, latency
```

## What it costs
Running the pipeline costs $0 on free tiers. Measured latency, GPU minutes and list-price equivalents
are in [COSTS.md](COSTS.md), produced from `runs.jsonl` by `python run.py costs`.

## Repository map
| Path | Contents |
|---|---|
| `storyboard.json`, `schema/` | the contract and its JSON Schema |
| `pipeline/` | one module per stage, plus `common.py` (cache, retries, logging) and `providers.py` (HTTP clients) |
| `kaggle/` | GPU-side scripts for Wan 2.2 and ACE-Step |
| `outputs/` | generated media, kept so a run can be inspected and rebuilt |
| `runs.jsonl` | one line per model call attempt |
| [ARCHITECTURE.md](ARCHITECTURE.md) | stages, contracts, caching, failure handling |
| [DESIGN.md](DESIGN.md) | audience, intent, user tests, what changed |
| [EXPERIMENTS.md](EXPERIMENTS.md) · [FAILURES.md](FAILURES.md) · [COSTS.md](COSTS.md) · [ETHICS.md](ETHICS.md) | the evidence |

The film carries a visible "Made with AI" end card and an MP4 metadata comment naming the tools.
