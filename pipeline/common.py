"""Shared plumbing for every stage: paths, secrets, storyboard validation,
content-hash caching, retries and the runs.jsonl call log."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs"
RUNS = ROOT / "runs.jsonl"
SCHEMA = ROOT / "schema" / "storyboard.schema.json"
DEFAULT_STORYBOARD = ROOT / "storyboard.json"
RUN_ID = _dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]


# --------------------------------------------------------------------------- secrets
def load_env() -> None:
    """Minimal .env loader (no dependency). Real environment variables win."""
    p = ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def require_env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise ConfigError(f"Missing {name}. Copy .env.example to .env and fill it in.")
    return v


# --------------------------------------------------------------------------- storyboard
class StoryboardError(Exception):
    pass


def load_storyboard(path: str | Path | None = None) -> dict:
    path = Path(path or DEFAULT_STORYBOARD)
    try:
        sb = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise StoryboardError(f"{path.name} is not valid JSON: {e}") from e
    warnings = validate_storyboard(sb)
    for w in warnings:
        print(f"  ! {w}")
    return sb


def validate_storyboard(sb: dict) -> list[str]:
    """Schema validation plus cross-field checks. Raises on errors, returns warnings."""
    import jsonschema

    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(sb), key=lambda e: list(e.absolute_path))
    msgs = [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in errors]

    warnings: list[str] = []
    if not msgs:
        ids = [s["id"] for s in sb["shots"]]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            msgs.append(f"duplicate shot ids: {sorted(dupes)}")
        chars = {c["id"]: c for c in sb["characters"]}
        for s in sb["shots"]:
            for c in s.get("characters", []):
                if c not in chars:
                    msgs.append(f"shot {s['id']}: unknown character '{c}'")
            if s["motion"]["engine"] == "wan" and not s["motion"].get("prompt"):
                msgs.append(f"shot {s['id']}: engine 'wan' needs motion.prompt")
            n = s.get("narration")
            if n and n.get("machine_translated"):
                warnings.append(f"shot {s['id']}: narration is machine-translated and not yet reviewed")
        for c in sb["characters"]:
            if c["real_person"] and not c.get("consent_ref"):
                msgs.append(f"character '{c['id']}' is a real person but has no consent_ref (see ETHICS.md)")
        m = sb["music"]
        if m.get("chosen"):
            n_cand = m.get("candidates", 3)
            valid = {f"m{i + 1}" for i in range(n_cand)}
            if m["chosen"] not in valid:
                msgs.append(f"music.chosen '{m['chosen']}' is not one of {sorted(valid)}")
        total = film_duration(sb)
        if not 60 <= total <= 90:
            warnings.append(f"film length is {total:.1f}s (target 60-90s incl. end card)")
    if msgs:
        raise StoryboardError("storyboard.json failed validation:\n  - " + "\n  - ".join(msgs))
    return warnings


def film_duration(sb: dict) -> float:
    return sum(s["duration"] for s in sb["shots"]) + sb["end_card"]["duration"]


def timeline(sb: dict) -> list[tuple[dict, float]]:
    """[(shot, start_seconds)]"""
    t, out = 0.0, []
    for s in sb["shots"]:
        out.append((s, t))
        t += s["duration"]
    return out


def select_shots(sb: dict, only: str | None) -> list[dict]:
    if not only:
        return sb["shots"]
    wanted = {x.strip() for x in only.split(",") if x.strip()}
    unknown = wanted - {s["id"] for s in sb["shots"]}
    if unknown:
        raise StoryboardError(f"--only: unknown shot ids {sorted(unknown)}")
    return [s for s in sb["shots"] if s["id"] in wanted]


def characters_in(sb: dict, shot: dict) -> list[dict]:
    chars = {c["id"]: c for c in sb["characters"]}
    return [chars[c] for c in shot.get("characters", [])]


# --------------------------------------------------------------------------- hashing / cache
_DIGEST_CACHE_FILE = OUT / ".digests.json"
_digest_cache: dict | None = None


def file_digest(path: Path) -> str:
    """sha256 of a file, memoised on (size, mtime) so big videos are hashed once."""
    global _digest_cache
    path = Path(path)
    if _digest_cache is None:
        try:
            _digest_cache = json.loads(_DIGEST_CACHE_FILE.read_text())
        except Exception:
            _digest_cache = {}
    st = path.stat()
    key = str(path.resolve())
    sig = f"{st.st_size}:{st.st_mtime_ns}"
    hit = _digest_cache.get(key)
    if hit and hit[0] == sig:
        return hit[1]
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    _digest_cache[key] = [sig, h.hexdigest()]
    _DIGEST_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _DIGEST_CACHE_FILE.write_text(json.dumps(_digest_cache))
    return h.hexdigest()


def content_key(*parts: Any) -> str:
    """Deterministic 12-char key over JSON-able parts, bytes and file paths."""
    h = hashlib.sha256()
    for p in parts:
        if isinstance(p, (bytes, bytearray)):
            h.update(p)
        elif isinstance(p, Path):
            h.update(file_digest(p).encode())
        else:
            h.update(json.dumps(p, sort_keys=True, ensure_ascii=False,
                                default=lambda o: "file:" + file_digest(o) if isinstance(o, Path) else str(o)
                                ).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:12]


def out_path(stage: str, name: str, key: str, ext: str) -> Path:
    d = OUT / stage
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{name}-{key}.{ext}"


def rel(p: Path) -> str:
    try:
        return Path(p).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(p)


# --------------------------------------------------------------------------- logging + retries
class ConfigError(Exception):
    """Local misconfiguration (e.g. a missing key): not a model call, never retried or logged."""


class ProviderError(Exception):
    def __init__(self, msg: str, retryable: bool = False, retry_after: float | None = None, status: int | None = None):
        super().__init__(msg)
        self.retryable = retryable
        self.retry_after = retry_after
        self.status = status


def log_call(record: dict) -> None:
    rec = {"ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"), "run_id": RUN_ID, **record}
    with RUNS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _short(v: Any, n: int = 400) -> Any:
    if isinstance(v, str) and len(v) > n:
        return v[:n] + f"...(+{len(v) - n} chars)"
    return v


def logged_call(
    fn: Callable[[], Any],
    *,
    stage: str,
    provider: str,
    model: str,
    params: dict,
    shot_id: str | None = None,
    seed: int | None = None,
    input_key: str | None = None,
    attempts: int = 5,
    cost_usd: float = 0.0,
    extra: Callable[[Any], dict] | None = None,
) -> Any:
    """Run one model call with retries; every attempt is a line in runs.jsonl."""
    safe_params = {k: _short(v) for k, v in params.items()}
    for attempt in range(1, attempts + 1):
        t0 = time.perf_counter()
        base = dict(stage=stage, shot_id=shot_id, provider=provider, model=model, params=safe_params,
                    seed=seed, input_key=input_key, attempt=attempt)
        try:
            result = fn()
        except ConfigError:
            raise
        except ProviderError as e:
            latency = round(time.perf_counter() - t0, 3)
            final = (not e.retryable) or attempt == attempts
            log_call({**base, "status": "error" if final else "retry", "latency_s": latency,
                      "http_status": e.status, "error": _short(str(e), 600), "cost_usd": 0.0})
            if final:
                raise
            wait = e.retry_after if e.retry_after is not None else min(60, 2 ** attempt) + random.uniform(0, 1)
            print(f"    retry {attempt}/{attempts - 1} in {wait:.0f}s ({_short(str(e), 120)})")
            time.sleep(wait)
            continue
        except Exception as e:  # network errors etc.
            latency = round(time.perf_counter() - t0, 3)
            final = attempt == attempts
            log_call({**base, "status": "error" if final else "retry", "latency_s": latency,
                      "error": _short(f"{type(e).__name__}: {e}", 600), "cost_usd": 0.0})
            if final:
                raise
            time.sleep(min(60, 2 ** attempt) + random.uniform(0, 1))
            continue
        latency = round(time.perf_counter() - t0, 3)
        rec = {**base, "status": "ok", "latency_s": latency, "cost_usd": cost_usd}
        if extra:
            try:
                rec.update(extra(result))
            except Exception:
                pass
        log_call(rec)
        return result
    raise RuntimeError("unreachable")


# --------------------------------------------------------------------------- ffmpeg
_FFMPEG: str | None = None


def ffmpeg_bin() -> str:
    global _FFMPEG
    if _FFMPEG:
        return _FFMPEG
    exe = shutil.which("ffmpeg")
    if not exe:
        try:
            import imageio_ffmpeg

            exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as e:  # pragma: no cover
            raise RuntimeError("ffmpeg not found: install it or `pip install imageio-ffmpeg`") from e
    _FFMPEG = exe
    return exe


def run_ffmpeg(args: list[str], cwd: Path | None = None, quiet: bool = True) -> None:
    cmd = [ffmpeg_bin(), "-hide_banner", "-y", "-loglevel", "error" if quiet else "info", *map(str, args)]
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({r.returncode}):\n{' '.join(cmd)[:2000]}\n{r.stderr[-3000:]}")


def media_duration(path: Path) -> float:
    """Duration in seconds by parsing ffmpeg's header dump (imageio-ffmpeg has no ffprobe)."""
    r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", r.stderr)
    if not m:
        raise RuntimeError(f"could not read duration of {path}")
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


# --------------------------------------------------------------------------- fonts
FONT_CANDIDATES = [
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial.ttf",
]


def load_font(size: int, preferred: str | None = None):
    from PIL import ImageFont

    for cand in ([preferred] if preferred else []) + FONT_CANDIDATES:
        if cand and Path(cand).exists():
            return ImageFont.truetype(cand, size)
    return ImageFont.load_default(size=size)


class Summary:
    """Collects per-shot outcomes so a stage keeps going and reports at the end."""

    def __init__(self, stage: str):
        self.stage = stage
        self.made: list[str] = []
        self.cached: list[str] = []
        self.failed: list[tuple[str, str]] = []

    def report(self) -> None:
        print(f"[{self.stage}] generated {len(self.made)}, cached {len(self.cached)}, failed {len(self.failed)}")
        for sid, err in self.failed:
            print(f"    x {sid}: {err[:200]}")
