# 策略四：个股高点回落反弹

策略四按交易日输出状态，用于识别高点有效回落后的反弹阶段，并保留历史上每个交易日的判断结果。

## 默认规则

- 高点回看窗口：120个交易日，只使用当前日之前的数据；
- 有效回落：相对参考高点下跌至少10%；
- 反弹开启：从回落低点上涨，并达到参考高点价格的70%；
- 反弹结束：相对本轮反弹最高收盘价回落至少10%；
- 结束状态：默认保留10个交易日，之后恢复为空状态；
- 突破前高：输出 `BREAK_PREVIOUS_HIGH` 告警，并终止原周期。

只有两个非空状态：

- `REBOUND_ACTIVE`：高点回落后反弹中；
- `REBOUND_ENDED`：反弹结束。

不属于这两个阶段的交易日仍会输出，但状态字段为空。

## 生成逐日状态

```powershell
python scripts/screen_stock_peak_pullback_rebound.py `
  --date 20261010 `
  --write-report
```

默认结果目录：

```text
stock_selection/strategies/stock_peak_pullback_rebound/output/2026-10-10/
```

其中：

- `states.csv`：每只股票、每个交易日的状态；
- `report.json`：参数、汇总、最新状态和告警；
- `report.md`：便于人工查看的汇总。

可以通过 `--stock-code 000001.SZ` 只计算指定股票。

## 生成K线图

先在以下文件中填写股票代码，每行一个：

```text
stock_selection/strategies/stock_peak_pullback_rebound/strategy_four_chart_codes.txt
```

然后手动运行：

```powershell
python scripts/plot_stock_peak_pullback_rebound_states.py --date 20261010
```

图表默认输出到：

```text
stock_selection/strategies/stock_peak_pullback_rebound/output/2026-10-10/charts/
```

图表中保留红涨绿跌的蜡烛图：

- 黄色背景：高点回落后反弹中；
- 蓝色背景：反弹结束；
- 紫色叉号：突破前高告警。
