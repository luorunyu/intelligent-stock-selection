# 策略四：个股高点回落反弹

该策略只使用收盘价，扫描历史上符合以下条件的形态：

- 当前点前后各120个交易日没有更高收盘价；
- 高点后最多40个交易日内，收盘价至少回落10%；
- 回落低点之后，收盘价重新达到前高收盘价的70%；
- 同一只股票可以返回多个历史形态。

```powershell
python scripts/screen_stock_peak_pullback_rebound.py `
  --date 20260930 `
  --write-report
```

可以通过 `--stock-code 000001.SZ` 限定股票。
