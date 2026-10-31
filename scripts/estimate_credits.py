#!/usr/bin/env python3
"""估算 WorkBuddy 积分消耗。

两件事：
1. 倍率从本机客户端配置自动读取（~/.workbuddy/local_storage/*.info 里的 models[].credits），
   这是唯一可靠来源，比任何二手文章都准。
2. 官方没公开「1.0x 倍率 = 多少积分/百万 token」，所以要一个锚点：
   拿某次实测的积分 ÷ (倍率 × 用量) 反推系数 K，再推算全表。

用法：
    # 只看相对结构（不需要锚点）
    python3 estimate_credits.py --raw raw.jsonl --outdir out

    # 用一次实测标定，推算绝对积分
    python3 estimate_credits.py --raw raw.jsonl --outdir out \
        --anchor-model kimi-k3.1-highspeed --anchor-credits 2000
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_STORAGE = Path.home() / ".workbuddy" / "local_storage"

# 这些模型在非高峰时段倍率减半
NIGHT_HALF = {"deepseek-v4.1-flash", "deepseek-v4-pro"}
PEAK_WINDOWS_UTC = [(1, 4), (6, 10)]

# trace 里的模型名与客户端模型 id 不一致时在此映射（左：trace 名，右：客户端 id）
ALIASES = {
    "kimi-k3.1-highspeed": "kimi-k3.1-pro-highspeed",
}


def lookup_rate(rates: dict[str, float], model: str) -> tuple[float | None, str]:
    """查倍率，支持别名。返回 (倍率, 实际命中的 id)。"""
    if model in rates:
        return rates[model], model
    alias = ALIASES.get(model)
    if alias and alias in rates:
        return rates[alias], alias
    return None, model


def in_peak(dt_utc: datetime) -> bool:
    if dt_utc.weekday() >= 5:
        return False
    h = dt_utc.hour
    return any(a <= h < b for a, b in PEAK_WINDOWS_UTC)


def parse_credits(value: str) -> float | None:
    """客户端里倍率写成 'x0.11' / 'x0.00' 这样的字符串。"""
    if not isinstance(value, str):
        return None
    m = re.fullmatch(r"x\s*([\d.]+)", value.strip())
    return float(m.group(1)) if m else None


def load_multipliers(storage: Path) -> tuple[dict[str, float], str | None]:
    """从客户端模型配置里读真实倍率，返回 (倍率表, 来源文件)。"""
    best: dict[str, float] = {}
    best_file = None
    for fp in sorted(storage.glob("entry_*.info")):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        blocks = data if isinstance(data, list) else [data]
        for blk in blocks:
            if not isinstance(blk, dict):
                continue
            inner = blk.get("data")
            if not isinstance(inner, dict):
                continue
            models = inner.get("models")
            if not isinstance(models, list):
                continue
            table = {}
            for m in models:
                if not isinstance(m, dict):
                    continue
                mid = m.get("id")
                rate = parse_credits(m.get("credits"))
                if mid and rate is not None:
                    table[mid] = rate
            if len(table) > len(best):
                best = table
                best_file = str(fp)
    return best, best_file


def main() -> None:
    ap = argparse.ArgumentParser(description="估算积分消耗")
    ap.add_argument("--raw", default="raw.jsonl")
    ap.add_argument("--outdir", default="out")
    ap.add_argument("--storage", default=str(DEFAULT_STORAGE), help="客户端配置目录")
    ap.add_argument("--multipliers", default=None, help="手动指定倍率 JSON（覆盖自动读取）")
    ap.add_argument("--anchor-model", default=None, help="用于标定的模型 id")
    ap.add_argument("--anchor-credits", type=float, default=None, help="该模型实测消耗的积分")
    args = ap.parse_args()

    rows = [json.loads(l) for l in Path(args.raw).open(encoding="utf-8")]

    # 倍率来源优先级：手动指定 > 客户端配置
    if args.multipliers:
        rates = json.loads(Path(args.multipliers).read_text(encoding="utf-8"))
        src = args.multipliers
    else:
        rates, src = load_multipliers(Path(args.storage).expanduser())

    agg = defaultdict(lambda: {"tokens": 0, "tok_peak": 0, "tok_off": 0,
                               "weighted": 0.0, "traces": 0})

    for r in rows:
        dt = datetime.strptime(r["startedAt"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        peak = in_peak(dt)
        tok = r["input"] + r["output"]
        for m in r["models"]:
            a = agg[m]
            a["traces"] += 1
            a["tokens"] += tok
            a["tok_peak" if peak else "tok_off"] += tok
            rate, _ = lookup_rate(rates, m)
            if rate is None:
                continue
            if m in NIGHT_HALF and not peak:
                rate = rate / 2
            a["weighted"] += tok * rate

    # 标定系数 K
    k = None
    if args.anchor_model and args.anchor_credits:
        a = agg.get(args.anchor_model)
        if not a or a["weighted"] <= 0:
            raise SystemExit(f"锚点模型 {args.anchor_model} 没有可用数据")
        k = args.anchor_credits / (a["weighted"] / 1e6)

    known = sum(a["weighted"] for a in agg.values())
    models = []
    total_credits = 0
    for m, a in sorted(agg.items(), key=lambda x: -x[1]["weighted"]):
        rate, hit_id = lookup_rate(rates, m)
        credits = round(a["weighted"] / 1e6 * k) if k else None
        if credits:
            total_credits += credits
        models.append({
            "model": m,
            "rate": rate,
            "rate_from": hit_id if hit_id != m else None,
            "night_halved": m in NIGHT_HALF,
            "tokens": a["tokens"],
            "tokens_peak": a["tok_peak"],
            "tokens_offpeak": a["tok_off"],
            "weighted_tokens": round(a["weighted"]),
            "share_pct": round(a["weighted"] / known * 100, 2) if known else 0,
            "credits": credits,
            "traces": a["traces"],
        })

    payload = {
        "multiplier_source": src,
        "models_without_rate": [m for m in agg if lookup_rate(rates, m)[0] is None],
        "anchor": {
            "model": args.anchor_model,
            "credits": args.anchor_credits,
            "k_per_multiplier_per_mtoken": round(k, 2) if k else None,
            "note": "K = 1.0x 倍率下每百万 token 的积分；由单次实测反推，存在抽样误差",
        } if k else None,
        "total_credits": total_credits if k else None,
        "models": models,
    }

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "credits.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"倍率来源: {src or '未找到'}")
    print(f"已知倍率模型: {sum(1 for m in agg if m in rates)}/{len(agg)}")
    if k:
        print(f"标定 K = {k:.2f} 积分/(倍率·百万 token)　合计 {total_credits:,} 积分")
    else:
        print("未提供锚点，只输出相对结构（加权 token 与占比）")
    for x in models:
        rate = f"{x['rate']:.2f}" if x["rate"] is not None else "未知"
        cr = f"{x['credits']:,}" if x["credits"] else "—"
        print(f"  {x['model']:24s} {rate:>5s}  {x['share_pct']:5.1f}%  积分 {cr}")
    if payload["models_without_rate"]:
        print(f"无倍率: {', '.join(payload['models_without_rate'])}")


if __name__ == "__main__":
    main()
