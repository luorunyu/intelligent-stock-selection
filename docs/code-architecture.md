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

## 数据契约：`stock_selection/core/`

| 文件 | 作用 |
| --- | --- |
| `core/schema.py` | 行情长表的统一字段名和必需列。 |
| `core/types.py` | 选股与回测结果的数据类容器。 |
| `core/validation.py` | 日线表字段、主键、价格和成交量校验，以及“截至某日”的防未来数据过滤。 |
| `core/__init__.py` | 核心包标识文件。 |

根目录 `stock_selection/__init__.py` 是项目包说明文件；它当前不再导出已删除的旧回测模块。

## 研究上下文：`stock_selection/context/`

| 文件 | 作用 |
| --- | --- |
| `context/multi_day_market.py` | 汇总多日市场宽度、行业统计和历史核心股票的行情验证。 |
| `context/report_context.py` | 从历史报告提取主题、股票、判断语句和关键词回溯。 |
| `context/relationship_map_context.py` | 解析历史关系地图 Markdown，建立公司、主题、产业链和状态索引。 |
| `context/theme_discovery.py` | 用全市场数据发现热点，输出行业热点、新主题、种子主题验证、角色和生命周期。 |
| `context/theme_rotation.py` | 对连续主题发现记录做时间线、状态转换和轮动候选分析。 |
| `context/theme_stock_roles.py` | 将热点内股票划分为龙头、中军、扩散、跟随、补涨、掉队和证伪角色。 |
| `context/__init__.py` | 上下文包标识文件。 |

## 总数据流

```text
Tushare 接口
  → tushare_client / limiter / collector
  → 正式缓存与 after_close manifest
  → prepare_after_close_context
  → theme_discovery + 历史报告/关系地图上下文
  → research_context
  → 观察池报告 + 股票关系地图报告 + 邮件
```

## 阅读顺序建议

1. 先读 `scripts/prepare_after_close_context.py`，理解盘后自动化的总入口。
2. 再读 `data/after_close.py` 和 `data/tushare_collector.py`，理解数据何时可用于正式报告。
3. 然后读 `context/theme_discovery.py`，理解热点为什么会被写入 `theme_discovery`。
4. 最后读 `context/multi_day_market.py`、`report_context.py`、`relationship_map_context.py`，理解报告如何结合近期数据与历史记录。
