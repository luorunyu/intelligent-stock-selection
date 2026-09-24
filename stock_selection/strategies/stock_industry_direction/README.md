# 策略二：个股与行业同方向比例

该策略只计算一个核心指标：个股与所属申万二级或三级行业指数的日涨跌同方向比例。

## 计算方法

在最近 `window` 个已对齐交易日内：

- 个股和行业同涨，计为同方向。
- 个股和行业同跌，计为同方向。
- 一个上涨、一个下跌，计为反方向。
- 任意一方涨跌幅为零，计为中性日，不进入比例分母。

```text
同方向比例 = 同方向天数 / (同方向天数 + 反方向天数)
```

目录文件：

- `screen.py`：缓存读取、行业成分展开、比例计算和阈值筛选。
- `report.py`：CSV、JSON 和 Markdown 报告。
- `cli.py`：命令行参数和执行流程。

运行航运三级行业示例：

```bash
./.venv/bin/python scripts/screen_stock_industry_direction.py \
  --date 20260918 \
  --level L3 \
  --industry-code 851761.SI \
  --window 20 \
  --write-report
```
