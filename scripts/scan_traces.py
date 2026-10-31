#!/usr/bin/env python3
"""扫描 WorkBuddy 本地 trace，抽出每个会话的模型 token 用量。

WorkBuddy 不提供用量面板，唯一的计数在 trace 文件里：
    ~/.workbuddy/traces/<workerPid>/trace_*.json     旧格式，文件头有 modelInfo
    ~/.workbuddy/traces/<workerPid>/trace_*.jsonl    新格式，逐行流式，modelInfo 在 trace_start/end

输出 raw.jsonl，一行一个 trace，供后续聚合使用。

用法：
    python3 scan_traces.py --out raw.jsonl
    python3 scan_traces.py --traces-dir /path/to/traces --out raw.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_TRACES = Path.home() / ".workbuddy" / "traces"


def parse_json_trace(fp: Path) -> dict | None:
    """旧格式：整个文件是一个 JSON，trace.modelInfo 在头部。"""
    try:
        data = json.loads(fp.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    tr = data.get("trace") or {}
    if not tr.get("modelInfo"):
        return None
    return tr


def parse_jsonl_trace(fp: Path) -> dict | None:
    """新格式：逐行 JSON，只认顶层 type=trace_start/trace_end 的 trace 对象。

    注意：trace 内容有时会被转义后塞进某个 span 的字符串字段里，
    那种是不会被 json.loads 出 dict 的，天然被排除，不会重复计数。
    """
    best = None
    try:
        with fp.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if "modelInfo" not in line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue
                if obj.get("type") not in ("trace_start", "trace_end"):
                    continue
                tr = obj.get("trace") or {}
                if tr.get("modelInfo"):
                    best = tr  # trace_end 覆盖 trace_start，拿到最终值
    except Exception:
        return None
    return best


def main() -> None:
    ap = argparse.ArgumentParser(description="扫描 WorkBuddy trace 提取用量")
    ap.add_argument("--traces-dir", default=str(DEFAULT_TRACES))
    ap.add_argument("--out", default="raw.jsonl")
    args = ap.parse_args()

    root = Path(args.traces_dir).expanduser()
    if not root.exists():
        raise SystemExit(f"traces 目录不存在: {root}")

    json_files = sorted(root.glob("*/trace_*.json"))
    jsonl_files = sorted(root.glob("*/trace_*.jsonl"))

    seen: dict[str, dict] = {}
    stats = {"json": 0, "jsonl": 0, "dup": 0, "no_model_info": 0}

    for fp in json_files:
        tr = parse_json_trace(fp)
        if not tr:
            stats["no_model_info"] += 1
            continue
        stats["json"] += 1
        tid = tr.get("traceId") or fp.stem
        if tid in seen:
            stats["dup"] += 1
            continue
        seen[tid] = tr

    for fp in jsonl_files:
        tr = parse_jsonl_trace(fp)
        if not tr:
            stats["no_model_info"] += 1
            continue
        stats["jsonl"] += 1
        tid = tr.get("traceId") or fp.stem
        if tid in seen:
            stats["dup"] += 1
            continue
        seen[tid] = tr

    rows = []
    for tid, tr in seen.items():
        mi = tr["modelInfo"]
        rows.append({
            "traceId": tid,
            "sessionId": tr.get("sessionId"),
            "startedAt": tr.get("startedAt"),
            "endedAt": tr.get("endedAt"),
            "status": tr.get("status"),
            "agentName": tr.get("agentName"),
            "models": mi.get("models") or [],
            "input": mi.get("totalInputTokens") or 0,
            "output": mi.get("totalOutputTokens") or 0,
            "cached": mi.get("totalCachedTokens") or 0,
            "calls": mi.get("callCount") or 0,
        })

    rows.sort(key=lambda r: r["startedAt"] or "")

    out = Path(args.out)
    with out.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    models = sorted({m for r in rows for m in r["models"]})
    total = sum(r["input"] + r["output"] for r in rows)
    print(
        f"scanned: {stats['json']} json + {stats['jsonl']} jsonl, "
        f"dedup {stats['dup']}, skipped {stats['no_model_info']}"
    )
    print(f"wrote {len(rows)} traces -> {out}")
    print(f"models: {len(models)}  |  total tokens: {total:,}")
    if rows:
        print(f"range: {rows[0]['startedAt']} -> {rows[-1]['startedAt']}")


if __name__ == "__main__":
    main()
