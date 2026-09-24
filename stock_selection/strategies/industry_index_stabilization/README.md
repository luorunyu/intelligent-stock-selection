# 策略三：申万L2/L3行业指数企稳反转历史验证

该策略扫描指定层级申万行业指数的全部历史交易日，而不是只判断最新交易日。
`--level` 支持 `L2` 和 `L3`，默认是 `L2`。

## 信号规则

- 下跌趋势：下跌持续至少20个交易日，或者从近期高点到低点的跌幅达到30%。
- 企稳：连续至少5个交易日内，全部开盘价和收盘价位于宽度不超过10点的价格带。
- 一个连续企稳阶段只在首次满足条件时产生一个信号。

## 有效性验证

- 默认观察信号后的60个交易日。
- 如果后续任一收盘价较信号日收盘价上涨至少40%，信号记为 `success`。
- 已有完整60日数据但未达到40%，记为 `failed`。
- 历史数据不足60日且尚未达到40%，记为 `pending`。

信号判断只使用信号日及之前的数据，未来行情只用于验证，避免未来数据泄漏。

```powershell
python scripts/screen_industry_index_stabilization.py `
  --date 20260924 `
  --level L2 `
  --write-report
```

扫描L3行业：

```powershell
python scripts/screen_industry_index_stabilization.py `
  --date 20260924 `
  --level L3 `
  --write-report
```
