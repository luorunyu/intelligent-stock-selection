# Tushare 5000 积分权限总结

整理日期：2026-06-08

资料来源：

- Tushare 官方「关于权限」：https://tushare.pro/document/2?doc_id=108
- Tushare 官方「积分与频次权限对应表」：https://tushare.pro/document/1?doc_id=290

## 1. 先记住结论

5000 积分属于 Tushare 常规 Pro 接口里比较够用的档位：

- 可以访问大多数最低积分要求小于等于 5000 的常规接口。
- 官方频次表显示：5000 积分以上，每分钟频次约 500 次，常规数据无日总量上限。
- 积分是权限门槛，不是每次查询都会扣积分。
- 分钟数据、实时数据、港美股、新闻、公告、研报、政策库等很多接口属于「独立权限」，不是 5000 积分自动包含。

对当前这个“智能选股系统”项目来说，5000 积分已经足够覆盖 A 股日线行情、复权行情、每日指标、财务报表、财务指标、指数行情、行业分类、申万行业日线、资金流、龙虎榜、两融、限售解禁、大宗交易等核心数据。

## 2. A 股选股最常用接口

| 数据 | API | 5000 积分是否可用 | 用途 |
|---|---|---:|---|
| 股票列表 | `stock_basic` | 通常可用 | 获取 A 股股票代码、名称、上市状态 |
| 交易日历 | `trade_cal` | 通常可用 | 判断交易日、生成回测日期 |
| 日线行情 | `daily` | 可用 | 开高低收、成交量、成交额 |
| 周线行情 | `weekly` | 可用 | 中长期周期分析 |
| 月线行情 | `monthly` | 可用 | 长周期趋势分析 |
| 复权行情 | `pro_bar` | 可用，日线/周线/月线可用 | 获取前复权、后复权价格 |
| 复权因子 | `adj_factor` | 可用 | 自己计算复权价格 |
| 每日指标 | `daily_basic` | 可用 | 换手率、量比、市盈率、市净率、市值等 |
| 涨跌停价格 | `stk_limit` | 可用 | 判断涨停、跌停、连板、炸板逻辑 |
| 停复牌信息 | `suspend_d` | 通常可用 | 回测时过滤停牌股 |

示例：

```python
import tushare as ts

pro = ts.pro_api("你的token")

# 平安银行最近3条日线
df = ts.pro_bar(api=pro, ts_code="000001.SZ", adj="qfq", limit=3)

# 某天全市场每日指标
daily_basic = pro.daily_basic(trade_date="20260608")
```

## 3. 财务与基本面数据

| 数据 | API | 5000 积分是否可用 | 用途 |
|---|---|---:|---|
| 利润表 | `income` | 可用 | 营收、利润、成本费用 |
| 资产负债表 | `balancesheet` | 可用 | 资产、负债、所有者权益 |
| 现金流量表 | `cashflow` | 可用 | 经营/投资/筹资现金流 |
| 业绩预告 | `forecast` | 可用 | 业绩预增、预减、扭亏等 |
| 业绩快报 | `express` | 可用 | 财报正式披露前的快报 |
| 分红送股 | `dividend` | 可用 | 分红、送转、股权登记日 |
| 财务指标 | `fina_indicator` | 可用 | ROE、毛利率、净利率、资产负债率等 |
| 审计意见 | `fina_audit` | 可用 | 识别非标审计意见 |
| 主营业务构成 | `fina_mainbz` | 可用 | 行业/产品/地区收入结构 |
| 财报披露计划 | `disclosure_date` | 可用 | 预判财报披露时间 |

选股时比较常用的是：

- `daily_basic`：估值、市值、换手率。
- `fina_indicator`：盈利能力、成长性、偿债能力。
- `income`、`balancesheet`、`cashflow`：做更细的基本面因子。
- `forecast`、`express`：做业绩预期变化类策略。

## 4. 资金、交易行为与事件数据

| 数据 | API | 5000 积分是否可用 | 用途 |
|---|---|---:|---|
| 个股资金流向 | `moneyflow` | 可用 | 主力、小单、中单、大单资金流 |
| 融资融券汇总 | `margin` | 可用 | 两融市场整体变化 |
| 融资融券明细 | `margin_detail` | 可用 | 个股融资买入、融券卖出 |
| 龙虎榜每日明细 | `top_list` | 可用 | 游资/机构活跃股 |
| 龙虎榜机构明细 | `top_inst` | 可用 | 机构买卖席位 |
| 大宗交易 | `block_trade` | 可用 | 大额成交、折溢价交易 |
| 股东人数 | `stk_holdernumber` | 可用 | 筹码集中度变化 |
| 股东增减持 | `stk_holdertrade` | 可用 | 重要股东交易行为 |
| 股权质押统计 | `pledge_stat` | 可用 | 质押风险 |
| 股权质押明细 | `pledge_detail` | 可用 | 具体质押记录 |
| 股票回购 | `repurchase` | 可用 | 回购事件 |
| 限售股解禁 | `share_float` | 可用 | 解禁压力 |
| 沪深股通持股明细 | `hk_hold` | 可用 | 北向资金持股变化 |

## 5. 指数与行业数据

| 数据 | API | 5000 积分是否可用 | 用途 |
|---|---|---:|---|
| 指数基本信息 | `index_basic` | 可用 | 查询指数代码，例如 `000001.SH` |
| 指数日线 | `index_daily` | 可用 | 大盘、宽基、行业指数行情 |
| 指数周线 | `index_weekly` | 可用 | 指数中周期分析 |
| 指数月线 | `index_monthly` | 可用 | 指数长期趋势 |
| 指数成分和权重 | `index_weight` | 可用 | 沪深300、中证500等成分股 |
| 大盘指数每日指标 | `index_dailybasic` | 可用，官方表中为 4000 起 | 市盈率、市净率、换手率等指数指标 |
| 申万行业分类 | `index_classify` | 可用 | 行业分类体系 |
| 申万行业成分 | `index_member_all` | 可用 | 股票所属申万行业 |
| 申万行业日线 | `sw_daily` | 可用，文档写明 5000 积分可调取 | 行业指数行情、估值、市值 |

做行业轮动时，建议组合：

- `index_classify`：拿行业层级。
- `index_member_all`：拿行业成分股。
- `sw_daily`：拿行业指数日线表现。
- `daily_basic`：聚合行业估值、市值、换手。

## 6. 基金、期货、期权、债券、外汇

5000 积分也可以覆盖不少跨资产数据：

| 类型 | 常用 API | 5000 积分是否可用 |
|---|---|---:|
| 公募基金 | `fund_basic`, `fund_company`, `fund_nav`, `fund_daily`, `fund_div`, `fund_portfolio`, `fund_adj` | 可用，其中 `fund_adj` 为 5000 起 |
| 期货 | `fut_basic`, `fut_daily`, `fut_holding`, `fut_wsr`, `fut_settle` | 可用 |
| 期权 | `opt_basic`, `opt_daily` | 可用，`opt_daily` 为 5000 起 |
| 可转债 | `cb_basic`, `cb_issue`, `cb_daily` | 可用 |
| 外汇 | `fx_obasic`, `fx_daily` | 可用 |
| 宏观利率 | `shibor`, `shibor_quote`, `shibor_lpr`, `libor`, `hibor`, `wz_index`, `gz_index` | 可用 |

## 7. 5000 积分不自动包含的内容

下面这些不要默认能查。它们通常需要单独开权限，或者更高积分/特色权限：

| 类型 | 说明 |
|---|---|
| A 股历史分钟 | 1、5、15、30、60 分钟数据，独立权限 |
| A 股实时分钟 | 盘中实时分钟，独立权限 |
| A 股实时日线 | 开盘后的当日实时成交，独立权限 |
| 指数实时日线 | 独立权限 |
| 申万指数实时行情 | 独立权限 |
| ETF 实时日线/实时参考 | 独立权限 |
| 期货/期权历史分钟和实时分钟 | 独立权限 |
| 港股日线、港股分钟、港股财报、港股实时 | 独立权限 |
| 美股日线、美股财报 | 独立权限 |
| 新闻资讯 | 快讯、长篇新闻、新闻联播等，独立权限 |
| 公告信息 | 上市公司、基金、固收公告等，独立权限 |
| 政策法规库 | 独立权限 |
| 券商研报库 | 独立权限 |
| 部分特色数据 | 例如筹码分布、盈利预测、券商月度金股等通常需要 10000 或 15000 积分档 |

## 8. 对本项目的建议

建议第一阶段只使用 5000 积分稳定可用的数据，不碰独立权限接口：

1. 行情层：`pro_bar`, `daily`, `weekly`, `monthly`, `adj_factor`
2. 指标层：`daily_basic`, `stk_limit`, `moneyflow`
3. 财务层：`fina_indicator`, `income`, `balancesheet`, `cashflow`, `forecast`, `express`
4. 行业层：`index_classify`, `index_member_all`, `sw_daily`, `index_daily`
5. 风险事件层：`share_float`, `pledge_stat`, `pledge_detail`, `repurchase`, `stk_holdernumber`, `stk_holdertrade`

最适合先做的选股因子：

- 价格动量：近 20/60/120 日收益率。
- 趋势强度：均线多头、突破新高、回撤幅度。
- 估值：PE、PB、总市值、流通市值。
- 交易活跃度：换手率、量比、成交额。
- 资金流：主力净流入、大单净流入。
- 财务质量：ROE、毛利率、净利率、经营现金流。
- 成长性：营收同比、净利润同比。
- 行业强度：申万行业指数涨跌幅、行业内上涨家数占比。

## 9. 常见查询模板

```python
import tushare as ts

pro = ts.pro_api("你的token")

# A股股票列表
stocks = pro.stock_basic(exchange="", list_status="L")

# 单只股票前复权行情
bars = ts.pro_bar(
    api=pro,
    ts_code="000001.SZ",
    adj="qfq",
    start_date="20250101",
    end_date="20260608",
)

# 某日全市场每日指标
basic = pro.daily_basic(trade_date="20260608")

# 财务指标
fina = pro.fina_indicator(ts_code="000001.SZ", start_date="20240101")

# 指数日线
index_daily = pro.index_daily(ts_code="000001.SH", start_date="20250101")

# 申万行业日线
sw = pro.sw_daily(trade_date="20260608")

# 个股资金流
mf = pro.moneyflow(ts_code="000001.SZ", start_date="20260601", end_date="20260608")
```

## 10. 查询时的注意点

- `000001.SZ` 是平安银行，`000001.SH` 是上证指数，后缀非常重要。
- `pro_bar` 是 SDK 里的通用行情封装，不是普通 HTTP 接口。
- 很多接口单次返回有行数限制，拉历史全量数据时要按日期或股票代码循环。
- 交易日当天数据通常在盘后更新，A 股日线和每日指标一般在 15:00 到 17:00 附近更新，部分事件数据更晚。
- 如果接口提示「没有权限」，优先判断它是不是独立权限接口，而不是代码写错。
- 如果接口返回 `NaN`，不一定是权限问题，可能是当日数据未补齐、停牌、上市首日或字段本身不可得。
