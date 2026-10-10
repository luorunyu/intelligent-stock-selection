# 筛选方案二：可融券股票状态分类

方案二读取每日券商融券标的文件，保留融券状态正常且可用数量大于0的记录，然后复用策略五判断对应股票的最新状态。

## 标准数据目录

```text
data_cache/securities_lending/daily/YYYYMMDD.xls
```

当前券商文件虽然使用 `.xls` 扩展名，实际格式是GBK编码、Tab分隔文本。

## 分类规则

- 做空范围：`HIGH_CANDIDATE`、`HIGH_CONFIRMED`、`PEAK_1`、`PEAK_2`、`PEAK_3`、`PEAK_N`。
- 非做空范围：`NORMAL`、`HIGH_DRAWDOWN`、`TROUGH_WAIT_REBOUND`以及各波峰结束后的回落状态。
- 策略状态异常：`INVALID_HIGH_NEW_HIGH`。
- 无法判断：非股票、缺少日线、历史数据不足或未生成策略状态。

默认要求融券日期与本地最新股票日线日期一致。行情落后时会停止运行；只有显式指定 `--allow-stale-market-data` 才允许使用较早行情，并在结果中标记。

## 运行

```powershell
python scripts/run_screening_plan_two.py `
  --date 20261009 `
  --write-report
```

当前本地日线尚未更新到2026年10月9日时，可仅用于检查流程：

```powershell
python scripts/run_screening_plan_two.py `
  --date 20261009 `
  --allow-stale-market-data `
  --write-report
```

结果保存到：

```text
analysis_records/screening_plan_two/YYYY-MM/
```
