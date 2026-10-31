#!/usr/bin/env python3
"""按公开价目估算用量成本（USD / CNY），并区分峰谷。

关键点：DeepSeek 是唯一带分时定价的模型，必须逐条 trace 判断落在高峰还是闲时，
不能用全天平均价，否则高峰任务会被低估一半。

用法：
    python3 estimate_cost.py --raw raw.jsonl --pricing ../config/pricing.json --outdir out
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def in_peak(dt_utc: datetime, cfg: dict) -> bool:
    """按模型配置判断该时刻是否处于高峰窗口。"""
    windows = cfg.get("peak_windows_utc")
    if not windows:
        return False
    if cfg.get("peak_weekdays_only", True) and dt_utc.weekday() >= 5:
        return False
    h = dt_utc.hour
    return any(start <= h < end for start, end in windows)


def main() -> None:
    ap = argparse.ArgumentParser(description="估算 API 等价成本")
    ap.add_argument("--raw", default="raw.jsonl")
    ap.add_argument("--pricing", default="../config/pricing.json")
    ap.add_argument("--outdir", default="out")
    args = ap.parse_args()

    pricing = json.loads(Path(args.pricing).read_text(encoding="utf-8"))
    fx = pricing.get("fx_usd_cny", 7.1)
    table = pricing["models"]
    rows = [json.loads(l) for l in Path(args.raw).open(encoding="utf-8")]

    agg = defaultdict(lambda: {
        "hit_tokens": 0, "miss_tokens": 0, "out_tokens": 0, "traces": 0,
        "traces_peak": 0, "cost": 0.0, "cost_peak": 0.0, "cost_offpeak": 0.0,
    })
    unpriced = set()

    for r in rows:
        dt = datetime.strptime(r["startedAt"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        for m in r["models"]:
            cfg = table.get(m)
            a = agg[m]
            a["traces"] += 1
            hit = r["cached"]
            miss = max(r["input"] - r["cached"], 0)
            out = r["output"]
            a["hit_tokens"] += hit
            a["miss_tokens"] += miss
            a["out_tokens"] += out

            if not cfg or cfg.get("cache_miss") is None:
                unpriced.add(m)
                continue

            peak = in_peak(dt, cfg)
            mult = cfg.get("peak_multiplier", 1.0) if peak else 1.0
            if peak:
                a["traces_peak"] += 1
            c = (hit / 1e6 * cfg["cache_hit"]
                 + miss / 1e6 * cfg["cache_miss"]
                 + out / 1e6 * cfg["output"]) * mult
            a["cost"] += c
            a["cost_peak" if peak else "cost_offpeak"] += c

    models = []
    for m, a in sorted(agg.items(), key=lambda x: -x[1]["cost"]):
        cfg = table.get(m) or {}
        models.append({
            "model": m,
            "traces": a["traces"],
            "traces_in_peak_window": a["traces_peak"],
            "cache_hit_tokens": a["hit_tokens"],
            "cache_miss_tokens": a["miss_tokens"],
            "output_tokens": a["out_tokens"],
            "cost_usd": round(a["cost"], 4),
            "cost_cny": round(a["cost"] * fx, 2),
            "cost_peak_usd": round(a["cost_peak"], 4),
            "cost_offpeak_usd": round(a["cost_offpeak"], 4),
            "priced": cfg.get("cache_miss") is not None,
            "source": cfg.get("source", ""),
            "verified_at": cfg.get("verified_at", ""),
        })

    total = sum(x["cost_usd"] for x in models)
    payload = {
        "fx_usd_cny": fx,
        "total_usd": round(total, 4),
        "total_cny": round(total * fx, 2),
        "total_peak_usd": round(sum(x["cost_peak_usd"] for x in models), 4),
        "total_offpeak_usd": round(sum(x["cost_offpeak_usd"] for x in models), 4),
        "unpriced_models": sorted(unpriced),
        "models": models,
    }

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "cost.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"total: ${total:.4f}  (¥{total * fx:.2f})   汇率 {fx}")
    for x in models:
        flag = "" if x["priced"] else "  [无公开价]"
        print(f"  {x['model']:24s} ${x['cost_usd']:>9.4f}  ¥{x['cost_cny']:>8.2f}{flag}")
    if unpriced:
        print(f"未计价模型: {', '.join(sorted(unpriced))}")


if __name__ == "__main__":
    main()
