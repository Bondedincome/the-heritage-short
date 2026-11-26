"""Stage 5: narration, one WAV per shot line, with Gemini TTS."""
from __future__ import annotations

import wave
from pathlib import Path

from . import providers
from .common import Summary, content_key, logged_call, out_path, rel, select_shots


def tts_text(sb: dict, shot: dict) -> str:
    direction = sb["voice"].get("direction", "").strip()
    line = shot["narration"]["text"].strip()
    return f"{direction}\n\n{line}" if direction else line


def narration_key(sb: dict, shot: dict) -> str:
    v = sb["voice"]
    return content_key("tts-v1", v["model"], v["voice_name"], tts_text(sb, shot))


def narration_path(sb: dict, shot: dict) -> Path | None:
    if not shot.get("narration") or not shot["narration"]["text"].strip():
        return None
    return out_path("narration", shot["id"], narration_key(sb, shot), "wav")


def wav_duration(p: Path) -> float:
    with wave.open(str(p), "rb") as w:
        return w.getnframes() / w.getframerate()


def run_narration(sb: dict, only: str | None = None, force: bool = False) -> Summary:
    summ = Summary("narration")
    v = sb["voice"]
    model = None
    for shot in select_shots(sb, only):
        p = narration_path(sb, shot)
        if p is None:
            continue
        if p.exists() and not force:
            summ.cached.append(shot["id"])
        else:
            try:
                model = model or providers.resolve_model(v["model"], "tts")
                text = tts_text(sb, shot)
                print(f"  narration {shot['id']} ({model}) -> {rel(p)}")
                pcm, rate, _ = logged_call(
                    lambda: providers.gemini_tts(model, text, v["voice_name"]),
                    stage="narration", provider="gemini", model=model, shot_id=shot["id"],
                    params={"voice": v["voice_name"], "text": text, "chars": len(text)},
                    input_key=narration_key(sb, shot),
                    extra=lambda r: {"output": rel(p), "audio_s": round(len(r[0]) / 2 / r[1], 2),
                                     "usage": r[2]})
                with wave.open(str(p), "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(rate)
                    w.writeframes(pcm)
                summ.made.append(shot["id"])
            except Exception as e:
                summ.failed.append((shot["id"], str(e)))
                continue
        # Does the line fit in its shot?
        dur = wav_duration(p)
        room = shot["duration"] - shot["narration"].get("offset", 0.3)
        if dur > room:
            print(f"  ! {shot['id']}: line is {dur:.1f}s but only {room:.1f}s available; "
                  f"lengthen the shot or shorten the line")
    summ.report()
    return summ
