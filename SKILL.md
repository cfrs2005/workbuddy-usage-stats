---
name: wb-usage-stats
description: 统计 WorkBuddy 本地 trace 里的模型用量，算出每天/每模型的 token、等价 API 成本与积分消耗。用户问「我这个月用了多少 token」「哪个模型最费钱」「积分都花哪了」「导出用量报表」时使用。
version: 1.0.0
platforms: [macos, linux]
metadata:
  workbuddy:
    tags: [usage, tokens, cost, credits, analytics, trace]
    related_skills: []
---

# wb-usage-stats

WorkBuddy 没有用量面板，但每条会话的 token 都记在本地 trace 里。这个 skill 把它们读出来，算成三份账：**用量、钱、积分**。

```
~/.workbuddy/traces/  →  raw.jsonl  →  summary.json（用量）
                                   →  cost.json（等价 API 成本）
                                   →  credits.json（积分）
```

## 数据从哪来

| 数据 | 位置 | 说明 |
|---|---|---|
| token 用量 | `~/.workbuddy/traces/<pid>/trace_*.json` | 旧格式，文件头 `trace.modelInfo` |
| | `~/.workbuddy/traces/<pid>/trace_*.jsonl` | 新格式，逐行流式，值在 `trace_start`/`trace_end` |
| 积分倍率 | `~/.workbuddy/local_storage/entry_*.info` | `data.models[].credits`，形如 `x0.11` |
| 价目 | `config/pricing.json` | 手工维护，带来源与核对日期 |

三个字段决定一切：`totalInputTokens`、`totalOutputTokens`、`totalCachedTokens`。加上 `callCount` 和 `startedAt` 就能还原出完整的用量画像。

**注意**：新格式 trace 有一部分不记 `modelInfo`，扫的时候会被跳过（输出里 `skipped` 会体现）。这不是 bug，是数据源本身的缺口。

## 三种账，别混着讲

同一批数据可以算出三个完全不同的结论，混着讲会自相矛盾：

| 口径 | deepseek 占 | gpt-6.1-sol 占 | 谁最贵 |
|---|---|---|---|
| token 量 | 80.8% | 8.2% | — |
| API 等价成本 | 15.1% | **55.8%** | gpt |
| 积分 | **48.7%** | 无倍率 | deepseek |

- **成本**看厂商单价（美元）
- **积分**看客户端倍率 × 用量
- 两者排序经常相反，因为贵的模型可能倍率低，便宜的模型可能用得多

写任何结论前，先说清用的是哪个口径。

## 工作流

```bash
# 1. 扫 trace
python3 scripts/scan_traces.py --out raw.jsonl

# 2. 聚合（默认按 UTC+8 归日）
python3 scripts/aggregate.py --raw raw.jsonl --outdir out

# 3. 等价成本（自动处理 DeepSeek 峰谷）
python3 scripts/estimate_cost.py --raw raw.jsonl \
        --pricing config/pricing.json --outdir out

# 4. 积分（可只算相对结构，也可带锚点算绝对值）
python3 scripts/estimate_credits.py --raw raw.jsonl --outdir out \
        --anchor-model kimi-k3.1-highspeed --anchor-credits 2000
```

四步可独立跑，产物都落在 `out/`。

## 输入输出

**输入**

- trace 目录（默认 `~/.workbuddy/traces`，可用 `--traces-dir` 指定）
- `--tz`：归日时区，默认 `8`（UTC+8）。**口径会改变每日数字，报告里必须写明。**
- `--pricing`：价目表，默认 `config/pricing.json`
- `--anchor-model` / `--anchor-credits`：积分标定锚点，两个都给才算绝对积分

**输出**

| 文件 | 内容 |
|---|---|
| `raw.jsonl` | 一条 trace 一行，去重后 |
| `out/summary.json` | 模型汇总 + 日期×模型矩阵 |
| `out/models.csv` | 模型维度，可直接进表格 |
| `out/daily.csv` | 日期×模型交叉表 |
| `out/cost.json` | 每模型 USD/CNY 成本，含峰谷拆分 |
| `out/credits.json` | 每模型倍率、加权 token、积分 |

## 适用场景

**用**

- 「这个月用了多少 token / 花了多少钱 / 积分都去哪了」
- 对比几个模型谁更费钱，决定下次派活给谁
- 出月度用量报表、做成本复盘
- 想验证某个模型的实际单价是否符合预期

**不用**

- 实时监控（这是离线统计，不是仪表盘）
- WorkBuddy 之外的工具用量（这里只读 WorkBuddy 的 trace）
- 需要精确账单金额（估算是按公开价目和倍率推算，不是官方账单）

## 关键约束

1. **只读。** 不修改、不删除任何 trace 文件。
2. **不猜价格。** 价目表里没有的模型标记为「无公开价」，不给数字，不按同类硬凑。
3. **倍率只信本机配置。** 客户端 `models[].credits` 是唯一可靠来源，二手文章的价值经常过期。
4. **峰谷必须逐条判断。** DeepSeek 高峰价是闲时 2 倍，用全天均价会把高峰任务低估一半。
5. **积分标定要声明误差。** 官方未公开「1.0x = 多少积分/百万 token」，锚点由单次实测反推，存在抽样误差（通常 ±10%），只能用于量级和相对倍数，不能当账单。
6. **跨模型不重复计。** 一个 trace 若含多个模型，token 归到各自模型；同一 trace 的 token 只属于它的模型集合。

## 换算依据

**成本**：见 `config/pricing.json`，每个模型带 `source` 与 `verified_at`。价格会变，用之前复核官方页面。

**积分**：

```
积分 = 倍率 × 用量
用量受 token 数、上下文长度、工具调用轮次共同影响
```

官方只公开了倍率（0.08x / 0.11x / 1.56x 这类相对系数），没公开绝对基准。所以：

- 只想知道**结构**（谁占大头）→ 用加权 token 和占比，不需要锚点
- 想知道**绝对积分** → 需要一次实测标定：`K = 实测积分 ÷ (倍率 × 用量百万数)`

本项目实测得到的 K ≈ 306 积分/(倍率·百万 token)，据此全月约 1.02 万积分。

## 文件清单

```
wb-usage-stats/
├── SKILL.md
├── scripts/
│   ├── scan_traces.py       # trace -> raw.jsonl（去重）
│   ├── aggregate.py         # raw -> summary + csv
│   ├── estimate_cost.py     # raw + pricing -> cost.json
│   └── estimate_credits.py  # raw + 倍率 + 锚点 -> credits.json
├── config/
│   └── pricing.json         # 价目表（含来源与核对日期）
└── references/
    ├── pricing.md           # 价目说明与更新方法
    └── credits.md           # 倍率机制与标定方法
```

## 故障排查

| 症状 | 原因 | 处理 |
|---|---|---|
| `scanned: 0` | trace 目录不对 | 确认 `~/.workbuddy/traces` 存在，或用 `--traces-dir` |
| 总量比预期少 | 部分新格式 trace 无 `modelInfo` | 看输出里的 `skipped` 计数，这是数据源缺口 |
| 成本为 0 | 该模型在 pricing.json 里没配或配成 null | 补价目，或接受「无公开价」标记 |
| 积分全为 `—` | 没给锚点，或该模型无倍率 | 给 `--anchor-model --anchor-credits`；无倍率的模型查客户端 |
| 倍率读不到 | 客户端配置路径变了 | 用 `--storage` 指定，或 `--multipliers` 传 JSON |
| 归日后数字对不上 | 时区口径不同 | 默认 UTC+8，换口径要重跑并在报告里说明 |
