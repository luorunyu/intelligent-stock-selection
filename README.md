# 申万行业趋势研究数据底座

当前分支只保留个股日K和申万行业数据，不包含热点发现、观察池、关系地图或报告生成逻辑。

## 数据范围

日频缓存：

- `daily`：全市场个股未复权日K。
- `sw_daily`：申万行业日K。
- `trade_cal`：交易日确认。

静态缓存：

- `stock_basic`：A股基础信息。
- `index_classify`：SW2021 一级、二级、三级行业目录。
- `index_member_all`：股票与申万行业的归属关系。

按需接口：

- `pro_bar`：单只股票前复权、后复权或未复权日K。

## 采集

```powershell
python scripts/collect_tushare_daily.py static
python scripts/collect_tushare_daily.py daily --date latest
```

历史回填：

```powershell
python scripts/collect_tushare_daily.py backfill --start 20260101 --end 20260914
```

## 读取

```python
from stock_selection.data.market_data import (
    load_stock_daily,
    load_sw_daily,
)
from stock_selection.data.industry_taxonomy import (
    load_sw_catalog,
    load_sw_industry_membership,
)

stock_bars = load_stock_daily(
    "000001.SZ",
    start_date="2026-01-01",
    end_date="2026-09-14",
)

sw_l3_catalog = load_sw_catalog(level="L3")
stock_membership = load_sw_industry_membership().data
sw_l3_bars = load_sw_daily(level="L3", start_date="2026-01-01")
```

缓存默认写入 `data_cache/tushare/`，真实 Token 只通过 `TUSHARE_TOKEN` 环境变量读取。

需要单股复权日K时，直接使用 `stock_selection.data.tushare_client.call_pro_bar`。
