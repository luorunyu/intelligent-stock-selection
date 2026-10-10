# 策略三：申万L2/L3行业指数企稳状态与历史验证

该策略为指定层级申万行业指数的每个历史交易日计算企稳状态，再从连续状态
区间中提取企稳信号并验证后续表现，而不是只判断最新交易日。
`--level` 支持 `L2` 和 `L3`，默认是 `L2`。

## 状态值

- `0 NORMAL`：普通状态。
- `10 STABILIZING_CANDIDATE`：前期下跌条件成立，正在形成企稳价格带，但尚未达到最少企稳天数。
- `20 STABILIZED`：企稳价格带达到最少天数，整段价格带回填为已确认企稳状态。

每个确认区间使用独立的 `episode_id`。一个连续企稳阶段仍然只产生一个正式
信号，信号日是首次达到最少企稳天数的日期。

## 信号规则

- 下跌趋势：下跌持续至少20个交易日，或者从近期高点到低点的跌幅达到30%。
- 企稳：连续至少5个交易日内，全部开盘价和收盘价位于宽度不超过10点的价格带。
- 一个连续企稳阶段只在首次满足条件时产生一个信号。

## 有效性验证

- 默认观察信号后的60个交易日。
- 默认目标价为下跌起点高点收盘价的80%，可通过 `--target-peak-ratio` 调整。
- 如果后续任一收盘价达到目标价，信号记为 `success`。
- 已有完整60日数据但未达到目标价，记为 `failed`。
- 历史数据不足60日且尚未达到目标价，记为 `pending`。

信号判断只使用信号日及之前的数据，未来行情只用于验证，避免未来数据泄漏。

```powershell
python scripts/screen_industry_index_stabilization.py `
  --date 20260924 `
  --level L2 `
  --write-report
```

默认输出：

```text
stock_selection/strategies/industry_index_stabilization/output/2026-09-24/
├── states.csv
├── episodes.csv
├── report.json
└── report.md
```

- `states.csv`：每个行业、每个交易日的状态。
- `episodes.csv`：每个确认企稳区间一行，包含后续验证结果。

扫描L3行业：

```powershell
python scripts/screen_industry_index_stabilization.py `
  --date 20260924 `
  --level L3 `
  --write-report
```

## K线状态图

先编辑：

```text
stock_selection/strategies/industry_index_stabilization/strategy_three_chart_codes.txt
```

一行填写一个申万行业指数代码，然后运行：

```powershell
python scripts/plot_industry_index_stabilization.py --date 20260924
```

图表保存到对应日期输出目录的 `charts/` 下：

- 浅蓝背景：正在形成的企稳候选区间；
- 黄色背景：已经确认的企稳区间；
- 黄色三角：首次确认企稳的信号日；
- 绿色星形：后续达到目标价的日期；
- 虚线：确认企稳区间的价格上下沿。
