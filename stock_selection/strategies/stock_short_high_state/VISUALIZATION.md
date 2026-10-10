# 策略五状态图表

策略五的输入、状态结果和图表统一放在本策略目录下。

## 1. 生成状态结果

```powershell
python scripts/screen_stock_short_high_state.py `
  --date 20260924 `
  --write-report
```

默认输出：

```text
stock_selection/strategies/stock_short_high_state/output/2026-09-24/
├── states.csv
├── report.json
└── report.md
```

## 2. 填写需要检查的股票

编辑：

```text
stock_selection/strategies/stock_short_high_state/strategy_five_chart_codes.txt
```

一行一个代码，支持 `000001` 或 `000001.SZ`，空行和 `#` 注释会被忽略。

## 3. 手动生成图表

```powershell
python scripts/plot_stock_short_high_states.py --date 20260924
```

图表输出到：

```text
stock_selection/strategies/stock_short_high_state/output/2026-09-24/charts/
```

可视化程序只读取已经保存的 `states.csv`，不会重新运行策略五。K线保持红涨绿跌，可做空区域使用半透明黄色背景，异常状态使用紫色叉号。
