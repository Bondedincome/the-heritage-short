"""Stage 1: script.

  draft      story/notes.md + DESIGN.md + the JSON Schema -> storyboard.draft.json (never overwrites
             storyboard.json; a human merges). Invalid drafts are sent back with the validator's errors.
  translate  fill narration.text from narration.english for lines that are empty, mark them
             machine_translated=true until a fluent speaker reviews them.
"""
from __future__ import annotations

import json

from . import providers
from .common import ROOT, SCHEMA, ProviderError, StoryboardError, logged_call, validate_storyboard


def text_call(sb: dict | None, prompt: str, params: dict, **kw) -> str:
    """Gemini text with a fallback chain: configured/newest flash -> newest flash-lite.
    Flash returned 503 'high demand' for minutes at a time on the free tier (FAILURES.md F02)."""
    configured = (sb or {}).get("models", {}).get("text", "auto")
    chain = [providers.resolve_model(configured, "text")]
    try:
        chain.append(providers.resolve_model("auto", "text-lite"))
    except ProviderError:
        pass
    last = None
    for model in dict.fromkeys(chain):
        try:
            text, _ = logged_call(lambda: providers.gemini_text(model, prompt, **kw), stage="script",
                                  provider="gemini", model=model, params=params, attempts=3,
                                  extra=lambda r: {"usage": r[1]})
            return text
        except ProviderError as e:
            if e.status not in (429, 500, 503, None):
                raise
            print(f"  {model} unavailable ({e.status}); falling back")
            last = e
    raise last

SYSTEM = ("You are a documentary story editor. You write short, concrete, visual storyboards for a 60-90 second "
          "heritage film. Every shot must have a reason. Describe visual qualities rather than naming living artists. "
          "Return only JSON that validates against the given JSON Schema.")


def draft(sb: dict | None, tries: int = 3) -> None:
    notes = (ROOT / "story" / "notes.md").read_text(encoding="utf-8")
    design = (ROOT / "DESIGN.md").read_text(encoding="utf-8")
    schema = SCHEMA.read_text(encoding="utf-8")
    prompt = (f"JSON Schema:\n{schema}\n\nDESIGN.md (audience and intent):\n{design[:6000]}\n\n"
              f"Story notes:\n{notes}\n\nWrite storyboard.json with 8-12 shots totalling 55-82 seconds "
              "(the end card adds the rest). Use shot ids s01, s02, ... Keep narration lines short enough to "
              "speak in the shot duration (about 2.2 words per second). Put the narration in the target "
              "language in narration.text and the English subtitle in narration.english.")
    for attempt in range(1, tries + 1):
        text = text_call(sb, prompt, {"task": "draft", "attempt": attempt, "prompt_chars": len(prompt)},
                         system=SYSTEM, json_mode=True, temperature=0.7)
        try:
            candidate = json.loads(text)
            validate_storyboard(candidate)
        except (json.JSONDecodeError, StoryboardError) as e:
            print(f"  draft {attempt} invalid, feeding errors back: {str(e)[:300]}")
            prompt += f"\n\nYour previous answer was invalid:\n{str(e)[:3000]}\nReturn a corrected full JSON."
            continue
        out = ROOT / "storyboard.draft.json"
        out.write_text(json.dumps(candidate, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[script] valid draft -> {out.name}. Review it, then merge into storyboard.json by hand.")
        return
    raise SystemExit("[script] no valid draft after retries; see runs.jsonl")


def translate(sb: dict, path, force: bool = False) -> None:
    lang = sb["language"]["name"]
    todo = [s for s in sb["shots"] if s.get("narration") and (force or not s["narration"]["text"].strip())]
    if not todo:
        print("[script] nothing to translate")
        return
    payload = {s["id"]: s["narration"]["english"] for s in todo}
    prompt = (f"Translate each line into natural spoken {lang}, as an elder would tell a family story. "
              f"Use the native script. Keep any phrase already written in {lang} exactly as written. "
              f"Keep each line about the same spoken length. Return a JSON object "
              f"with the same keys.\n\n{json.dumps(payload, ensure_ascii=False, indent=2)}")
    text = text_call(sb, prompt, {"task": "translate", "language": lang, "lines": len(todo), "prompt": prompt},
                     json_mode=True, temperature=0.3)
    out = json.loads(text)
    for s in todo:
        if s["id"] in out:
            s["narration"]["text"] = out[s["id"]]
            s["narration"]["machine_translated"] = True
    validate_storyboard(sb)
    path.write_text(json.dumps(sb, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[script] translated {len(out)} line(s) into {lang}; marked machine_translated=true for review")
