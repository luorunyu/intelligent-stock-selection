# 筛选策略目录

每个策略使用一个独立目录，策略之间不直接调用。多个策略的组合位于 `stock_selection/screening_schemes/`，通过各策略的结果表进行连接。

```text
strategies/
├── industry_index_rebound/       # 策略一：行业指数低点反弹
│   ├── screen.py                 # 计算和筛选
│   ├── report.py                 # 报告构建和保存
│   ├── cli.py                    # 命令行流程
│   └── README.md                 # 策略口径
└── stock_industry_direction/     # 策略二：个股与行业同方向比例
    ├── screen.py
    ├── report.py
    ├── cli.py
    └── README.md
```

新增第三个策略时，按相同结构增加一个新目录，不要把计算逻辑放进已有策略。
