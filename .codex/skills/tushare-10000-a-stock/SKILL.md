---
name: tushare-10000-a-stock
description: 在本项目中采集、缓存、读取或排查 Tushare Pro 个人 10000 积分账户下的 A 股个股日K、申万行业日K、SW2021 一级/二级/三级目录和股票行业归属。用户要求每日数据缓存、历史回填、个股行情读取、申万行业数据读取、缓存完整性检查、接口权限排查或相关代码修改时使用；当前分支只使用 trade_cal、daily、sw_daily、stock_basic、index_classify、index_member_all 和 pro_bar。
---

# Tushare 个股与申万行业数据

## 固定范围

只使用以下接口：

```text
日频：trade_cal、daily、sw_daily
静态：stock_basic、index_classify、index_member_all
按需：pro_bar
派生：sw_industry_membership
```

不要在当前分支引入资金流、龙虎榜、融资融券、财务、公告、新闻、研报或跨资产接口。

## 工作流

1. 优先读取 `data_cache/tushare/`，不要隐式访问网络。
2. 需要接口字段或参数时读取 `references/api-reference.md`。
3. 需要初始化、每日增量或历史回填时读取 `references/usage-strategy.md`。
4. 所有网络调用必须经过：
   - `stock_selection/data/tushare_client.py`
   - `stock_selection/data/tushare_collector.py`
5. 不要绕过项目限流、重试、自定义 HTTP 地址和安全日志。
6. 不要输出或写入真实 `TUSHARE_TOKEN`。

## 标准命令

首次或定期刷新静态数据：

```powershell
python scripts/collect_tushare_daily.py static
```

采集最新有效交易日：

```powershell
python scripts/collect_tushare_daily.py daily --date latest
```

回填历史区间：

```powershell
python scripts/collect_tushare_daily.py backfill --start 20260101 --end 20260914
```

权限探测：

```powershell
python scripts/probe_tushare_apis.py --date latest
```

## 完整性规则

- 每日研究日期必须同时存在 `daily` 和 `sw_daily`。
- `daily` 或 `sw_daily` 任一缺失时，不得把该日期视为完整日期。
- `index_classify` 必须使用 `src=SW2021`。
- `index_member_all` 是股票申万归属的唯一正式来源。
- 静态原始表更新后必须重建 `sw_industry_membership`。
- 缺失申万映射时保留空值和警告，不得退回 `stock_basic.industry`。
- 权限探测只证明接口可调用，不证明静态全量数据已经缓存。

## 读取入口

```python
from stock_selection.data import (
    load_stock_daily,
    load_sw_catalog,
    load_sw_daily,
    load_sw_industry_membership,
)

stock_daily = load_stock_daily(
    "000001.SZ",
    start_date="20260101",
    end_date="20260914",
)

sw_l3 = load_sw_catalog(level="L3")
membership = load_sw_industry_membership().data
industry_daily = load_sw_daily(
    level="L3",
    start_date="20260101",
    end_date="20260914",
)
```

需要单股复权行情时直接使用项目客户端：

```python
from stock_selection.data.tushare_client import call_pro_bar

bars = call_pro_bar(
    {
        "ts_code": "000001.SZ",
        "adj": "qfq",
        "freq": "D",
        "start_date": "20260101",
        "end_date": "20260914",
    }
)
```

## 安全边界

- Token 只从环境变量或项目环境文件读取。
- 不在报告、日志、示例或最终回答中显示真实 Token。
- 不把公司文件、缓存或数据上传到第三方公网服务。
- 接口无权限时记录失败原因，不尝试绕过。

## 参考文件

- `references/api-reference.md`：当前分支7个接口的参数、字段和缓存规则。
- `references/usage-strategy.md`：初始化、每日增量、历史回填和静态刷新流程。
