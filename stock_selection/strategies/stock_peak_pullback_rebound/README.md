# 策略四：个股高点回落反弹状态

该策略用个股收盘价实时跟踪以下过程：最近新高、从高点有效回落、从低点反弹、
反弹结束。它为每只股票的每个交易日输出一行，但只在“反弹中”和“反弹结束”
两个阶段设置非空状态。

## 数据和范围

- 股票日线来自本地 `data_cache/tushare/daily/`，股票名称来自 `stock_basic.parquet`。
- 总市值和流通市值来自对应交易日的 `data_cache/tushare/daily_basic/`。
- 状态判断只使用缓存中已存储的收盘价；本策略内部不做复权转换，也不使用开高低、成交量或行业数据。
- 按 `--date` 截断数据，最新日也可以在不使用未来行情的情况下得到状态。
- 默认排除名称中包含 `ST` 的股票，可通过 `--include-st` 包含。

## 默认参数

| 参数 | 默认值 | 含义 |
| --- | ---: | --- |
| `peak_lookback_days` | `120` | 候选高点必须达到前120个交易日的最高收盘价 |
| `max_rebound_days` | `40` | 从当前参考高点起，允许等待有效反弹的最大交易日间隔 |
| `min_pullback_pct` | `0.10` | 进入回落阶段至少需从参考高点下跌10% |
| `min_second_peak_ratio` | `0.70` | 反弹开启时收盘价至少达到参考高点的70% |
| `rebound_end_drawdown_pct` | `0.10` | 从本轮反弹最高收盘价回落10%后判定反弹结束 |
| `rebound_ended_state_days` | `10` | `REBOUND_ENDED` 状态保留的交易日数 |

## 状态流转

```text
空状态
  -> 收盘价达到前120日最高收盘价：内部候选高点
  -> 从运行中高点回落至少10%：内部等待反弹
  -> 从低点上涨且达到前高的70%：REBOUND_ACTIVE
  -> 从反弹最高收盘价回落至少10%：REBOUND_ENDED
  -> 保留10个交易日后回到空状态
```

### 1. 候选高点

- 至少有120个既有交易日后才能开始识别。
- 当日收盘价大于或等于前120个交易日最高收盘价时，开始新的 `cycle_id`。
- 候选期间收盘价达到或超过当前参考高点时，`reference_high_date`
  和 `reference_high_close` 都更新到当日。
- 候选阶段本身不是可见状态，`state`、`state_name` 和 `state_description` 仍为空。

### 2. 有效回落和参考低点

- `pullback_pct = 当日收盘价 / reference_high_close - 1`。
- `pullback_pct <= -10%` 的当日进入内部“等待反弹”阶段，并把当日设为初始参考低点。
- 等待期间如果收盘价更低或与当前低点相等，参考低点的日期和价格同步更新。
- 这一阶段也不设置可见状态，但 `reference_*` 和百分比字段会有值。
- 从最新参考高点到当日超过40个交易日仍未开启反弹时，原周期直接失效并重置。

40日从参考高点计时，不是从回落低点计时；间隔恰好等于40日时仍可启动反弹。

### 3. `10 REBOUND_ACTIVE`：高点回落后反弹中

同时满足以下条件的当日开始：

- 当日在当前参考低点之后。
- 当日收盘价严格高于参考低点。
- `当日收盘价 / reference_high_close >= 70%`。

70% 表示当日价格已恢复到原高点的70%，不是从低点反弹70%。
进入状态后，`rebound_high_close` 用达到或超过当前反弹高点的收盘价持续更新。

### 4. `20 REBOUND_ENDED`：反弹结束

```text
drawdown_from_rebound_high = 当日收盘价 / rebound_high_close - 1
```

当 `drawdown_from_rebound_high <= -10%` 时，当日直接记为 `REBOUND_ENDED`，
`state_days=1`。默认连续保留10个交易日；第11个交易日开始重置，
并可按普通规则在当日寻找新候选高点。结束状态保留期内不重新计算反弹高点。

### 5. 突破前高告警

在已达到10%有效回落之后，如果任意一日收盘价严格高于原
`reference_high_close`：

- 当日输出 `alert_code=BREAK_PREVIOUS_HIGH`。
- 当日状态为空，不属于 `REBOUND_ACTIVE` 或 `REBOUND_ENDED`。
- 原周期终止，该日新高作为新候选周期的内部起点。

候选高点尚未发生10%回落时，继续创新高只会上移参考高点，不产生告警。

## 主要输出字段

| 字段 | 含义 |
| --- | --- |
| `cycle_id` | 内部高点-回落-反弹周期编号 |
| `state_start_date` | 当前可见状态的起始日；空状态时为空 |
| `state_days` | 当前 `REBOUND_ACTIVE` 或 `REBOUND_ENDED` 的连续交易日数 |
| `reference_high_*` | 本周期的参考高点日期和收盘价 |
| `reference_low_*` | 有效回落后的运行中参考低点 |
| `rebound_start_date` | 首次满足反弹开启条件的日期 |
| `rebound_high_*` | 本轮反弹期间的最高收盘价及日期 |
| `pullback_pct` | `reference_low_close / reference_high_close - 1` |
| `rebound_pct_from_low` | `当日收盘价 / reference_low_close - 1` |
| `drawdown_from_rebound_high` | `当日收盘价 / rebound_high_close - 1` |
| `total_mv` | 当日总市值；CSV/JSON 保留 Tushare 原始单位万元，Markdown 和图表换算为亿元展示 |
| `circ_mv` | 当日流通市值；CSV/JSON 保留 Tushare 原始单位万元，Markdown 和图表换算为亿元展示 |

不属于两个可见状态的交易日仍会写入 `states.csv`。这些行的 `state`为空，
但在候选或等待反弹阶段，参考高低点等中间字段可能已经有值。

## 与历史形态接口的区别

`screen.py` 还保留了 `calculate_peak_pullback_rebounds()` 历史事件接口。它要求高点
同时是前各默认120个交易日的局部最高收盘价，因此使用了高点之后的未来数据，
只适合历史形态统计。当前 `cli.py` 调用的是
`calculate_peak_pullback_rebound_states()` 逐日状态接口，不使用未来数据。

## 代码位置

- `screen.py`：逐日状态机、历史事件接口和缓存读取。
- `report.py`：最新状态、告警汇总以及输出文件保存。
- `cli.py`：命令行参数和完整执行流程。
- `visualization.py`：将已保存的状态与 OHLCV 合并，生成 K 线图和入口页。
- `scripts/plot_stock_peak_pullback_rebound_states.py`：绘图入口。

## 生成逐日状态

```bash
./.venv/bin/python -m stock_selection.strategies.stock_peak_pullback_rebound.cli \
  --date 20261009 \
  --write-report
```

只计算指定股票时，`--stock-code` 可重复传入：

```bash
./.venv/bin/python -m stock_selection.strategies.stock_peak_pullback_rebound.cli \
  --date 20261009 \
  --stock-code 000001.SZ \
  --write-report
```

## 输出文件

```text
stock_selection/strategies/stock_peak_pullback_rebound/output/YYYY-MM-DD/
├── states.csv
├── report.json
└── report.md
```

- `states.csv`：每只股票每个有效交易日一行，包括空状态和中间参考字段。
- `report.json`：参数、汇总、每只股票的最新行以及全部突破前高告警。
- `report.md`：便于人工查看的最新状态和告警汇总。

## K 线状态图

先在以下文件中每行填写一个股票代码：

```text
stock_selection/strategies/stock_peak_pullback_rebound/strategy_four_chart_codes.txt
```

然后运行：

```bash
./.venv/bin/python scripts/plot_stock_peak_pullback_rebound_states.py --date 20261009
```

图表保存在对应日期的 `charts/` 目录，`index.html` 是汇总入口：

- 黄色背景：`REBOUND_ACTIVE`。
- 蓝色背景：`REBOUND_ENDED`。
- 紫色叉号：`BREAK_PREVIOUS_HIGH` 告警。
- K 线保持红涨绿跌。
- 图表标题显示最新交易日总市值和流通市值；鼠标悬停显示每个历史交易日对应的市值。
- 入口页增加总市值、流通市值两列，方便横向比较。

如果某日没有对应的 `daily_basic` 缓存，状态计算和画图仍会继续，该日市值显示为 `-`，
不会用其他日期的市值回填。

## 解读限制

- `REBOUND_ACTIVE` 只表示价格结构满足阈值，不表示反弹会继续或必然回到前高。
- 70% 恢复阈值是相对前高的价格位置，与从低点上涨了多少是两个不同指标。
- 策略未使用成交确认、行业趋势、资金流、复权校正或公司事件。
- 输出用于研究观察，不构成买入、卖出或做空建议。
