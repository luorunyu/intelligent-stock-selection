# 代码结构说明

本文覆盖当前 `scripts/` 和 `stock_selection/` 下仍存在的 Python 文件。执行入口在 `scripts/`；可复用业务代码在 `stock_selection/`。

## 执行入口：`scripts/`

| 文件 | 作用 | 主要输出 |
| --- | --- | --- |
| `backfill_tmp_automation_cache.py` | 将旧自动化遗留 CSV 迁移到正式缓存。 | `data_cache/tushare/` 下的 Parquet。 |
| `build_after_close_research_context.py` | 合并多日行情、历史报告和热点发现，供盘后报告阅读。 | `analysis_records/research_context/...md`。 |
| `collect_tushare_daily.py` | 采集日频或静态 Tushare 数据。 | 正式缓存与运行日志。 |
| `discover_active_themes.py` | 从正式缓存独立扫描当日热点。 | 控制台 JSON；指定 `--write` 时写主题记录。 |
| `prepare_after_close_context.py` | 盘后主入口：补齐正式缓存、发现热点、输出报告上下文。 | 控制台 JSON 与 `theme_discovery/...json`。 |
| `prepare_after_close_data.py` | 只执行盘后缓存准备，不构建报告上下文。 | `after_close_*.json` manifest。 |
| `prepare_preopen_context.py` | 盘前只读：读取上一正式盘后基线和报告路径。 | 控制台 JSON，指出盘前报告应保存的位置。 |
| `probe_tushare_apis.py` | 试调接口并缓存当前令牌的可用性。 | `_metadata/available_apis.json`。 |
| `plot_industry_klines.py` | 从正式缓存加载申万行业和流通市值最大的成分股。 | 共享时间轴的交互式日 K HTML。 |
| `run_screening_plan_one.py` | 方案一薄入口：先筛选 L2 行业，再筛选行业内同方向个股。 | 行业-个股 CSV、嵌套 JSON、Markdown 报告。 |
| `screen_industry_index.py` | 第一筛选策略：统计申万行业指数从近期低点反弹的幅度和持续性。 | 行业分级 JSON、Markdown 报告。 |
| `screen_stock_industry_direction.py` | 第二筛选策略的薄入口：计算个股与所属申万二级或三级行业的同方向比例。 | 完整结果 CSV、筛选 JSON、Markdown 报告。 |
| `refresh_sw_industry_stock_map.py` | 按申万一级行业分批拉取完整成分关系。 | 一、二、三级行业到当前上市股票代码的 JSON 字典。 |
| `query_sw_industry_stocks.py` | 按申万行业代码或唯一行业名查询。 | 行业层级、父子节点和全部当前上市股票代码 JSON。 |

## 数据、缓存与通信：`stock_selection/data/`、`tools/`

| 文件 | 作用 |
| --- | --- |
| `data/after_close.py` | 盘后正式数据管线：交易日回退、必需接口就绪性重试、manifest 和报告数据路径。 |
| `data/trading_calendar.py` | 从正式缓存反推可用交易日和 T+ 偏移，不依赖自然日。 |
| `data/tushare_cache.py` | Parquet/CSV 兼容读取、正式缓存路径和 JSON/JSONL 审计日志。 |
| `data/tushare_client.py` | `.env` 加载、Tushare 客户端复用、限流、重试和安全调用审计。 |
| `data/tushare_collector.py` | 将接口注册表转成可采集任务，处理缓存跳过、失败记录和静态/日频/按需模式。 |
| `data/tushare_limiter.py` | 线程安全滑动窗口限流器。 |
| `data/tushare_permissions.py` | 探测令牌权限，减少后续无权限调用。 |
| `data/tushare_registry.py` | 声明各接口频率、缓存方式和默认最小参数。 |
| `tools/send_text_email.py` | 读取报告文本并以 SMTP 发送邮件；密码仅从环境变量读取。 |
| `data/__init__.py`、`tools/__init__.py` | 包标识文件；当前不承载业务逻辑。 |

## 可视化：`stock_selection/visualization/`

| 文件 | 作用 |
| --- | --- |
| `visualization/industry_kline.py` | 将行业指数和多只成分股按流通市值降序排成等高面板，同步缩放共享时间轴，输出单个离线 HTML。 |

## 数据契约：`stock_selection/core/`

| 文件 | 作用 |
| --- | --- |
| `core/schema.py` | 行情长表的统一字段名和必需列。 |
| `core/types.py` | 选股与回测结果的数据类容器。 |
| `core/validation.py` | 日线表字段、主键、价格和成交量校验，以及“截至某日”的防未来数据过滤。 |
| `core/__init__.py` | 核心包标识文件。 |

根目录 `stock_selection/__init__.py` 是项目包说明文件；它当前不再导出已删除的旧回测模块。

## 独立筛选策略：`stock_selection/strategies/`

每个策略占用一个目录，内部统一使用 `screen.py`、`report.py`、`cli.py` 和 `README.md`。策略之间不直接依赖，后续多策略组合由单独的编排层完成。

| 目录 | 作用 |
| --- | --- |
| `strategies/industry_index_rebound/` | 第一策略：计算申万行业指数从近期低点反弹的幅度和持续性。 |
| `strategies/stock_industry_direction/` | 第二策略：只计算个股与所属 L2/L3 行业指数的同涨同跌比例。零涨跌日不进入比例分母。 |

## 筛选方案：`stock_selection/screening_schemes/`

筛选方案只负责编排策略单元，不在组合层重复实现指标。以后可以继续增加 `plan_two/` 等目录。

| 目录 | 作用 |
| --- | --- |
| `screening_schemes/plan_one/` | 先用策略一筛选 L2 行业，再用策略二筛选这些行业的成分股，按行业强度和个股同方向比例输出。 |

## 研究上下文：`stock_selection/context/`

| 文件 | 作用 |
| --- | --- |
| `context/multi_day_market.py` | 汇总多日市场宽度、行业统计和历史核心股票的行情验证。 |
| `context/report_context.py` | 从历史报告提取主题、股票、判断语句和关键词回溯。 |
| `context/relationship_map_context.py` | 解析历史关系地图 Markdown，建立公司、主题、产业链和状态索引。 |
| `context/theme_discovery.py` | 用全市场数据和 Tushare SW2021 申万一级、二级、三级映射发现正式行业热点与未命名动态共振簇；不维护主题种子或预设股票。 |
| `data/industry_taxonomy.py` | 分批刷新完整成分关系，维护申万一、二、三级行业到当前上市股票代码的字典，并生成每只股票的三级映射。 |
| `data/sw_industry_dictionary.py` | 从 SW2021 分类、成分和当前上市股票构建一级、二级、三级行业成分大字典。 |
| `context/theme_rotation.py` | 对连续主题发现记录做时间线、状态转换和轮动候选分析。 |
| `context/theme_stock_roles.py` | 将热点内股票划分为龙头、中军、扩散、跟随、补涨、掉队和证伪角色。 |
| `context/__init__.py` | 上下文包标识文件。 |

## 总数据流

```text
Tushare 接口
  → tushare_client / limiter / collector
  → 正式缓存
    → strategies/industry_index_rebound → 策略一独立报告
    → strategies/stock_industry_direction → 策略二独立报告
    → screening_schemes/plan_one → 策略一 + 策略二行业-个股报告
    → after_close manifest → prepare_after_close_context
      → theme_discovery + 历史报告/关系地图上下文
      → research_context
      → 观察池报告 + 股票关系地图报告 + 邮件
```

## 阅读顺序建议

1. 先读 `scripts/prepare_after_close_context.py`，理解盘后自动化的总入口。
2. 再读 `data/after_close.py` 和 `data/tushare_collector.py`，理解数据何时可用于正式报告。
3. 然后读 `context/theme_discovery.py`，理解热点为什么会被写入 `theme_discovery`。
4. 最后读 `context/multi_day_market.py`、`report_context.py`、`relationship_map_context.py`，理解报告如何结合近期数据与历史记录。
