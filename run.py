#!/usr/bin/env python
"""The Heritage Short: one entry point for every pipeline stage.

    python run.py film          # the whole pipeline, both aspect ratios
    python run.py radio         # narration + placeholder music on black (first user test)
    python run.py keyframes --only s03 --force
    python run.py --help
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from pipeline.common import DEFAULT_STORYBOARD, StoryboardError, load_env, load_storyboard


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--storyboard", default=str(DEFAULT_STORYBOARD), help="path to storyboard.json")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, help_, only=False, force=False):
        p = sub.add_parser(name, help=help_)
        if only:
            p.add_argument("--only", help="comma-separated shot ids, e.g. s03,s07")
        if force:
            p.add_argument("--force", action="store_true", help="ignore the cache and regenerate")
        return p

    add("validate", "validate storyboard.json against the schema")
    add("smoke", "call every provider once and log latency")
    p = add("script", "draft a storyboard or translate narration with Gemini", force=True)
    p.add_argument("action", choices=["draft", "translate"])
    add("refsheet", "generate character reference sheets", force=True)
    add("keyframes", "generate keyframes (needs refsheets for FLUX.2 conditioning)", only=True, force=True)
    p = add("shots", "render moving shots; --bundle/--import for Wan 2.2 on Kaggle", only=True, force=True)
    p.add_argument("--bundle", action="store_true", help="write missing Wan jobs to outputs/kaggle/shots_bundle.zip")
    p.add_argument("--import", dest="import_dir", help="folder of clips downloaded from Kaggle")
    p = add("music", "placeholder score, or --bundle/--import ACE-Step candidates")
    p.add_argument("--bundle", action="store_true")
    p.add_argument("--import", dest="import_dir")
    add("narration", "generate narration lines with Gemini TTS", only=True, force=True)
    p = add("assemble", "edit and export")
    p.add_argument("--aspect", choices=["horizontal", "vertical", "both"], default="both")
    add("radio", "narration + placeholder music on black, with subtitles")
    add("film", "run every stage and export both aspect ratios")
    add("costs", "summarise runs.jsonl for COSTS.md")

    a = ap.parse_args(argv)
    load_env()
    sb_path = Path(a.storyboard)
    only = getattr(a, "only", None)
    force = getattr(a, "force", False)
    t0 = time.perf_counter()

    try:
        if a.cmd == "costs":
            from pipeline import costs
            print(costs.report())
            return 0
        if a.cmd == "script" and a.action == "draft":
            from pipeline import script
            sb = load_storyboard(sb_path) if sb_path.exists() else None
            script.draft(sb)
            return 0

        sb = load_storyboard(sb_path)
        print(f"storyboard OK: {sb['title']!r}, {len(sb['shots'])} shots")

        if a.cmd == "validate":
            return 0
        if a.cmd == "smoke":
            from pipeline import smoke
            smoke.run(sb)
        elif a.cmd == "script":
            from pipeline import script
            script.translate(sb, sb_path, force)
        elif a.cmd == "refsheet":
            from pipeline import keyframes
            keyframes.run_refsheets(sb, force)
        elif a.cmd == "keyframes":
            from pipeline import keyframes
            keyframes.run_keyframes(sb, only, force)
        elif a.cmd == "shots":
            from pipeline import shots
            if a.bundle:
                shots.bundle(sb, only)
            elif a.import_dir:
                shots.import_returns(sb, Path(a.import_dir))
            else:
                shots.run_render(sb, only, force)
        elif a.cmd == "music":
            from pipeline import music
            if a.bundle:
                music.bundle(sb)
            elif a.import_dir:
                music.import_returns(sb, Path(a.import_dir))
            else:
                music.make_placeholder(sb)
                music.status(sb)
        elif a.cmd == "narration":
            from pipeline import narration
            narration.run_narration(sb, only, force)
        elif a.cmd == "assemble":
            from pipeline import assemble
            for asp in (["horizontal", "vertical"] if a.aspect == "both" else [a.aspect]):
                assemble.build(sb, asp)
        elif a.cmd == "radio":
            from pipeline import assemble, narration
            narration.run_narration(sb)
            assemble.build(sb, "horizontal", mode="radio")
        elif a.cmd == "film":
            from pipeline import assemble, keyframes, narration, shots
            print("== refsheet");  keyframes.run_refsheets(sb)
            print("== keyframes"); keyframes.run_keyframes(sb)
            print("== shots");     shots.run_render(sb)
            print("== narration"); narration.run_narration(sb)
            print("== assemble")
            for asp in ("horizontal", "vertical"):
                assemble.build(sb, asp)
    except StoryboardError as e:
        print(e, file=sys.stderr)
        return 2
    print(f"done in {time.perf_counter() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
