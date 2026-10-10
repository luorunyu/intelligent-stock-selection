# 策略五：高位做空状态

该策略只使用前复权收盘价，为每只股票的每个交易日标记高位状态，不选择具体做空点。

默认参数：

- 半年窗口：120个交易日；
- 最高点确认：从运行最高收盘价回落30%；
- 波峰开启：从当前波谷反弹10%；
- 波峰结束：收盘价回落到波峰开始价格；
- 最高点热度：当前收盘价相对前10个交易日均价的涨幅；
- 波峰热度：当前收盘价相对波谷日前5个交易日均价的涨幅。

主要状态使用 `StockShortState` 整数枚举，同时输出枚举名称和中文描述。确认最高点后如果再次突破原最高收盘价，会输出 `INVALID_HIGH_NEW_HIGH` 告警状态。

直接运行：

```powershell
python scripts/screen_stock_short_high_state.py --date 20260930
```

保存完整逐日状态 CSV、JSON 摘要和 Markdown 报告：

```powershell
python scripts/screen_stock_short_high_state.py `
  --date 20260930 `
  --write-report
```

只处理指定股票：

```powershell
python scripts/screen_stock_short_high_state.py `
  --date 20260930 `
  --stock-code 000001.SZ
```

#
todo :后续可能还要考虑板块情况