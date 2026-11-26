"""Call each provider once, log latency to runs.jsonl, and print what works."""
from __future__ import annotations

import shutil
import wave
from pathlib import Path

from . import providers
from .common import OUT, ffmpeg_bin, logged_call


def run(sb: dict) -> None:
    d = OUT / "smoke"
    d.mkdir(parents=True, exist_ok=True)
    results = []

    def check(name, fn):
        try:
            msg = fn()
            results.append((name, "OK", msg))
        except Exception as e:
            results.append((name, "FAIL", str(e)[:300]))

    def models():
        names = providers.gemini_list_models()
        tts = [n for n in names if "tts" in n]
        return f"{len(names)} models; TTS: {', '.join(tts) or 'none'}"

    def text():
        m = providers.resolve_model(sb.get("models", {}).get("text", "auto"), "text")
        t, _ = logged_call(lambda: providers.gemini_text(m, "Reply with the single word: ready"),
                           stage="smoke", provider="gemini", model=m, params={"prompt": "ready"}, attempts=2)
        return f"{m}: {t.strip()[:40]}"

    def tts():
        m = providers.resolve_model(sb["voice"]["model"], "tts")
        line = next((s["narration"]["text"] for s in sb["shots"] if s.get("narration", {}).get("text")), "Hello.")
        pcm, rate, _ = logged_call(lambda: providers.gemini_tts(m, line[:200], sb["voice"]["voice_name"]),
                                   stage="smoke", provider="gemini", model=m,
                                   params={"voice": sb["voice"]["voice_name"], "text": line[:200]}, attempts=2)
        p = d / "tts.wav"
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm)
        return f"{m}: {len(pcm) / 2 / rate:.1f}s of audio -> outputs/smoke/tts.wav"

    def image():
        m = sb.get("models", {}).get("image", "@cf/black-forest-labs/flux-1-schnell")
        b = logged_call(lambda: providers.cloudflare_image(m, "a clay coffee pot on a wooden table, morning light",
                                                           width=512, height=512, steps=4, seed=1),
                        stage="smoke", provider="cloudflare", model=m, params={"steps": 4}, attempts=2)
        (d / "image.jpg").write_bytes(b)
        return f"{m}: {len(b) // 1024} KB -> outputs/smoke/image.jpg"

    def ffmpeg():
        return ffmpeg_bin()

    def kaggle():
        cfg = Path.home() / ".kaggle" / "kaggle.json"
        cli = shutil.which("kaggle")
        if not cli and not cfg.exists():
            raise RuntimeError("no kaggle CLI or ~/.kaggle/kaggle.json (fine if you use the web notebook UI)")
        return f"cli={'yes' if cli else 'no'}, token={'yes' if cfg.exists() else 'no'}"

    for name, fn in [("gemini models", models), ("gemini text", text), ("gemini tts", tts),
                     ("cloudflare image", image), ("ffmpeg", ffmpeg), ("kaggle", kaggle)]:
        print(f"  checking {name}...")
        check(name, fn)
    print()
    for name, status, msg in results:
        print(f"  {status:4s}  {name:17s} {msg}")
