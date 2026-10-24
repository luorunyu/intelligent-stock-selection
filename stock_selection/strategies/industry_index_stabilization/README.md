# 策略三：申万 L2/L3 行业指数企稳状态与历史验证

该策略在申万行业指数经历一段时间或一定幅度的下跌后，寻找开盘价和收盘价
收窄到固定价格带的连续区间。它同时输出逐日状态、正式企稳信号，并检查历史信号
在后续窗口内是否恢复到指定目标价。

`--level` 只支持 `L2` 和 `L3`，默认是 `L2`。

## 数据和范围

- 指数 OHLCV 来自本地 `data_cache/tushare/sw_daily/`。
- 有效行业代码和名称来自 `data_cache/tushare/static/sw_industry_stock_dictionary.json`。
- 企稳价格带只使用开盘价和收盘价；日内最高价、最低价和成交量不参与状态判断。
- 默认计算字典中全部所选层级行业；`--industry-code` 可重复传入以限定范围。
- 按 `--date` 截断状态数据，该日之后的行情不参与本次计算。

## 默认参数

| 参数 | 默认值 | 含义 |
| --- | ---: | --- |
| `trend_lookback` | `120` | 从企稳区间起点向前寻找下跌起点的最大回看交易日数 |
| `min_decline_days` | `20` | 从下跌起点到企稳区间起点的最少交易日数 |
| `min_decline_pct` | `0.30` | 从下跌起点到区间低点的最小跌幅30% |
| `min_stable_days` | `5` | 确认企稳所需的最少连续交易日数 |
| `max_stable_range_points` | `10.0` | 企稳价格带的最大绝对宽度，单位是指数点 |
| `forward_window` | `60` | 信号后的验证交易日数 |
| `target_peak_ratio` | `0.80` | 目标价为下跌起点收盘价的80% |

10点是绝对指数点，不是10%。

## 下跌背景的精确定义

对每个正在形成的企稳价格带，程序按以下方式建立下跌背景：

1. 从价格带起点向前最多回看120个交易日。
2. 取这段回看区间中的最高收盘价作为 `decline_start_close`。
3. 从该高点到当前日寻找最低收盘价，且这个低点必须出现在当前企稳价格带内。
4. `decline_days` 是下跌起点到价格带起点的交易日间隔。
5. `decline_pct = decline_low_close / decline_start_close - 1`。
6. `decline_days >= 20` 或 `decline_pct <= -30%` 任意一项成立，就认为存在有效下跌背景。

`downtrend_by_duration` 和 `downtrend_by_drawdown` 分别记录是否由持续时间或跌幅条件确认。

## 企稳价格带

每个交易日使用 `min(open, close)` 作为当日下沿，使用 `max(open, close)`
作为当日上沿。程序在以当日为结尾的连续区间中，寻找能满足下式的最长尾随区间：

```text
stable_price_min = 区间内全部 open 和 close 的最小值
stable_price_max = 区间内全部 open 和 close 的最大值
stable_range_points = stable_price_max - stable_price_min
stable_range_points <= 10
```

价格带边界包含恰好等于10点的情况。只要当日仍存在至少5日的有效尾随区间，
已确认的企稳阶段就继续；当有效尾随区间缩短到5日以下时，该阶段结束。

## 状态定义

| 值 | 枚举 | 定义 |
| ---: | --- | --- |
| `0` | `NORMAL` | 当日既不是有效企稳候选，也不在已确认企稳阶段中。旧阶段结束后，当日也可能根据新价格带直接进入新的候选状态。 |
| `10` | `STABILIZING_CANDIDATE` | 有效下跌背景已成立，当前价格带连续1至4个交易日，尚未达到5日确认阈值。 |
| `20` | `STABILIZED` | 价格带首次达到5个交易日时确认。程序将该价格带从起点到确认日的历史记录全部回填为此状态，之后在企稳阶段继续期间逐日延长。 |

每个确认区间使用独立的 `episode_id`。一个连续企稳阶段只生成一个正式信号：

- `signal_date`：首次达到5个交易日的确认日。
- `state_start_date`：该企稳价格带的起点。
- `state_days`：当日是从价格带起点开始的第几个交易日。

### 回填和未来数据

`STABILIZED` 的前4天是在第5天到来后才回填的历史状态。因此：

- 正式信号日及其规则只使用当日及之前数据，可用于无未来函数的信号研究。
- 最终 `states.csv` 中信号日前的 `STABILIZED` 记录包含事后确认，不能当作当日已知信号。

## 后续有效性验证

每个正式信号从信号日的下一个交易日开始，最多观察60个交易日。

```text
target_close = decline_start_close * 80%
max_forward_return = 验证窗口最高收盘价 / signal_close - 1
max_forward_peak_ratio = 验证窗口最高收盘价 / decline_start_close
```

| `validation_status` | 定义 |
| --- | --- |
| `success` | 验证窗口内任意一日收盘价大于或等于 `target_close`；即使尚不足60日也立即成功 |
| `failed` | 已有完整60个后续交易日，但始终未达到目标价 |
| `pending` | 尚未达标，且信号后数据不足60个交易日 |

报告中的成功率只使用 `success + failed` 已完成样本作分母，不包含 `pending`。

## 代码位置

- `screen.py`：企稳状态、区间提取和后续表现验证。
- `report.py`：最新状态、历史成功率汇总以及输出文件保存。
- `cli.py`：命令行参数和完整执行流程。
- `visualization.py`：读取已保存状态并生成 K 线图和图表入口页。
- `scripts/plot_industry_index_stabilization.py`：绘图入口。

## 运行方式

计算 L2 逐日状态和历史验证：

```bash
./.venv/bin/python -m stock_selection.strategies.industry_index_stabilization.cli \
  --date 20261009 \
  --level L2 \
  --write-report
```

扫描 L3 或限定行业：

```bash
./.venv/bin/python -m stock_selection.strategies.industry_index_stabilization.cli \
  --date 20261009 \
  --level L3 \
  --industry-code 850111.SI \
  --write-report
```

## 输出文件

```text
stock_selection/strategies/industry_index_stabilization/output/YYYY-MM-DD/
├── states.csv
├── episodes.csv
├── report.json
└── report.md
```

- `states.csv`：所选行业每个交易日一行，保留 OHLCV、状态、价格带和下跌背景。
- `episodes.csv`：每个已确认企稳阶段一行，包含信号日、完整区间和后续验证结果。
- `report.json`：参数、汇总、每个行业的最新状态和全部历史信号。
- `report.md`：便于人工查看的汇总和信号明细。

## K 线状态图

在以下文件中每行填写一个申万行业指数代码：

```text
stock_selection/strategies/industry_index_stabilization/strategy_three_chart_codes.txt
```

然后运行：

```bash
./.venv/bin/python scripts/plot_industry_index_stabilization.py --date 20261009
```

图表保存在对应日期的 `charts/` 目录，`index.html` 是汇总入口：

- 浅蓝背景：`STABILIZING_CANDIDATE`。
- 黄色背景：`STABILIZED`。
- 黄色三角：首次确认企稳的 `signal_date`。
- 绿色星形：首次达到目标价的 `target_hit_date`。
- 虚线：企稳区间的价格上下沿。

## 解读限制

- 固定10点价格带对不同绝对点位的指数不是同一百分比宽度。
- 目标价是下跌起点的80%，不是要求从信号价上涨80%。
- 该策略不使用行业成分股宽度、成交额扩散、资金流或新闻催化。
- 输出用于研究观察和历史验证，不构成投资建议。
