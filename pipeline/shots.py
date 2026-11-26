"""Stage 3: moving shots.

Wan 2.2 runs on Kaggle, not here. This stage therefore has three jobs:
  bundle  - write the Wan jobs whose clip is missing (keyed by content hash) for upload to Kaggle
  import  - copy returned clips into outputs/shots/wan/ and merge the Kaggle call log into runs.jsonl
  render  - for every shot, use the Wan clip if one exists for the *current* inputs,
            otherwise render a Ken Burns pan/zoom locally with FFmpeg (no model call).
"""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from .common import (OUT, RUNS, Summary, content_key, out_path, rel, run_ffmpeg, select_shots)
from .keyframes import keyframe_for_edit, keyframe_path

DEFAULT_VIDEO = {"model": "Wan-AI/Wan2.2-TI2V-5B-Diffusers", "width": 704, "height": 704, "num_frames": 121,
                 "fps": 24, "steps": 30, "guidance": 5.0, "seed": 42}
NEGATIVE = ("blurry, distorted face, extra fingers, deformed hands, text, watermark, subtitles, "
            "flicker, jitter, low quality, oversaturated")


def video_cfg(sb: dict) -> dict:
    return {**DEFAULT_VIDEO, **sb.get("models", {}).get("video", {})}


def wan_key(sb: dict, shot: dict) -> str | None:
    kf = keyframe_path(sb, shot)
    if not kf.exists():
        return None
    neg = sb["style"].get("negative") or NEGATIVE
    return content_key("wan-v1", video_cfg(sb), shot["motion"].get("prompt", ""), neg, kf)


def wan_path(sb: dict, shot: dict) -> Path | None:
    k = wan_key(sb, shot)
    return out_path("shots/wan", shot["id"], k, "mp4") if k else None


def kenburns_path(sb: dict, shot: dict) -> Path:
    kf = keyframe_for_edit(sb, shot)
    kb = shot["motion"].get("kenburns", {})
    return out_path("shots/kenburns", shot["id"],
                    content_key("kb-v1", kb, shot["duration"], sb["format"]["fps"], kf), "mp4")


def render_kenburns(sb: dict, shot: dict, dest: Path) -> None:
    kf = keyframe_for_edit(sb, shot)
    kb = {"zoom_from": 1.0, "zoom_to": 1.12, "x_from": 0.5, "x_to": 0.5, "y_from": 0.5, "y_to": 0.5,
          **shot["motion"].get("kenburns", {})}
    fps = sb["format"]["fps"]
    n = max(1, round(shot["duration"] * fps))
    from PIL import Image

    w, h = Image.open(kf).size
    lerp = lambda a, b: f"({a}+({b}-{a})*on/{n})"  # noqa: E731
    # Upscale 2x before zoompan so sub-pixel pans don't jitter.
    vf = (f"scale={w * 2}:{h * 2}:flags=lanczos,"
          f"zoompan=z='{lerp(kb['zoom_from'], kb['zoom_to'])}'"
          f":x='(iw-iw/zoom)*{lerp(kb['x_from'], kb['x_to'])}'"
          f":y='(ih-ih/zoom)*{lerp(kb['y_from'], kb['y_to'])}'"
          f":d={n}:s={w}x{h}:fps={fps},format=yuv420p")
    run_ffmpeg(["-i", kf, "-vf", vf, "-frames:v", n, "-c:v", "libx264", "-crf", "18", "-preset", "fast", dest])


def clip_for_edit(sb: dict, shot: dict) -> tuple[Path, str]:
    """(path, engine) - the clip assembly should use for this shot right now."""
    if shot["motion"]["engine"] == "wan":
        wp = wan_path(sb, shot)
        if wp and wp.exists():
            return wp, "wan"
    kp = kenburns_path(sb, shot)
    if not kp.exists():
        render_kenburns(sb, shot, kp)
    return kp, "kenburns"


def run_render(sb: dict, only: str | None = None, force: bool = False) -> Summary:
    summ = Summary("shots")
    for shot in select_shots(sb, only):
        try:
            kb = kenburns_path(sb, shot)
            if force:
                kb.unlink(missing_ok=True)
            existed = kb.exists()
            p, engine = clip_for_edit(sb, shot)
            print(f"  {shot['id']}: {engine:8s} {rel(p)}")
            (summ.made if engine == "kenburns" and not existed else summ.cached).append(shot["id"])
        except Exception as e:
            summ.failed.append((shot["id"], str(e)))
    missing = [s["id"] for s in sb["shots"] if s["motion"]["engine"] == "wan"
               and not ((wp := wan_path(sb, s)) and wp.exists())]
    if missing:
        print(f"  note: no current Wan clip for {missing}; using Ken Burns. Run `python run.py shots --bundle`.")
    summ.report()
    return summ


# --------------------------------------------------------------------------- Kaggle hand-off
BUNDLE_DIR = OUT / "kaggle" / "shots_bundle"


def bundle(sb: dict, only: str | None = None) -> Path | None:
    if BUNDLE_DIR.exists():
        shutil.rmtree(BUNDLE_DIR)
    (BUNDLE_DIR / "keyframes").mkdir(parents=True)
    cfg = video_cfg(sb)
    neg = sb["style"].get("negative") or NEGATIVE
    jobs = []
    for shot in select_shots(sb, only):
        if shot["motion"]["engine"] != "wan":
            continue
        kf, wp = keyframe_path(sb, shot), wan_path(sb, shot)
        if not kf.exists():
            print(f"  skip {shot['id']}: keyframe not generated yet")
            continue
        if wp.exists():
            continue
        key = wan_key(sb, shot)
        name = f"{shot['id']}-{key}"
        shutil.copy(kf, BUNDLE_DIR / "keyframes" / f"{name}.png")
        jobs.append({"shot_id": shot["id"], "key": key, "output": f"{name}.mp4", "image": f"keyframes/{name}.png",
                     "prompt": f"{sb['style']['visual']}. {shot['motion']['prompt']}", "negative_prompt": neg, **cfg})
    if not jobs:
        print("  every Wan shot already has a current clip; nothing to bundle")
        return None
    (BUNDLE_DIR / "jobs.json").write_text(json.dumps({"jobs": jobs}, indent=2, ensure_ascii=False), encoding="utf-8")
    zpath = OUT / "kaggle" / "shots_bundle.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in BUNDLE_DIR.rglob("*"):
            z.write(f, f.relative_to(BUNDLE_DIR))
    print(f"  {len(jobs)} job(s) -> {rel(zpath)}\n  Upload it as a Kaggle dataset and run kaggle/wan22_i2v.py (see kaggle/README.md).")
    return zpath


def merge_kaggle_log(src: Path) -> int:
    if not src.exists():
        return 0
    seen = set()
    if RUNS.exists():
        for line in RUNS.read_text(encoding="utf-8").splitlines():
            try:
                cid = json.loads(line).get("call_id")
                if cid:
                    seen.add(cid)
            except json.JSONDecodeError:
                pass
    added = 0
    with RUNS.open("a", encoding="utf-8") as f:
        for line in src.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("call_id") in seen:
                continue
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            added += 1
    return added


def import_returns(sb: dict, src: Path) -> None:
    src = Path(src)
    current = {s["id"]: wan_key(sb, s) for s in sb["shots"]}
    n = 0
    for mp4 in sorted(src.rglob("*.mp4")):
        sid, _, key = mp4.stem.partition("-")
        if current.get(sid) != key:
            print(f"  stale or unknown, skipped: {mp4.name} (current key for {sid}: {current.get(sid)})")
            continue
        dest = out_path("shots/wan", sid, key, "mp4")
        shutil.copy(mp4, dest)
        n += 1
        print(f"  imported {mp4.name}")
    logs = sum(merge_kaggle_log(p) for p in src.rglob("runs_kaggle.jsonl"))
    print(f"[shots import] {n} clip(s), {logs} log line(s) merged into runs.jsonl")
