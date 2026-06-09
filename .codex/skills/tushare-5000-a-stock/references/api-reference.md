# Tushare 5000 积分 A 股接口参考

整理日期：2026-06-08

官方来源：

- 权限说明：https://tushare.pro/document/2?doc_id=108
- 积分与频次权限表：https://tushare.pro/document/1?doc_id=290

## 1. 权限边界

对 5000 积分个人账户，可以假设以下数据通常适合用于 A 股选股系统：

- A 股日线、周线、月线行情
- 通过 `ts.pro_bar` 获取复权行情
- 每日估值、换手率、量比、市值等指标
- 财务报表和财务指标
- 指数基础信息、指数行情、指数权重、指数每日指标
- 申万行业分类、行业成分、行业日线
- 资金流、融资融券、龙虎榜、大宗交易
- 股东人数、股东增减持、股权质押、回购、限售解禁
- 很多基金、期货、期权、可转债、外汇和宏观利率接口

不要默认包含以下数据：

- A 股历史分钟、实时分钟、实时日线
- 港股、美股、港美股财务
- 新闻、公告、研报、政策库
- 部分高级数据，例如筹码分布、券商金股、部分盈利预测类数据

## 2. 代码初始化

### 本项目优先入口

在 `D:\Projects\intelligent-stock-selection-system` 项目中，优先使用已验证的项目入口：

```text
stock_selection/data/tushare_client.py
```

该入口已经包含项目可用的 `pro` client 和自定义 HTTP 地址设置。使用本项目进行 A 股数据采集、选股观察池、板块复盘或自动化任务时，先沿用这个入口，不要只检查 `TUSHARE_TOKEN` 环境变量。

注意：

- 不要在报告、日志或最终回答里输出真实 token。
- 如果该文件存在顶层示例调用，导入时可能触发示例输出；生成长期脚本时，建议封装为 `get_pro()` 或在脚本中复用同样初始化方式。

### 通用初始化

```python
import tushare as ts

pro = ts.pro_api("YOUR_TOKEN")
```

使用环境变量的写法：

```python
import os
import tushare as ts

pro = ts.pro_api(os.environ["TUSHARE_TOKEN"])
```

## 3. A 股股票池与交易日历

日常选股和复盘任务的推荐采集顺序见 `usage-strategy.md`。优先按交易日拉横截面，不要对全市场逐票深度循环。

### `stock_basic`

用途：获取 A 股股票基础信息。

常用入参：

- `exchange`：交易所，`""` 表示全部，常见值有 `SSE`、`SZSE`、`BSE`
- `list_status`：上市状态，`L` 上市，`D` 退市，`P` 暂停上市
- `ts_code`：可选，指定单只股票
- `fields`：可选，指定返回字段

示例：

```python
stocks = pro.stock_basic(
    exchange="",
    list_status="L",
    fields="ts_code,symbol,name,area,industry,market,list_date"
)
```

### `trade_cal`

用途：获取交易日历。

常用入参：

- `exchange`：通常用 `SSE`
- `start_date`、`end_date`：日期区间
- `is_open`：`1` 表示交易日，`0` 表示休市日

示例：

```python
cal = pro.trade_cal(exchange="SSE", start_date="20250101", end_date="20260608", is_open="1")
```

## 4. 行情与成交量

### `ts.pro_bar`

用途：获取可复权的行情数据，适合研究和回测。它是 Tushare SDK 封装函数，不是普通 Pro API endpoint。

常用入参：

- `api`：`pro` 客户端
- `ts_code`：股票代码
- `start_date`、`end_date`：日期区间
- `adj`：`qfq` 前复权，`hfq` 后复权，不传为原始价格
- `freq`：`D` 日线，`W` 周线，`M` 月线
- `limit`：最近 N 条

示例：

```python
bars = ts.pro_bar(api=pro, ts_code="000001.SZ", adj="qfq", freq="D", limit=250)
```

典型返回字段：

- `ts_code`、`trade_date`
- `open`、`high`、`low`、`close`、`pre_close`
- `change`、`pct_chg`
- `vol`、`amount`

### `daily`、`weekly`、`monthly`

用途：获取未复权的日线、周线、月线行情。

常用入参：

- `ts_code`
- `trade_date`
- `start_date`、`end_date`

示例：

```python
one_stock = pro.daily(ts_code="000001.SZ", start_date="20250101", end_date="20260608")
one_day = pro.daily(trade_date="20260608")
```

### `adj_factor`

用途：获取复权因子，适合自己计算复权价格。

常用入参：

- `ts_code`
- `trade_date`
- `start_date`、`end_date`

示例：

```python
adj = pro.adj_factor(ts_code="000001.SZ", start_date="20250101", end_date="20260608")
```

### `stk_limit`

用途：获取涨停价、跌停价，适合判断涨停、跌停、连板等逻辑。

常用入参：

- `ts_code`
- `trade_date`
- `start_date`、`end_date`

示例：

```python
limits = pro.stk_limit(trade_date="20260608")
```

### `suspend_d`

用途：识别停复牌股票。

常用入参：

- `ts_code`
- `trade_date`
- `start_date`、`end_date`

示例：

```python
suspend = pro.suspend_d(trade_date="20260608")
```

## 5. 每日估值与交易指标

### `daily_basic`

用途：获取横截面因子，例如估值、换手率、量比、市值。

常用入参：

- `ts_code`
- `trade_date`
- `start_date`、`end_date`
- `fields`

示例：

```python
basic = pro.daily_basic(
    trade_date="20260608",
    fields="ts_code,trade_date,turnover_rate,volume_ratio,pe,pb,total_mv,circ_mv"
)
```

常用字段：

- `turnover_rate`、`turnover_rate_f`
- `volume_ratio`
- `pe`、`pe_ttm`、`pb`、`ps`、`ps_ttm`
- `dv_ratio`、`dv_ttm`
- `total_share`、`float_share`、`free_share`
- `total_mv`、`circ_mv`

## 6. 财务与基本面

### 三大报表

接口：

- `income`：利润表
- `balancesheet`：资产负债表
- `cashflow`：现金流量表

常用入参：

- `ts_code`
- `ann_date`
- `start_date`、`end_date`
- `period`：报告期，例如 `20251231`
- `report_type`、`comp_type`

示例：

```python
income = pro.income(ts_code="000001.SZ", start_date="20240101", end_date="20260608")
balance = pro.balancesheet(ts_code="000001.SZ", period="20251231")
cashflow = pro.cashflow(ts_code="000001.SZ", period="20251231")
```

### `fina_indicator`

用途：获取 Tushare 已整理好的财务指标。

常用入参：

- `ts_code`
- `ann_date`
- `start_date`、`end_date`
- `period`

示例：

```python
fina = pro.fina_indicator(
    ts_code="000001.SZ",
    start_date="20240101",
    fields="ts_code,ann_date,end_date,roe,roa,grossprofit_margin,netprofit_margin,debt_to_assets"
)
```

常用因子字段：

- 盈利能力：`roe`、`roe_dt`、`roa`、`grossprofit_margin`、`netprofit_margin`
- 成长性：`or_yoy`、`netprofit_yoy`、`dtprofit_yoy`
- 质量/风险：`debt_to_assets`、`current_ratio`、`quick_ratio`

### 业绩与财务事件

接口：

- `forecast`：业绩预告
- `express`：业绩快报
- `dividend`：分红送股
- `fina_audit`：审计意见
- `fina_mainbz`：主营业务构成
- `disclosure_date`：财报披露计划

示例：

```python
forecast = pro.forecast(ts_code="000001.SZ", start_date="20250101", end_date="20260608")
express = pro.express(ts_code="000001.SZ", start_date="20250101", end_date="20260608")
dividend = pro.dividend(ts_code="000001.SZ")
```

## 7. 指数与行业

### `index_basic`

用途：查询指数代码。

常用入参：

- `market`：例如 `SSE`、`SZSE`、`CSI`、`SW`、`OTH`
- `publisher`
- `category`

示例：

```python
indexes = pro.index_basic(market="SSE")
```

### `index_daily`、`index_weekly`、`index_monthly`

用途：获取指数日线、周线、月线行情。

常用入参：

- `ts_code`
- `trade_date`
- `start_date`、`end_date`

示例：

```python
sh = pro.index_daily(ts_code="000001.SH", start_date="20250101", end_date="20260608")
```

### `index_weight`

用途：获取指数成分股和权重。

常用入参：

- `index_code`
- `trade_date`
- `start_date`、`end_date`

示例：

```python
weights = pro.index_weight(index_code="000300.SH", start_date="20250101", end_date="20260608")
```

### `index_dailybasic`

用途：获取指数估值和市场统计指标。

常用入参：

- `ts_code`
- `trade_date`
- `start_date`、`end_date`

示例：

```python
idx_basic = pro.index_dailybasic(ts_code="000001.SH", start_date="20250101", end_date="20260608")
```

### 申万行业

接口：

- `index_classify`：行业分类
- `index_member_all`：行业成分股
- `sw_daily`：申万行业日线

示例：

```python
sw_class = pro.index_classify(src="SW2021")
sw_members = pro.index_member_all(l1_code="801010.SI")
sw_daily = pro.sw_daily(trade_date="20260608")
```

常用入参：

- `src`：通常用 `SW2021`
- `index_code`、`l1_code`、`l2_code`、`l3_code`
- `trade_date`、`start_date`、`end_date`

## 8. 资金流、交易行为与风险事件

### 资金流

接口：

- `moneyflow`：个股资金流
- `moneyflow_hsgt`：沪深港通资金流
- `hk_hold`：北向持股

示例：

```python
mf = pro.moneyflow(ts_code="000001.SZ", start_date="20260601", end_date="20260608")
hsgt = pro.moneyflow_hsgt(start_date="20260601", end_date="20260608")
hk_hold = pro.hk_hold(trade_date="20260608")
```

### 融资融券

接口：

- `margin`：融资融券汇总
- `margin_detail`：个股融资融券明细

示例：

```python
margin = pro.margin(trade_date="20260608")
margin_detail = pro.margin_detail(trade_date="20260608")
```

### 龙虎榜

接口：

- `top_list`：龙虎榜每日明细
- `top_inst`：龙虎榜机构席位明细

示例：

```python
top = pro.top_list(trade_date="20260608")
inst = pro.top_inst(trade_date="20260608")
```

### 事件/风险接口

接口：

- `block_trade`：大宗交易
- `stk_holdernumber`：股东人数
- `stk_holdertrade`：股东增减持
- `pledge_stat`：股权质押统计
- `pledge_detail`：股权质押明细
- `repurchase`：股票回购
- `share_float`：限售股解禁

示例：

```python
blocks = pro.block_trade(trade_date="20260608")
holders = pro.stk_holdernumber(ts_code="000001.SZ")
holder_trade = pro.stk_holdertrade(ts_code="000001.SZ")
pledge = pro.pledge_stat(ts_code="000001.SZ")
unlock = pro.share_float(ts_code="000001.SZ", start_date="20250101", end_date="20260608")
```

## 9. 5000 积分下常见可用的跨资产接口

仅在用户策略确实需要时使用。

基金：

- `fund_basic`、`fund_company`、`fund_nav`、`fund_daily`、`fund_div`、`fund_portfolio`、`fund_adj`

期货：

- `fut_basic`、`fut_daily`、`fut_holding`、`fut_wsr`、`fut_settle`

期权：

- `opt_basic`、`opt_daily`

可转债：

- `cb_basic`、`cb_issue`、`cb_daily`

外汇与利率：

- `fx_obasic`、`fx_daily`
- `shibor`、`shibor_quote`、`shibor_lpr`、`libor`、`hibor`、`wz_index`、`gz_index`

## 10. 独立权限费用提示

当用户询问下面这些数据时，不要暗示 5000 积分足够；报价前要查看最新官方文档。

之前核对过的个人用户口径示例：

- 港股日线：单独按年开通
- 港股分钟/实时：单独权限
- 美股日线/财务：单独权限
- 新闻：单独按年开通
- 公告：单独按年开通
- 券商研报：单独按年开通

公司/机构用户的费用可能与个人账户不同。

## 11. 排查问题

接口返回权限错误时：

1. 先判断是否属于独立权限接口。
2. 再判断是否高于 5000 积分门槛。
3. 检查 token 是否配置在当前运行脚本的 Python 环境里。
4. 尝试只请求少量 `fields` 做最小化调用。
5. 检查单次返回行数限制，按 `trade_date`、日期区间或 `ts_code` 拆分请求。

数据里出现 `NaN` 时：

- 可能是上市首日、停牌日、交易日数据尚未更新、字段本身不可得，或者接口返回不完整。
- 不要在没有权限错误提示时直接判断为权限问题。

代码容易混淆时：

- `000001.SZ` 是平安银行。
- `000001.SH` 是上证指数。
- 同一个数字代码配不同后缀，代表不同标的。
