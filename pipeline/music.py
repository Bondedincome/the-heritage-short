"""Stage 4: music.

ACE-Step 1.5 runs on Kaggle/Colab. Locally this stage can:
  placeholder - synthesise a quiet ambient pad with FFmpeg (used for the radio cut)
  bundle      - write N candidate jobs (m1..mN) keyed by content hash for the GPU notebook
  import      - copy returned candidates in and merge the notebook's call log
The film uses music.chosen if that candidate exists, otherwise the placeholder."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .common import OUT, content_key, film_duration, out_path, rel, run_ffmpeg
from .shots import merge_kaggle_log

DEFAULT_MODEL = "ACE-Step/ACE-Step-v1.5"


def _cands(sb: dict) -> list[dict]:
    m = sb["music"]
    n = m.get("candidates", 3)
    seeds = m.get("seeds") or [1000 + i for i in range(n)]
    dur = round(film_duration(sb) + 2)
    cues = "; ".join(f"{s['id']}: {s['music_cue']}" for s in sb["shots"] if s.get("music_cue"))
    out = []
    for i in range(n):
        job = {"id": f"m{i + 1}", "model": m.get("model", DEFAULT_MODEL), "prompt": m["prompt"],
               "lyrics": m.get("lyrics", "[instrumental]"), "duration": dur,
               "seed": seeds[i % len(seeds)], "cue_notes": cues}
        job["key"] = content_key("music-v1", job)
        job["output"] = f"{job['id']}-{job['key']}.wav"
        out.append(job)
    return out


def candidate_path(job: dict) -> Path:
    return out_path("music", job["id"], job["key"], "wav")


def placeholder_path(sb: dict) -> Path:
    return out_path("music", "placeholder", content_key("pad-v1", round(film_duration(sb) + 2)), "wav")


def make_placeholder(sb: dict) -> Path:
    p = placeholder_path(sb)
    if p.exists():
        return p
    d = round(film_duration(sb) + 2)
    # A soft A-minor-ish pad: three detuned sines, low-passed, slow tremolo, fades.
    expr = ("0.10*sin(2*PI*110*t)+0.07*sin(2*PI*164.8*t)+0.06*sin(2*PI*220.6*t)"
            "+0.04*sin(2*PI*261.6*t)*(0.5+0.5*sin(2*PI*0.05*t))")
    run_ffmpeg(["-f", "lavfi", "-i", f"aevalsrc='{expr}':s=48000:d={d}",
                "-af", f"lowpass=f=1200,tremolo=f=0.2:d=0.3,afade=t=in:d=3,afade=t=out:st={d - 4}:d=4",
                "-ac", "2", p])
    print(f"  placeholder music -> {rel(p)}")
    return p


def music_for_edit(sb: dict) -> tuple[Path, str]:
    chosen = sb["music"].get("chosen")
    if chosen:
        for job in _cands(sb):
            if job["id"] == chosen and candidate_path(job).exists():
                return candidate_path(job), chosen
        print(f"  ! music.chosen={chosen} but that candidate is not generated for the current prompt; using placeholder")
    return make_placeholder(sb), "placeholder"


def status(sb: dict) -> None:
    for job in _cands(sb):
        p = candidate_path(job)
        print(f"  {job['id']} seed={job['seed']}: {'ready  ' + rel(p) if p.exists() else 'missing'}")
    print(f"  chosen: {sb['music'].get('chosen')}")


BUNDLE_DIR = OUT / "kaggle" / "music_bundle"


def bundle(sb: dict) -> Path | None:
    if BUNDLE_DIR.exists():
        shutil.rmtree(BUNDLE_DIR)
    BUNDLE_DIR.mkdir(parents=True)
    jobs = [j for j in _cands(sb) if not candidate_path(j).exists()]
    if not jobs:
        print("  all candidates exist; nothing to bundle")
        return None
    p = BUNDLE_DIR / "music_jobs.json"
    p.write_text(json.dumps({"jobs": jobs}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  {len(jobs)} job(s) -> {rel(p)}\n  Upload with kaggle/ace_step_music.py (see kaggle/README.md).")
    return p


def import_returns(sb: dict, src: Path) -> None:
    src = Path(src)
    cur = {j["output"]: j for j in _cands(sb)}
    n = 0
    for f in sorted(src.rglob("*.wav")):
        job = cur.get(f.name)
        if not job:
            print(f"  stale or unknown, skipped: {f.name}")
            continue
        shutil.copy(f, candidate_path(job))
        n += 1
        print(f"  imported {f.name}")
    logs = sum(merge_kaggle_log(p) for p in src.rglob("runs_kaggle.jsonl"))
    print(f"[music import] {n} candidate(s), {logs} log line(s) merged")
