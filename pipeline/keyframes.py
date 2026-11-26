"""Stage 2: character/style reference sheets, then one square master keyframe per shot.

Every keyframe is a square master (default 1024x1024). Assembly crops it to 16:9
and 9:16 around shot.focus, so one image serves both exports."""
from __future__ import annotations

import io
from pathlib import Path

from . import providers
from .common import (Summary, characters_in, content_key, load_font, logged_call, out_path, rel,
                     select_shots, ROOT)

DEFAULT_IMAGE_MODEL = "@cf/black-forest-labs/flux-1-schnell"


def _cfg(sb: dict) -> dict:
    m = sb.get("models", {})
    return {
        "model": m.get("image", DEFAULT_IMAGE_MODEL),
        "steps": m.get("image_steps", 4),
        "width": m.get("image_width", 1024),
        "height": m.get("image_height", 1024),
    }


def _uses_refs(model: str) -> bool:
    return "flux-2" in model


def _normalise(img_bytes: bytes, w: int, h: int) -> bytes:
    """Centre-crop and resize to the master size; always store PNG."""
    from PIL import Image, ImageOps

    im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    im = ImageOps.fit(im, (w, h), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


# --------------------------------------------------------------------------- reference sheets
def refsheet_prompt(sb: dict, char: dict) -> str:
    return (f"{sb['style']['visual']}. Character reference sheet: {char['description']}. "
            "Three views side by side on a plain warm neutral background: front view, three-quarter view, "
            "side profile. Full body, same outfit in every view, even soft light, no text.")


def refsheet_key(sb: dict, char: dict) -> str:
    c = _cfg(sb)
    ref = [Path(ROOT / char["reference_image"])] if char.get("reference_image") else []
    return content_key("refsheet-v1", c, refsheet_prompt(sb, char), *ref)


def refsheet_path(sb: dict, char: dict) -> Path:
    return out_path("refsheet", char["id"], refsheet_key(sb, char), "png")


def run_refsheets(sb: dict, force: bool = False) -> Summary:
    summ = Summary("refsheet")
    c = _cfg(sb)
    for char in sb["characters"]:
        p = refsheet_path(sb, char)
        if p.exists() and not force:
            summ.cached.append(char["id"])
            continue
        prompt = refsheet_prompt(sb, char)
        refs = [(ROOT / char["reference_image"]).read_bytes()] if char.get("reference_image") else []
        print(f"  refsheet {char['id']} -> {rel(p)}")
        try:
            img = logged_call(
                lambda: providers.cloudflare_image(c["model"], prompt, width=c["width"], height=c["height"],
                                                   steps=c["steps"], seed=None, refs=refs if _uses_refs(c["model"]) else None),
                stage="refsheet", provider="cloudflare", model=c["model"], shot_id=char["id"],
                params={"prompt": prompt, "steps": c["steps"], "w": c["width"], "h": c["height"], "n_refs": len(refs)},
                input_key=refsheet_key(sb, char), extra=lambda b: {"output": rel(p), "bytes": len(b)})
            p.write_bytes(_normalise(img, c["width"], c["height"]))
            summ.made.append(char["id"])
        except Exception as e:
            summ.failed.append((char["id"], str(e)))
    summ.report()
    return summ


# --------------------------------------------------------------------------- keyframes
def keyframe_prompt(sb: dict, shot: dict) -> str:
    chars = characters_in(sb, shot)
    who = " ".join(f"{c['name']}: {c['description']}." for c in chars)
    return (f"{sb['style']['visual']}. {shot['keyframe']['prompt']}. {who} "
            "Main subject near the centre of the frame, readable when cropped to portrait or landscape. No text.").strip()


def _refs_for(sb: dict, shot: dict) -> list[Path]:
    if not _uses_refs(_cfg(sb)["model"]):
        return []
    return [p for p in (refsheet_path(sb, c) for c in characters_in(sb, shot)) if p.exists()]


def keyframe_key(sb: dict, shot: dict) -> str:
    c = _cfg(sb)
    return content_key("keyframe-v1", c, keyframe_prompt(sb, shot), shot["keyframe"].get("seed"), *_refs_for(sb, shot))


def keyframe_path(sb: dict, shot: dict) -> Path:
    return out_path("keyframes", shot["id"], keyframe_key(sb, shot), "png")


def placeholder_keyframe(sb: dict, shot: dict, why: str) -> Path:
    """Grey slate with the prompt on it, so the edit still builds when a call failed."""
    from PIL import Image, ImageDraw

    c = _cfg(sb)
    p = out_path("keyframes", f"{shot['id']}-PLACEHOLDER", keyframe_key(sb, shot), "png")
    im = Image.new("RGB", (c["width"], c["height"]), (40, 40, 44))
    d = ImageDraw.Draw(im)
    f = load_font(34)
    text = f"{shot['id']} - keyframe missing\n\n{shot['keyframe']['prompt'][:300]}\n\n({why[:120]})"
    import textwrap

    wrapped = "\n".join(textwrap.fill(line, 46) for line in text.split("\n"))
    d.multiline_text((60, 60), wrapped, font=f, fill=(220, 220, 220), spacing=8)
    im.save(p)
    return p


def keyframe_for_edit(sb: dict, shot: dict) -> Path:
    p = keyframe_path(sb, shot)
    if p.exists():
        return p
    ph = out_path("keyframes", f"{shot['id']}-PLACEHOLDER", keyframe_key(sb, shot), "png")
    return ph if ph.exists() else placeholder_keyframe(sb, shot, "not generated yet")


def run_keyframes(sb: dict, only: str | None = None, force: bool = False) -> Summary:
    summ = Summary("keyframes")
    c = _cfg(sb)
    for shot in select_shots(sb, only):
        p = keyframe_path(sb, shot)
        if p.exists() and not force:
            summ.cached.append(shot["id"])
            continue
        prompt = keyframe_prompt(sb, shot)
        refs = _refs_for(sb, shot)
        seed = shot["keyframe"].get("seed")
        print(f"  keyframe {shot['id']} -> {rel(p)}")
        try:
            img = logged_call(
                lambda: providers.cloudflare_image(c["model"], prompt, width=c["width"], height=c["height"],
                                                   steps=c["steps"], seed=seed, refs=[r.read_bytes() for r in refs]),
                stage="keyframes", provider="cloudflare", model=c["model"], shot_id=shot["id"], seed=seed,
                params={"prompt": prompt, "steps": c["steps"], "w": c["width"], "h": c["height"],
                        "refs": [rel(r) for r in refs]},
                input_key=keyframe_key(sb, shot), extra=lambda b: {"output": rel(p), "bytes": len(b)})
            p.write_bytes(_normalise(img, c["width"], c["height"]))
            summ.made.append(shot["id"])
        except Exception as e:
            summ.failed.append((shot["id"], str(e)))
            placeholder_keyframe(sb, shot, str(e))
    summ.report()
    return summ
