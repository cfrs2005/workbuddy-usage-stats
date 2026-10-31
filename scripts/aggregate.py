#!/usr/bin/env python3
"""按模型和日期聚合用量，输出汇总与交叉矩阵。

归日口径：trace 的 startedAt 是 UTC，默认按 UTC+8 归日（可用 --tz 改）。
口径选择会影响"每天用了多少"，写报告时要注明。

用法：
    python3 aggregate.py --raw raw.jsonl --outdir out
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


def parse_dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def main() -> None:
    ap = argparse.ArgumentParser(description="按模型/日期聚合用量")
    ap.add_argument("--raw", default="raw.jsonl")
    ap.add_argument("--outdir", default="out")
    ap.add_argument("--tz", type=float, default=8.0, help="归日时区偏移，默认 8 = UTC+8")
    args = ap.parse_args()

    rows = [json.loads(l) for l in Path(args.raw).open(encoding="utf-8")]
    tz = timezone(timedelta(hours=args.tz))

    models = defaultdict(lambda: {
        "traces": 0, "calls": 0, "input": 0, "output": 0, "cached": 0,
        "days": set(), "first": None, "last": None,
    })
    matrix = defaultdict(lambda: defaultdict(int))
    day_calls = defaultdict(int)

    for r in rows:
        dt = parse_dt(r["startedAt"]).astimezone(tz)
        day = dt.strftime("%Y-%m-%d")
        total = r["input"] + r["output"]
        day_calls[day] += r["calls"]

        for m in r["models"]:
            a = models[m]
            a["traces"] += 1
            a["calls"] += r["calls"]
            a["input"] += r["input"]
            a["output"] += r["output"]
            a["cached"] += r["cached"]
            a["days"].add(day)
            if a["first"] is None or day < a["first"]:
                a["first"] = day
            if a["last"] is None or day > a["last"]:
                a["last"] = day
            matrix[day][m] += total

    grand = sum(a["input"] + a["output"] for a in models.values())
    summary = []
    for m, a in sorted(models.items(), key=lambda x: -(x[1]["input"] + x[1]["output"])):
        tot = a["input"] + a["output"]
        summary.append({
            "model": m,
            "traces": a["traces"],
            "calls": a["calls"],
            "active_days": len(a["days"]),
            "first": a["first"],
            "last": a["last"],
            "input": a["input"],
            "output": a["output"],
            "cached": a["cached"],
            "total": tot,
            "share_pct": round(tot / grand * 100, 2) if grand else 0,
            "cache_rate_pct": round(a["cached"] / a["input"] * 100, 2) if a["input"] else 0,
            "avg_in_per_call": round(a["input"] / a["calls"]) if a["calls"] else 0,
            "avg_out_per_call": round(a["output"] / a["calls"]) if a["calls"] else 0,
        })

    days = sorted(matrix)
    payload = {
        "timezone_offset_hours": args.tz,
        "trace_count": len(rows),
        "model_count": len(summary),
        "day_count": len(days),
        "first_day": days[0] if days else None,
        "last_day": days[-1] if days else None,
        "total_calls": sum(a["calls"] for a in models.values()),
        "grand_total_tokens": grand,
        "total_input": sum(a["input"] for a in models.values()),
        "total_output": sum(a["output"] for a in models.values()),
        "total_cached": sum(a["cached"] for a in models.values()),
        "models": summary,
        "daily": [
            {
                "date": d,
                "calls": day_calls[d],
                "total": sum(matrix[d].values()),
                "by_model": dict(matrix[d]),
            }
            for d in days
        ],
    }

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    with (outdir / "models.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0].keys()) if summary else ["model"])
        w.writeheader()
        w.writerows(summary)

    with (outdir / "daily.csv").open("w", newline="", encoding="utf-8-sig") as f:
        cols = ["date"] + [s["model"] for s in summary] + ["total"]
        w = csv.writer(f)
        w.writerow(cols)
        for d in days:
            w.writerow([d] + [matrix[d].get(s["model"], 0) for s in summary]
                       + [sum(matrix[d].values())])

    print(f"{payload['model_count']} models, {payload['day_count']} days, "
          f"{payload['trace_count']} traces, {grand:,} tokens")
    for s in summary:
        print(f"  {s['model']:24s} {s['share_pct']:5.1f}%  {s['total']:>13,}")


if __name__ == "__main__":
    main()
