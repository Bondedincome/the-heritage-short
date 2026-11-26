"""Stage 6: assembly, all in FFmpeg and Pillow, driven by the storyboard.

  per shot   crop the square master to the target aspect around shot.focus, scale,
             stretch (max 1.5x slow-down) then hold the last frame to fill shot.duration
  end card   rendered with Pillow ("made with AI" + credits)
  audio      narration lines placed at shot start + offset; score side-chain ducked
             under the voice; loudness-normalised (EBU R128, default -14 LUFS)
  subtitles  English lines rendered to PNG with Pillow and overlaid (no libass/fontconfig
             dependency, so it behaves the same on Windows, macOS and Linux); .srt sidecar too
Exports: outputs/final/film-horizontal.mp4, film-vertical.mp4, radio-cut.mp4, film.en.srt, edl.json
"""
from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path

from .common import (OUT, content_key, film_duration, load_font, media_duration, rel, run_ffmpeg, timeline)
from .music import music_for_edit
from .narration import narration_path, wav_duration
from .shots import clip_for_edit

MAX_SLOWDOWN = 1.5
FINAL = OUT / "final"


def _size(sb: dict, aspect: str) -> tuple[int, int]:
    w, h = sb["format"][aspect]
    return int(w) // 2 * 2, int(h) // 2 * 2


# --------------------------------------------------------------------------- video segments
def shot_segment(sb: dict, shot: dict, aspect: str, is_first: bool) -> tuple[Path, str]:
    src, engine = clip_for_edit(sb, shot)
    W, H = _size(sb, aspect)
    fps = sb["format"]["fps"]
    dur = shot["duration"]
    focus = {"x": 0.5, "y": 0.5, **shot.get("focus", {})}
    key = content_key("seg-v1", src, W, H, fps, dur, focus, is_first)
    dest = OUT / "build" / "segments" / aspect / f"{shot['id']}-{key}.mp4"
    if dest.exists():
        return dest, engine
    dest.parent.mkdir(parents=True, exist_ok=True)
    src_dur = media_duration(src)
    stretch = min(MAX_SLOWDOWN, dur / src_dur) if src_dur < dur else 1.0
    a = W / H
    vf = [
        f"setpts={stretch:.4f}*PTS",
        f"crop=w='min(iw,ih*{a:.6f})':h='min(ih,iw/{a:.6f})':x='(iw-ow)*{focus['x']}':y='(ih-oh)*{focus['y']}'",
        f"scale={W}:{H}:flags=lanczos,setsar=1",
        f"fps={fps}",
        f"tpad=stop_mode=clone:stop_duration={dur + 1}",
        f"trim=duration={dur},setpts=PTS-STARTPTS",
    ]
    if is_first:
        vf.append("fade=t=in:st=0:d=0.8")
    vf.append("format=yuv420p")
    run_ffmpeg(["-i", src, "-an", "-vf", ",".join(vf), "-r", fps, "-c:v", "libx264", "-crf", "19",
                "-preset", "medium", dest])
    return dest, engine


def end_card(sb: dict, aspect: str) -> Path:
    from PIL import Image, ImageDraw

    W, H = _size(sb, aspect)
    ec = sb["end_card"]
    key = content_key("endcard-v1", ec, W, H, sb["format"]["fps"])
    dest = OUT / "build" / "segments" / aspect / f"endcard-{key}.mp4"
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", (W, H), (12, 12, 14))
    d = ImageDraw.Draw(im)
    big = load_font(int(min(W, H) * 0.075), ec.get("font"))
    small = load_font(int(min(W, H) * 0.038), ec.get("font"))
    wrap_chars = 22 if W < H else 40
    blocks = []
    for i, line in enumerate(ec["lines"]):
        f = big if i == 0 else small
        for sub in textwrap.wrap(line, wrap_chars if i == 0 else int(wrap_chars * 1.8)) or [""]:
            bb = d.textbbox((0, 0), sub, font=f)
            blocks.append((sub, f, bb[2] - bb[0], bb[3] - bb[1], i == 0))
        blocks.append(("", small, 0, int(min(W, H) * 0.03), False))
    total = sum(b[3] + 12 for b in blocks)
    y = (H - total) // 2
    for text, f, w, h, first in blocks:
        if text:
            d.text(((W - w) // 2, y), text, font=f, fill=(245, 240, 230) if first else (190, 185, 175))
        y += h + 12
    png = dest.with_suffix(".png")
    im.save(png)
    dur = ec["duration"]
    run_ffmpeg(["-loop", "1", "-i", png, "-t", dur, "-vf",
                f"fps={sb['format']['fps']},fade=t=in:st=0:d=0.6,fade=t=out:st={dur - 1}:d=1,format=yuv420p",
                "-c:v", "libx264", "-crf", "19", dest])
    return dest


def black_segment(sb: dict, aspect: str, dur: float) -> Path:
    W, H = _size(sb, aspect)
    dest = OUT / "build" / "segments" / aspect / f"black-{content_key(W, H, dur, sb['format']['fps'])}.mp4"
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        run_ffmpeg(["-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={sb['format']['fps']}:d={dur}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", dest])
    return dest


def concat(segments: list[Path], dest: Path) -> None:
    lst = dest.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in segments), encoding="utf-8")
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", dest])


# --------------------------------------------------------------------------- audio
def mix_audio(sb: dict) -> Path:
    T = round(film_duration(sb), 3)
    music, music_id = music_for_edit(sb)
    lines = []
    for shot, start in timeline(sb):
        p = narration_path(sb, shot)
        if p and p.exists():
            lines.append((p, start + shot["narration"].get("offset", 0.3)))
    m = sb["music"]
    vol, ratio = m.get("volume_db", -6), m.get("duck_ratio", 8)
    lufs = sb["format"].get("loudness_lufs", -14)
    key = content_key("mix-v1", music, [(p, round(t, 3)) for p, t in lines], T, vol, ratio, lufs)
    dest = OUT / "build" / "audio" / f"mix-{key}.wav"
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = ["-stream_loop", "-1", "-i", music]
    for p, _ in lines:
        args += ["-i", p]
    norm = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    g = [f"[0:a]{norm},atrim=0:{T},asetpts=PTS-STARTPTS,volume={vol}dB,"
         f"afade=t=in:d=1.5,afade=t=out:st={max(0, T - 3)}:d=3[mus]"]
    if lines:
        for i, (_, t) in enumerate(lines, start=1):
            g.append(f"[{i}:a]{norm},adelay=delays={int(t * 1000)}:all=1[n{i}]")
        labels = "".join(f"[n{i}]" for i in range(1, len(lines) + 1))
        if len(lines) > 1:
            g.append(f"{labels}amix=inputs={len(lines)}:normalize=0:duration=longest,apad=whole_dur={T}[narr]")
        else:
            g.append(f"{labels}apad=whole_dur={T}[narr]")
        g.append("[narr]asplit=2[nv][sc]")
        g.append(f"[mus][sc]sidechaincompress=threshold=0.015:ratio={ratio}:attack=30:release=600[duck]")
        g.append(f"[duck][nv]amix=inputs=2:normalize=0:duration=first[mix]")
    else:
        g.append("[mus]anull[mix]")
    g.append(f"[mix]loudnorm=I={lufs}:TP=-1.5:LRA=11,aresample=48000,atrim=0:{T}[out]")
    run_ffmpeg([*args, "-filter_complex", ";".join(g), "-map", "[out]", "-ar", 48000, "-ac", 2, dest])
    print(f"  audio mix ({music_id}, {len(lines)} narration lines) -> {rel(dest)}")
    return dest


# --------------------------------------------------------------------------- subtitles
def _chunks(text: str, max_chars: int = 84) -> list[str]:
    parts = re.split(r"(?<=[.!?;:])\s+|(?<=,)\s+(?=\S{4,})", text.strip())
    out: list[str] = []
    for p in parts:
        if out and len(out[-1]) + 1 + len(p) <= max_chars:
            out[-1] += " " + p
        else:
            out.extend(textwrap.wrap(p, max_chars) or [p])
    return [o for o in out if o.strip()]


def subtitle_events(sb: dict) -> list[tuple[float, float, str]]:
    events = []
    for shot, start in timeline(sb):
        n = shot.get("narration")
        if not n or not n.get("english", "").strip():
            continue
        t0 = start + n.get("offset", 0.3)
        p = narration_path(sb, shot)
        span = wav_duration(p) if p and p.exists() else len(n["english"]) / 15
        span = min(span, start + shot["duration"] - t0 - 0.05)
        chunks = _chunks(n["english"])
        total = sum(len(c) for c in chunks)
        t = t0
        for c in chunks:
            d = span * len(c) / total
            events.append((round(t, 3), round(t + d, 3), c))
            t += d
    return events


def write_srt(events, dest: Path) -> None:
    def ts(x):
        ms = int(round(x * 1000))
        return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"

    dest.write_text("".join(f"{i}\n{ts(a)} --> {ts(b)}\n{t}\n\n" for i, (a, b, t) in enumerate(events, 1)),
                    encoding="utf-8")


def subtitle_pngs(sb: dict, aspect: str, events) -> list[tuple[Path, float, float, int]]:
    from PIL import Image, ImageDraw

    W, H = _size(sb, aspect)
    size = int(min(W, H) * (0.05 if W < H else 0.045))
    font = load_font(size, sb["format"].get("subtitle_font"))
    wrap = 26 if W < H else 42
    d_dir = OUT / "build" / "subs" / aspect
    d_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for i, (a, b, text) in enumerate(events):
        lines = "\n".join(textwrap.wrap(text, wrap))
        p = d_dir / f"sub-{content_key('sub-v1', lines, size, W)}.png"
        probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        bb = probe.multiline_textbbox((0, 0), lines, font=font, stroke_width=3, align="center", spacing=6)
        w, h = int(bb[2] - bb[0]) + 24, int(bb[3] - bb[1]) + 20
        if not p.exists():
            im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            ImageDraw.Draw(im).multiline_text((12 - bb[0], 10 - bb[1]), lines, font=font, fill=(255, 255, 255, 255),
                                              stroke_width=3, stroke_fill=(0, 0, 0, 255), align="center", spacing=6)
            im.save(p)
        # Vertical: keep captions above the platform UI at the bottom of the screen.
        y = int(H * 0.68) if W < H else int(H - h - H * 0.06)
        out.append((p, a, b, y))
    return out


# --------------------------------------------------------------------------- build
def build(sb: dict, aspect: str, mode: str = "film") -> Path:
    FINAL.mkdir(parents=True, exist_ok=True)
    shots_T = sum(s["duration"] for s in sb["shots"])
    edl = []
    if mode == "radio":
        segs = [black_segment(sb, aspect, shots_T)]
    else:
        segs = []
        for i, (shot, start) in enumerate(timeline(sb)):
            seg, engine = shot_segment(sb, shot, aspect, i == 0)
            segs.append(seg)
            edl.append({"shot": shot["id"], "start": round(start, 2), "duration": shot["duration"], "engine": engine,
                        "segment": rel(seg)})
    segs.append(end_card(sb, aspect))
    video = OUT / "build" / f"{mode}-{aspect}-video.mp4"
    concat(segs, video)
    audio = mix_audio(sb)
    events = subtitle_events(sb)
    write_srt(events, FINAL / "film.en.srt")
    subs = subtitle_pngs(sb, aspect, events)

    dest = FINAL / ("radio-cut.mp4" if mode == "radio" else f"film-{aspect}.mp4")
    args = ["-i", video, "-i", audio]
    for p, *_ in subs:
        args += ["-i", p]
    if subs:
        g, last = [], "0:v"
        for i, (_, a, b, y) in enumerate(subs):
            g.append(f"[{last}][{i + 2}:v]overlay=x=(W-w)/2:y={y}:enable='between(t,{a},{b})'[v{i}]")
            last = f"v{i}"
        args += ["-filter_complex", ";".join(g), "-map", f"[{last}]"]
    else:
        args += ["-map", "0:v"]
    tools = "Gemini TTS, FLUX (Cloudflare Workers AI), Wan 2.2, ACE-Step 1.5, FFmpeg"
    args += ["-map", "1:a", "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
             "-metadata", f"title={sb['title']}",
             "-metadata", f"comment=Made with AI ({tools}). Storyboard-driven pipeline.", dest]
    run_ffmpeg(args)
    music, music_id = music_for_edit(sb)
    if mode == "film":
        (FINAL / f"edl-{aspect}.json").write_text(json.dumps(
            {"aspect": aspect, "size": _size(sb, aspect), "duration": film_duration(sb), "music": music_id,
             "audio": rel(audio), "shots": edl, "subtitles": len(events)}, indent=2), encoding="utf-8")
    print(f"[assemble] {mode} {aspect}: {rel(dest)} ({film_duration(sb):.1f}s)")
    return dest
