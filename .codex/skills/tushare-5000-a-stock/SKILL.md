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
4. 在本项目中，优先读取并沿用 `stock_selection/data/tushare_client.py` 里的 Tushare 初始化方式，包括项目已验证的 `pro` client 和自定义 HTTP 地址。不要把 token 写入报告、日志或最终回答。
5. 涉及批量选股、每日复盘或自动化任务时，先读取 `references/usage-strategy.md`，按 5000 积分和分钟频率友好的方式调用接口。
6. 如果当前项目没有可用 client，再使用环境变量或占位 token 方式：

```python
import tushare as ts

pro = ts.pro_api("YOUR_TOKEN")
```

7. 用户需要可用于研究/回测的复权行情时，优先使用 `ts.pro_bar(api=pro, ...)`。
8. 普通 Pro 接口使用 `pro.<api_name>(...)`。
9. 说明常见限制：很多接口有单次返回行数限制，历史数据通常要按日期或股票代码分页拉取，交易日数据可能在盘后才完整更新。

## 本项目数据入口

当前项目已提供并测试过 Tushare client：

```text
stock_selection/data/tushare_client.py
```

使用本项目做数据采集、行情分析、选股观察池或复盘时，先检查这个文件的初始化写法，并复用其中的 `pro` client。该文件可能包含示例调用或顶层打印；如果只是生成长期脚本，优先把初始化封装为函数或在新脚本中复用同样的 client 构造方式，避免导入时触发无关示例输出。

安全规则：

- 不要在回答、报告、日志、归档文件或示例代码里输出真实 token。
- 如果需要展示代码，使用 `YOUR_TOKEN`、环境变量或说明“沿用项目 client”，不要复制项目里的真实 token。
- 保留项目 client 中的自定义 HTTP 地址设置，除非用户明确要求改回官方默认地址。

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

做横截面因子、每日观察池和收盘后复盘时，优先按 `trade_date` 拉取全市场数据，再筛候选池：

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
- `references/usage-strategy.md`：5000 积分和分钟调用限制下的批量拉取、缓存、候选池深挖和自动化任务调用策略。
