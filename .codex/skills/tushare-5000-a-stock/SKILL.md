---
name: tushare-5000-a-stock
description: 当 Codex 需要在 Tushare Pro 个人 5000 积分账户范围内处理 A 股选股、数据采集、因子研究、回测数据拉取或权限排查时使用。用于选择可用接口，避开港股、美股、新闻、公告、研报等独立权限数据，并生成包含合适入参的 Python tushare/pro_bar/pro 接口调用代码。
---

# Tushare 5000 积分 A 股数据

## 核心规则

把 5000 积分视为：足够使用官方积分表中大多数 A 股日线、指数、行业、财务、资金流、事件、基金、期货、期权、债券、外汇和宏观类常规接口；但不自动包含独立权限数据。

当用户需求涉及以下数据时，必须提醒这是独立权限或可能需要更高权限：

- 港股、美股、新闻、公告、研报
- A 股历史分钟、实时分钟、实时日线
- 港美股财务、实时行情、政策库、部分高级因子/预测数据

如需确认最新权限，以 Tushare 官方文档为准：

- 权限说明：`https://tushare.pro/document/2?doc_id=108`
- 积分与频次权限表：`https://tushare.pro/document/1?doc_id=290`

## 使用流程

1. 先判断用户要的数据类型：行情、估值、财务、指数、行业、资金流、事件/风险，还是跨资产数据。
2. 需要接口名、入参模板或字段说明时，读取 `references/api-reference.md`。
3. 优先选择 5000 积分下稳定可用的 A 股常规接口，再考虑独立权限数据。
4. 生成代码时优先沿用项目里已有的 token/client 写法；如果没有，就使用：

```python
import tushare as ts

pro = ts.pro_api("YOUR_TOKEN")
```

5. 用户需要可用于研究/回测的复权行情时，优先使用 `ts.pro_bar(api=pro, ...)`。
6. 普通 Pro 接口使用 `pro.<api_name>(...)`。
7. 说明常见限制：很多接口有单次返回行数限制，历史数据通常要按日期或股票代码分页拉取，交易日数据可能在盘后才完整更新。

## 常用入参

统一使用这些参数含义：

- `ts_code`：证券代码，例如 `000001.SZ`、`600000.SH`、`000001.SH`
- `trade_date`：单个交易日，格式 `YYYYMMDD`
- `start_date`、`end_date`：日期区间，格式 `YYYYMMDD`
- `limit`：`pro_bar` 最近 N 条记录
- `adj`：复权方式，`qfq` 前复权，`hfq` 后复权，不传或 `None` 为不复权
- `freq`：周期，`D` 日线，`W` 周线，`M` 月线
- `fields`：限制返回字段，多个字段用英文逗号拼接

## 代码默认选择

股票策略研究默认使用 `qfq` 前复权价格，除非用户明确要求原始价格。

做横截面因子时，优先按 `trade_date` 拉取全市场数据：

```python
daily_basic = pro.daily_basic(trade_date="20260608")
moneyflow = pro.moneyflow(trade_date="20260608")
```

做单只股票历史序列时，优先按 `ts_code` 加日期区间拉取：

```python
bars = ts.pro_bar(
    api=pro,
    ts_code="000001.SZ",
    adj="qfq",
    start_date="20250101",
    end_date="20260608",
)
```

## 参考文件

- `references/api-reference.md`：5000 积分友好的接口目录、入参、示例调用和独立权限排除项。
