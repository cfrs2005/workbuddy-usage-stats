# workbuddy-usage-stats

统计 [WorkBuddy](https://www.workbuddy.cn) 本地 trace 里的模型用量，算出三份账：**token、等价 API 成本、积分消耗**。

WorkBuddy 没有用量面板。但每条会话的 token 数都记在本地 trace 文件里——这个工具把它们读出来。

```bash
python3 scripts/scan_traces.py   --out raw.jsonl
python3 scripts/aggregate.py     --raw raw.jsonl --outdir out
python3 scripts/estimate_cost.py --raw raw.jsonl --outdir out
python3 scripts/estimate_credits.py --raw raw.jsonl --outdir out \
        --anchor-model kimi-k3.1-highspeed --anchor-credits 2000
```

```
142 traces, 7 models, 25 days, 289,096,757 tokens
total: $13.26 (¥94.16)
K = 306.02 积分/(倍率·百万 token)　合计 10,181 积分
```

---

## 为什么需要它

你大概不知道自己这个月用了多少 token。客户端不显示，账单也不细到模型。

而这些数据一直都在本地：

| 数据 | 位置 |
|---|---|
| token 用量 | `~/.workbuddy/traces/<pid>/trace_*.json`（旧格式）|
| | `~/.workbuddy/traces/<pid>/trace_*.jsonl`（新格式，逐行）|
| 积分倍率 | `~/.workbuddy/local_storage/entry_*.info` 的 `models[].credits` |
| 价目 | `config/pricing.json`（手工维护） |

只要 `totalInputTokens` / `totalOutputTokens` / `totalCachedTokens` / `callCount` 四个字段，就能还原完整画像。

---

## 三种账，别混着讲

同一批数据能算出三个互相矛盾的结论。这是这个工具最想让人记住的一点：

| 口径 | deepseek 占 | gpt-6.1-sol 占 | 谁最贵 |
|---|---|---|---|
| token 量 | 80.8% | 8.2% | — |
| API 等价成本 | 15.1% | **55.8%** | gpt |
| 积分 | **48.7%** | 无倍率 | deepseek |

- **成本**看厂商单价（美元/百万 token）
- **积分**看客户端倍率 × 用量

两者排序经常相反：单价贵的模型可能倍率低，便宜的模型可能用量极大。

**所以任何结论先说口径。** 只说「哪个模型最费钱」是不完整的，得说是费钱还是费积分。

---

## 安装

```bash
git clone https://github.com/<your-name>/workbuddy-usage-stats.git
cp -r workbuddy-usage-stats ~/.workbuddy/skills/wb-usage-stats
```

或直接当命令行工具用，只依赖 Python 3.10+，无第三方包。

---

## 四个脚本

### 1. `scan_traces.py` — 扫 trace

读 `~/.workbuddy/traces/*/trace_*.json(l)`，抽 `modelInfo`，按 `traceId` 去重。

新格式 `.jsonl` 要逐行解析，只认顶层 `type=trace_start/trace_end` 的 `trace` 对象——因为有些 trace 内容会被转义后塞进 span 的字符串字段里，那些不能算。

```bash
python3 scripts/scan_traces.py --out raw.jsonl
# scanned: 86 json + 56 jsonl, dedup 0, skipped 506
# wrote 142 traces -> raw.jsonl
```

`skipped` 是本身不含 `modelInfo` 的 trace，属于数据源缺口，不是 bug。

### 2. `aggregate.py` — 聚合

```bash
python3 scripts/aggregate.py --raw raw.jsonl --outdir out --tz 8
```

输出 `summary.json`（模型汇总 + 日期×模型矩阵）、`models.csv`、`daily.csv`。

`--tz` 控制归日时区，默认 `8`（UTC+8）。**换口径每日数字就变，写报告必须注明。**

### 3. `estimate_cost.py` — 等价成本

```bash
python3 scripts/estimate_cost.py --raw raw.jsonl --pricing config/pricing.json --outdir out
```

三档价格分开算（缓存命中 / 未命中 / 输出），并对 DeepSeek 逐条判断峰谷——用全天均价会把高峰任务低估一半。

价目表里没有的模型标记为「无公开价」，不编数字。

### 4. `estimate_credits.py` — 积分

```bash
# 只看相对结构
python3 scripts/estimate_credits.py --raw raw.jsonl --outdir out

# 带锚点算绝对值
python3 scripts/estimate_credits.py --raw raw.jsonl --outdir out \
        --anchor-model kimi-k3.1-highspeed --anchor-credits 2000
```

倍率自动从本机客户端配置读取，不需要手填。峰谷折扣（deepseek 夜间减半）自动应用。

---

## 积分为什么需要锚点

官方公开了倍率（`0.11x`、`1.56x` 这类相对系数），但没公开「1.0x 对应多少积分/百万 token」。

所以绝对积分只能反推：

```
K = 实测积分 ÷ (倍率 × 用量百万数)
```

本项目实测 K ≈ 306。注意这是**从一次采样推出来的**，误差约 ±10%，只能用于量级和相对倍数，不能当账单。

想知道结构而不在乎绝对值时，不用给锚点。

---

## 已知局限

| 局限 | 说明 |
|---|---|
| 只覆盖有 trace 的会话 | 未落 trace 的短期对话、部分无 `modelInfo` 的新格式 trace 不在统计内，实际用量只会更高 |
| 成本是估算 | 按公开价目算，不是官方账单；不含税费、折扣、企业协议价 |
| 分时判断有边界误差 | 跨高峰窗口的长会话按起始时间归属，只有少量误差 |
| 积分是推算 | 锚点单点标定，见上 |
| 不含搜索/抓取等附加费用 | DeepSeek 的 Search & Fetch 按请求计费，未计入 |

---

## 目录

```
workbuddy-usage-stats/
├── SKILL.md                     # WorkBuddy skill 定义
├── scripts/
│   ├── scan_traces.py
│   ├── aggregate.py
│   ├── estimate_cost.py
│   └── estimate_credits.py
├── config/
│   └── pricing.json             # 价目表（含来源与核对日期）
├── references/
│   ├── pricing.md               # 价目说明与更新方法
│   └── credits.md               # 倍率机制与标定方法
├── examples/
│   └── 2026-10/                 # 真实跑通的产物样本
├── LICENSE
└── README.md
```

---

## 隐私

只读本地文件，不联网、不上传。输出里只有模型名、token 数、时间戳，不含对话内容。

如果要把产物分享出去，检查一下 `raw.jsonl` 里的 `sessionId`——那是会话 ID，不含正文但能对应到具体会话。

---

## License

MIT。见 [LICENSE](LICENSE)。
