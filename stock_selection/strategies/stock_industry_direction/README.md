# 策略二：个股与申万行业同方向比例

该策略衡量个股与其当前所属申万二级或三级行业指数在最近一段时间内
的收益联动。方向指标记录同涨、同跌和反方向天数；相关系数、Beta 和 R²
补充衡量收益率幅度关系。默认一次运行会生成所有完整历史滚动窗口，
而不是只计算最新一天。

## 数据和范围

- 个股日涨跌幅来自 `data_cache/tushare/daily/`。
- 申万行业指数日涨跌幅来自 `data_cache/tushare/sw_daily/`。
- 股票名称来自日线或 `data_cache/tushare/static/stock_basic.parquet`。
- 行业成分关系来自 `data_cache/tushare/static/sw_industry_stock_dictionary.json`。
- 只支持 `L2` 和 `L3`，默认是 `L2`。
- 默认排除名称中包含 `ST` 的股票，可通过 `--include-st` 包含。

成分关系使用当前申万 SW2021 字典，不是按历史日期复原的成分关系。
因此回看过去的行情时，存在用当前归属解释历史的偏差。

## 默认参数

| 参数 | 默认值 | 含义 |
| --- | ---: | --- |
| `level` | `L2` | 与申万二级行业指数比较 |
| `window` | `20` | 最近20个股票与行业都有涨跌幅的已对齐交易日 |
| `min_same_direction_ratio` | `0.60` | 报告只保留同方向比例大于或等于60%的有效关系 |
| `max_report_results` | `200` | JSON 和 Markdown 最多展示200条筛选结果 |

## 计算规则

1. 按 `--date` 同时截断个股日线和行业指数日线。
2. 按交易日对个股和行业涨跌幅做内连接，去掉任意一方涨跌幅缺失的日期。
3. 取最近 `window` 个已对齐交易日；不足完整窗口时不缩短窗口计算。
4. 在完整窗口内按涨跌方向分类：同涨和同跌记为同方向，一涨一跌记为反方向，任意一方涨跌幅等于0记为中性日。
5. 中性日记入 `observations`，但不进入同方向比例分母。
6. 相关系数、Beta 和 R² 使用同一窗口的全部已对齐收益率，零收益日也保留。
7. 历史长表从第一个完整窗口开始，每行代表一个“日期-股票-行业”关系。
8. 仅对 `data_quality=ok` 的最新截面关系应用 `--min-ratio` 阈值。

```text
same_direction_days = same_up_days + same_down_days
direction_observations = same_direction_days + opposite_direction_days
same_direction_ratio = same_direction_days / direction_observations
neutral_days = observations - direction_observations
return_correlation = Corr(stock_return, industry_return)
industry_beta = Cov(stock_return, industry_return) / Var(industry_return)
industry_r_squared = return_correlation ^ 2
```

方向天数只使用 `pct_chg` 的符号。个股涨0.1%、行业涨5%与两者都涨1%一样，
都只记为1个同涨日；但它们对相关系数、Beta 和 R² 的贡献不同。回归为
带截距的一元回归：

```text
stock_return = alpha + industry_beta * industry_return + error
```

## 一股多行业的处理

一只股票如果在字典中对应多个所选层级的行业，会保留多行计算结果，
每行代表一个“股票-行业”关系。报告同时保留 `l1_*`、`l2_*` 和 `l3_*`
完整路径；多个层级路径会用分号合并展示。

## 数据质量状态

| `data_quality` | 含义 | 是否进入筛选 |
| --- | --- | --- |
| `ok` | 最新日期一致，且有完整的已对齐窗口和至少一个非中性日 | 是 |
| `st_excluded` | 股票名称包含 `ST`，且未启用 `--include-st` | 否 |
| `missing_stock_returns` | 本地缓存没有该股票的有效涨跌幅 | 否 |
| `missing_industry_index` | 本地缓存没有对应行业指数的有效涨跌幅 | 否 |
| `no_latest_trade` | 股票最新日期与行业指数最新日期不一致 | 否 |
| `insufficient_history` | 去掉缺失日后，已对齐记录少于 `window` | 否 |
| `no_direction_observations` | 完整窗口内每一天都至少有一方涨跌幅为0 | 否 |

回归指标另有 `regression_quality`：`ok`、`zero_industry_variance` 或
`zero_stock_variance`。方差为零时无法定义对应的相关系数或 R²，不会强行填0。

`no_latest_trade` 只检查个股和行业的最新日期是否相同，不额外要求它们一定等于
请求的 `--date`。如果两类缓存同时滞后，仍可能得到 `ok`，因此应同时查看
`latest_stock_date` 和 `latest_industry_date`。

## 代码位置

- `screen.py`：缓存读取、成分关系展开、交易日对齐、比例计算和阈值筛选。
- `report.py`：报告汇总、数据质量统计和 CSV/JSON/Markdown 保存。
- `cli.py`：命令行参数和完整执行流程。

## 运行方式

一次生成截至指定日期的全部申万 L2 历史滚动关系：

```bash
./.venv/bin/python -m stock_selection.strategies.stock_industry_direction.cli \
  --date 20261009 \
  --level L2 \
  --window 20 \
  --min-ratio 0.60 \
  --write-report
```

`--date` 是历史结果的截止日期。可用 `--start-date` 限制输出起始日；
起始日之前的数据仍会用于填充起始日的滚动窗口。如果只需要之前的最新截面，
增加 `--latest-only`。

只计算指定的 L3 行业；`--industry-code` 可重复传入：

```bash
./.venv/bin/python -m stock_selection.strategies.stock_industry_direction.cli \
  --date 20261009 \
  --level L3 \
  --industry-code 851761.SI \
  --window 20 \
  --write-report
```

## 输出文件

使用 `--write-report` 后，默认保存到：

```text
analysis_records/stock_industry_direction/YYYY-MM/
├── YYYY-MM-DD-L2-stock-industry-direction.csv
├── YYYY-MM-DD-L2-stock-industry-direction.json
└── YYYY-MM-DD-L2-stock-industry-direction.md

analysis_records/stock_industry_direction/history/
├── through-YYYY-MM-DD-L2-window-20-stock-industry-direction-history.parquet
└── through-YYYY-MM-DD-L2-window-20-stock-industry-direction-history.json
```

指定单个行业或多个行业时，文件名会增加行业代码或行业数量后缀。

- CSV 保存全部成分关系，包括数据质量不合格和未达60%阈值的行。
- JSON 和 Markdown 只展示达到 `--min-ratio` 的结果，并受 `--max-report-results` 限制。
- `history/*.parquet` 保存全部历史滚动长表，同名 JSON 记录口径和行数。
- `selected_rows_before_limit` 是限制前的合格数，`returned_rows` 是实际展示数。

## 解读限制

- 高同方向比例只说明方向同步；个股可能涨得更少、跌得更多，仍然得到高比例。
- 大量中性日会缩小分母；解读比例时应同时查看 `direction_observations`。
- 该指标不使用成交量、资金流、估值、新闻催化或公司基本面。
- 输出用于研究观察，不构成投资建议。
