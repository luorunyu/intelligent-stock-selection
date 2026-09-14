# 当前分支 Tushare 接口参考

## 接口总览

| 接口 | 用途 | 缓存 | 频率 |
| --- | --- | --- | --- |
| `trade_cal` | 确认交易日 | `trade_cal/YYYYMMDD.parquet` | 每日 |
| `daily` | 全市场个股未复权日K横截面 | `daily/YYYYMMDD.parquet` | 每日 |
| `sw_daily` | 申万行业日K横截面 | `sw_daily/YYYYMMDD.parquet` | 每日 |
| `stock_basic` | 上市A股基础信息 | `static/stock_basic.parquet` | 低频 |
| `index_classify` | SW2021行业目录 | `static/index_classify.parquet` | 低频 |
| `index_member_all` | 股票申万L1/L2/L3归属 | `static/index_member_all.parquet` | 低频 |
| `pro_bar` | 单股复权日K | 按需 | 按需 |

## `trade_cal`

正式参数：

```python
{
    "exchange": "SSE",
    "start_date": "20260914",
    "end_date": "20260914",
}
```

必需字段：

```text
exchange, cal_date, is_open
```

## `daily`

按交易日批量获取全市场个股日K：

```python
daily = pro.daily(trade_date="20260914")
```

必需字段：

```text
ts_code, trade_date, open, high, low, close, vol, amount
```

缓存价格为未复权价格。全市场历史应按交易日回填，不应逐票调用 `pro_bar`。

## `sw_daily`

按交易日批量获取申万行业日K：

```python
industry_daily = pro.sw_daily(trade_date="20260914")
```

必需字段：

```text
ts_code, trade_date, open, high, low, close
```

需要结合 `index_classify` 判断行业属于L1、L2还是L3。

## `stock_basic`

```python
stocks = pro.stock_basic(
    exchange="",
    list_status="L",
    fields="ts_code,symbol,name,area,industry,market,list_date,delist_date",
)
```

`stock_basic.industry` 不是申万三级正式归属，不能替代 `index_member_all`。

## `index_classify`

```python
catalog = pro.index_classify(src="SW2021")
```

必需字段：

```text
index_code, industry_name, level, industry_code, parent_code, src
```

正式缓存只接受 `src=SW2021`，并保留L1/L2/L3父子层级。

## `index_member_all`

正式采集：

```python
members = pro.index_member_all()
```

权限探测：

```python
sample = pro.index_member_all(l1_code="801010.SI")
```

必需字段：

```text
l1_code, l1_name, l2_code, l2_name,
l3_code, l3_name, ts_code
```

权限探测结果只用于确认接口可用，不能替代完整静态缓存。

## `pro_bar`

单股前复权日K：

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

常用参数：

- `adj="qfq"`：前复权。
- `adj="hfq"`：后复权。
- 不传 `adj`：原始价格。
- `freq="D"`：日线。

不要使用 `pro_bar` 对全市场逐票回填。

## 项目入口

所有普通接口调用通过：

```text
stock_selection/data/tushare_client.py
stock_selection/data/tushare_collector.py
scripts/collect_tushare_daily.py
```

Token 使用 `TUSHARE_TOKEN`，不要写入代码、缓存、日志或回答。
