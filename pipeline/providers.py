"""Thin HTTP clients for the hosted providers. No SDKs: one function per call shape,
every error mapped to ProviderError(retryable=...) so logged_call can decide."""
from __future__ import annotations

import base64
import json
import os
import re

import requests

from .common import ProviderError, require_env

try:  # Verify TLS against the OS certificate store (Windows antivirus/proxies re-sign HTTPS;
    import truststore  # certifi's bundle then fails with CERTIFICATE_VERIFY_FAILED). See FAILURES.md F01.

    truststore.inject_into_ssl()
except ImportError:
    pass

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
CF_BASE = "https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{model}"
TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "300"))


def _raise_for(r: requests.Response, provider: str) -> None:
    if r.status_code < 400:
        return
    text = r.text[:1500]
    retry_after = None
    if "Retry-After" in r.headers:
        try:
            retry_after = float(r.headers["Retry-After"])
        except ValueError:
            pass
    m = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', text)  # Gemini puts it in the body
    if m:
        retry_after = float(m.group(1)) + 1
    daily = bool(re.search(r"PerDay|per day|daily", text, re.I))
    retryable = (r.status_code == 429 and not daily) or r.status_code >= 500
    hint = " (daily quota exhausted: wait for reset or switch model)" if daily else ""
    raise ProviderError(f"{provider} HTTP {r.status_code}{hint}: {text}", retryable=retryable,
                        retry_after=retry_after, status=r.status_code)


def _post(url: str, provider: str, **kw) -> requests.Response:
    try:
        r = requests.post(url, timeout=TIMEOUT, **kw)
    except (requests.ConnectionError, requests.Timeout) as e:
        raise ProviderError(f"{provider} network error: {e}", retryable=True) from e
    _raise_for(r, provider)
    return r


# --------------------------------------------------------------------------- Gemini
def _gemini_headers() -> dict:
    return {"x-goog-api-key": require_env("GEMINI_API_KEY"), "Content-Type": "application/json"}


_model_list: list[str] | None = None


def gemini_list_models() -> list[str]:
    global _model_list
    if _model_list is None:
        r = requests.get(f"{GEMINI_BASE}/models?pageSize=1000", headers=_gemini_headers(), timeout=60)
        _raise_for(r, "gemini")
        _model_list = [m["name"].removeprefix("models/") for m in r.json().get("models", [])]
    return _model_list


def _version(name: str) -> tuple:
    m = re.search(r"gemini-(\d+)(?:\.(\d+))?", name)
    major, minor = (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)
    return (major, minor, "lite" not in name, "preview" not in name and "exp" not in name)


def resolve_model(configured: str, kind: str) -> str:
    """'auto' picks the newest flash model of a kind from the live model list.
    Free-tier model ids change every few months; this keeps the pipeline running."""
    if configured and configured != "auto":
        return configured
    names = gemini_list_models()
    if kind == "tts":
        pool = [n for n in names if "tts" in n and "flash" in n]
    elif kind == "text-lite":
        bad = ("tts", "image", "live", "embedding", "audio")
        pool = [n for n in names if n.startswith("gemini") and "flash-lite" in n and not any(b in n for b in bad)]
    else:
        bad = ("tts", "image", "live", "embedding", "audio", "lite", "thinking", "learnlm", "robotics", "computer")
        pool = [n for n in names if n.startswith("gemini") and "flash" in n and not any(b in n for b in bad)]
    if not pool:
        raise ProviderError(f"no Gemini {kind} model found in model list", retryable=False)
    return sorted(pool, key=_version)[-1]


def gemini_generate(model: str, body: dict) -> dict:
    r = _post(f"{GEMINI_BASE}/models/{model}:generateContent", "gemini", headers=_gemini_headers(), json=body)
    data = r.json()
    if not data.get("candidates"):
        raise ProviderError(f"gemini returned no candidates: {json.dumps(data)[:800]}", retryable=True)
    return data


def gemini_text(model: str, prompt: str, *, system: str | None = None, json_mode: bool = False,
                temperature: float | None = None) -> tuple[str, dict]:
    body: dict = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
    cfg: dict = {}
    if json_mode:
        cfg["responseMimeType"] = "application/json"
    if temperature is not None:
        cfg["temperature"] = temperature
    if cfg:
        body["generationConfig"] = cfg
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    data = gemini_generate(model, body)
    parts = data["candidates"][0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text:
        raise ProviderError(f"gemini empty text (finishReason={data['candidates'][0].get('finishReason')})",
                            retryable=True)
    return text, data.get("usageMetadata", {})


def gemini_tts(model: str, text: str, voice: str) -> tuple[bytes, int, dict]:
    """Returns (pcm_s16le_mono, sample_rate, usage)."""
    body = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    }
    data = gemini_generate(model, body)
    for p in data["candidates"][0].get("content", {}).get("parts", []):
        inline = p.get("inlineData") or p.get("inline_data")
        if inline and inline.get("data"):
            mime = inline.get("mimeType", "")
            m = re.search(r"rate=(\d+)", mime)
            return base64.b64decode(inline["data"]), int(m.group(1)) if m else 24000, data.get("usageMetadata", {})
    raise ProviderError(f"gemini TTS returned no audio: {json.dumps(data)[:600]}", retryable=True)


# --------------------------------------------------------------------------- Cloudflare Workers AI
def cloudflare_image(model: str, prompt: str, *, width: int, height: int, steps: int, seed: int | None,
                     refs: list[bytes] | None = None) -> bytes:
    url = CF_BASE.format(acct=require_env("CLOUDFLARE_ACCOUNT_ID"), model=model)
    headers = {"Authorization": f"Bearer {require_env('CLOUDFLARE_API_TOKEN')}"}
    if "flux-2" in model:
        # FLUX.2 on Workers AI takes multipart form data and up to 4 reference images.
        fields = [("prompt", (None, prompt)), ("width", (None, str(width))), ("height", (None, str(height))),
                  ("steps", (None, str(steps)))]
        if seed is not None:
            fields.append(("seed", (None, str(seed))))
        for i, img in enumerate((refs or [])[:4]):
            fields.append((f"input_image_{i}", (f"ref{i}.png", img, "image/png")))
        r = _post(url, "cloudflare", headers=headers, files=fields)
    else:
        # FLUX.1 schnell: JSON in, square 1024 JPEG out; prompt + steps (max 8) only.
        r = _post(url, "cloudflare", headers=headers, json={"prompt": prompt, "steps": min(steps, 8)})
    if r.headers.get("content-type", "").startswith("image/"):
        return r.content
    data = r.json()
    img = (data.get("result") or {}).get("image")
    if not img:
        raise ProviderError(f"cloudflare returned no image: {json.dumps(data)[:600]}", retryable=True)
    return base64.b64decode(img)
