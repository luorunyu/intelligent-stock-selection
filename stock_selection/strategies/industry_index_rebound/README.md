# 策略一：申万行业指数近期低点反弹

该策略对申万行业指数做截面初筛：在最近一段交易日中找到最低收盘价，
再统计低点之后的反弹幅度、持续时间和涨跌天数。它用于判断哪些行业近期
从低位恢复得更强，不负责个股筛选，也不是历史状态机或交易信号。

## 数据和范围

- 行情来自本地 `data_cache/tushare/sw_daily/` 中的申万行业指数日线。
- 行业层级和报告树来自 `data_cache/tushare/static/sw_industry_stock_dictionary.json`。
- 按 `--date` 截断数据，每个指数只使用截止日及之前的记录。
- 报告按申万一级、二级、三级的树形关系展示，但每个指数的指标独立计算。

## 默认参数

| 参数 | 默认值 | 含义 |
| --- | ---: | --- |
| `window` | `20` | 每个行业使用最近20个交易日 |
| `min_rise_from_low` | 未设置 | 相对窗口低点的最小反弹幅度 |
| `min_days_since_low` | 未设置 | 低点之后的最少交易日数 |
| `min_up_days` | 未设置 | 低点之后的最少上涨天数 |

三个筛选阈值默认都不启用。因此不传 `--min-rise`、`--min-days` 和
`--min-up-days` 时，所有数据完整的行业都会被标记为满足条件；这时报告的
主要用途是排序和浏览，不是过滤。

## 计算规则

1. 对每个行业指数取截止日前最近 `window` 个交易日。
2. 在窗口内找最低收盘价。如果最低收盘价多次出现，使用最后一次出现的日期。
3. 从低点的下一个交易日开始，统计上涨、下跌和平盘天数。
4. 将已启用的筛选条件按“且”关系组合；同时满足所有条件才是 `qualified=true`。
5. 报告只展示自身满足条件或至少有一个下级行业满足条件的行业分支。

### 核心指标

| 字段 | 定义 |
| --- | --- |
| `window_start_date` | 当前统计窗口的第一个交易日 |
| `window_low_date` | 窗口内最后一次出现最低收盘价的日期 |
| `window_low_close` | 窗口内最低收盘价 |
| `days_since_low` | 低点日之后到最新日的交易日数，不含低点当日 |
| `rise_from_low_pct` | `最新收盘价 / 窗口最低收盘价 - 1` |
| `up_days_after_low` | 低点后 `pct_chg > 0` 的天数 |
| `up_pct_sum_after_low` | 低点后所有正涨跌幅的算术和，单位是百分点 |
| `down_days_after_low` | 低点后 `pct_chg < 0` 的天数 |
| `down_pct_sum_after_low` | 低点后所有负涨跌幅的算术和，单位是百分点 |
| `flat_days_after_low` | 低点后涨跌幅等于0的天数 |

`pct_chg` 缺失时，程序用相邻收盘价计算日涨跌幅；再无法计算时按0处理。
涨跌幅累计是每日百分点直接相加，不是复利收益率。

## 数据质量

- `ok`：该行业在截止日前至少有 `window` 条有效记录，可计算全部指标。
- `insufficient_history`：有效记录少于 `window`；低点和反弹指标保持为空，且不会通过筛选。

## 代码位置

- `screen.py`：数据标准化、低点后指标计算和简单阈值筛选。
- `report.py`：合格标记、申万一至三级树形报告以及 JSON/Markdown 保存。
- `cli.py`：命令行参数和完整执行流程。
- `visualization.py`：L1/L2/L3 行业 K 线、个股行业查询和静态入口页。
- `scripts/plot_industry_index_rebound.py`：项目根目录下的全量画图入口。

## 运行方式

只计算并在终端输出 JSON：

```bash
./.venv/bin/python -m stock_selection.strategies.industry_index_rebound.cli --date 20261009 --window 20
```

例如，要求相对低点至少上涨5%、低点后至少3个交易日、其中至少2天上涨：

```bash
./.venv/bin/python -m stock_selection.strategies.industry_index_rebound.cli \
  --date 20261009 \
  --window 20 \
  --min-rise 0.05 \
  --min-days 3 \
  --min-up-days 2 \
  --write-report
```

`--min-rise` 使用小数，`0.05` 表示5%。

### 生成 L1/L2/L3 行业图

使用本地缓存为全部申万行业生成图表入口：

```bash
./.venv/bin/python scripts/plot_industry_index_rebound.py \
  --date 20261009 \
  --window 20
```

默认入口页保存到：

```text
stock_selection/strategies/industry_index_rebound/output/YYYY-MM-DD/charts/index.html
```

入口页支持两种查看方式：

- 输入股票代码或名称，查看该股当前的 `L1 → L2 → L3` 完整行业路径，并打开各层级行业图。
- 在 L1、L2、L3 页签中浏览全部行业，按名称或指数代码过滤。

每张行业图包含日 K 线、成交量、最近 `window` 日窗口、窗口最低收盘价、
最新收盘价，以及低点日期、低点后交易日数、反弹幅度和上涨/下跌天数。
所有页面和 Plotly 脚本都保存在本地，打开 `index.html` 不需要联网。

行业字典包含的分类节点不一定都有 Tushare `sw_daily` 指数行情。入口页仍会
列出这些行业并标记为“无缓存行情”，但不会伪造 K 线。个股行业归属使用当前
SW2021 成分关系，不代表历史日期上的行业归属；一个股票有多条归属时会全部展示。

## 输出文件

使用 `--write-report` 后，默认保存到：

```text
analysis_records/sector_analysis/YYYY-MM/
├── YYYY-MM-DD-industry-index-screen.json
└── YYYY-MM-DD-industry-index-screen.md
```

- JSON 保留结构化的一级、二级、三级行业树、指标和筛选条件。
- Markdown 用于人工查看，只展示自身或下级有合格项的分支。

## 解读限制

- “从低点上涨”只描述最近窗口内的价格位置，不等于长期趋势已反转。
- 低点随窗口滚动；旧低点移出窗口后，所有相关指标可能突然变化。
- 该策略不使用成交额、行业宽度、资金流、个股成分扩散或新闻催化。
- 输出用于研究观察，不构成投资建议。
