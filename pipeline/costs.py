"""Summarise runs.jsonl into the tables COSTS.md needs.

List prices live in pipeline/prices.json. Fill them in from each provider's public
pricing page on the day you check, with the date, so the equivalents are honest."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from .common import RUNS

PRICES = Path(__file__).with_name("prices.json")


def _price(prices: dict, rec: dict) -> float | None:
    p = prices.get(rec.get("model", ""), {})
    if not p:
        return None
    if p.get("per_call") is not None:
        return p["per_call"]
    if p.get("per_gpu_hour") is not None and rec.get("gpu_seconds"):
        return p["per_gpu_hour"] * rec["gpu_seconds"] / 3600
    if p.get("per_1k_chars") is not None:
        return p["per_1k_chars"] * rec.get("params", {}).get("chars", 0) / 1000
    if p.get("per_audio_second") is not None and rec.get("audio_s"):
        return p["per_audio_second"] * rec["audio_s"]
    return None


def report() -> str:
    if not RUNS.exists():
        return "runs.jsonl is empty"
    prices = {k: v for k, v in json.loads(PRICES.read_text()).items() if not k.startswith("_")} if PRICES.exists() else {}
    rows = defaultdict(lambda: {"calls": 0, "ok": 0, "retry": 0, "error": 0, "lat": [], "gpu": 0.0, "list": 0.0,
                                "priced": 0})
    for line in RUNS.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        k = (r.get("stage"), r.get("model"))
        row = rows[k]
        row["calls"] += 1
        row[r.get("status", "ok") if r.get("status") in ("ok", "retry", "error") else "ok"] += 1
        if r.get("status") == "ok" and r.get("latency_s") is not None:
            row["lat"].append(r["latency_s"])
        row["gpu"] += r.get("gpu_seconds") or 0
        if r.get("status") == "ok":
            price = _price(prices, r)
            if price is not None:
                row["list"] += price
                row["priced"] += 1
    out = ["| stage | model | calls | ok | retry | error | mean latency (s) | p95 (s) | GPU min | list-price eq. (USD) |",
           "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for (stage, model), r in sorted(rows.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        lat = sorted(r["lat"])
        mean = sum(lat) / len(lat) if lat else 0
        p95 = lat[min(len(lat) - 1, int(0.95 * len(lat)))] if lat else 0
        lp = f"{r['list']:.4f}" if r["priced"] else "n/a"
        out.append(f"| {stage} | {model} | {r['calls']} | {r['ok']} | {r['retry']} | {r['error']} | {mean:.1f} | "
                   f"{p95:.1f} | {r['gpu'] / 60:.1f} | {lp} |")
    return "\n".join(out)
