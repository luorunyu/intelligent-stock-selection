# 策略一：行业指数近期低点反弹

该策略只判断申万行业指数从最近窗口低点反弹的幅度和持续性，不负责个股筛选。

- `screen.py`：指标计算和阈值筛选。
- `report.py`：申万一、二、三级层级报告。
- `cli.py`：命令行参数和执行流程。

运行入口：

```bash
./.venv/bin/python scripts/screen_industry_index.py \
  --date 20260918 \
  --window 20 \
  --write-report
```
