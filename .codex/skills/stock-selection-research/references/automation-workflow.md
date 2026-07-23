# 自动化工作流

## 收盘后观察池复盘

默认时间：A 股交易日 16:30，中国时区。

定位：收盘后任务是正式行情缓存生产者，也是当天观察池和股票关系地图的主报告生成者。

执行入口：

```text
python scripts/prepare_after_close_context.py --date YYYYMMDD
python scripts/build_after_close_research_context.py --date <after_close.trade_date> --lookback 5
```

第一步会先调用正式缓存生产逻辑，并输出报告生成所需 JSON 上下文；第二步会读取最近正式 Tushare 缓存和最近历史报告，生成多日研究上下文，供 skill 判断板块/主题延续性。

数据规则：
- 先确认今天是否为 A 股交易日；如果当日 `daily/daily_basic` 尚未落库，使用最近一个完整有效交易日，并在报告中写明请求日期和实际交易日。
- Tushare 结构化数据必须写入正式缓存目录 `data_cache/tushare/<api>/<trade_date>.parquet`，不要写入 `analysis_records/_tmp_automation_*` 作为长期数据源。
- 盘后报告读取 `after_close.manifest_path` 指向的 `data_cache/tushare/_metadata/after_close_YYYYMMDD.json` 作为本次数据清单。
- 盘后报告使用 `after_close.trade_date` 作为最新有效交易日，使用 `after_close.dataset_paths` 里的正式缓存路径读取结构化数据。
- 生成报告前必须读取 `analysis_records/research_context/YYYY-MM/YYYY-MM-DD.md`；不能只依据历史报告文字判断板块延续性，必须结合最近多日正式 Tushare 行业/个股统计。
- `research_context` 中的“最近报告线索”只作为最新判断入口；对当天最强主线和子线，必须继续读取“历史报告关键词回溯”，默认回溯最近 30 天。该回溯只提供历史报告原文命中段落，不替代最终判断。
- 如果 `after_close.requested_date` 与 `after_close.trade_date` 不一致，报告开头必须写明请求日期、实际使用交易日和回退原因。
- 可用字段包括行情、成交、换手、资金流、涨跌停、龙虎榜、融资融券、申万行业、指数、主营业务构成等；缺失字段必须标注缺口，不要编造。
- 股票池和关系地图可以用新闻、公告、公司源、行业源、海外来源解释催化，但新闻事实和行情数据要分开写。
- 盘后必须复核盘前提出的海外映射：哪些被 A 股成交、涨跌幅、宽度和核心股承接确认，哪些只是隔夜情绪，哪些被证伪或需要降级。

报告规则：
- 读取最近 5 篇 `analysis_records/sector_analysis`、`analysis_records/stock_selection`、`analysis_records/stock_relationship_map`，检查历史观察池验证、历史地图更新、之前遗漏、之前错误和公司信息变化。
- 对当天最强 1-3 个主线及其子线执行历史报告关键词回溯，默认最近 30 天；每条主线必须基于历史命中段落输出“主题历史路径”，说明启动、强化、分化、退潮、修复、证伪中能被历史报告支持的关键节点。
- 如果当天是修复行情，必须说明它是首次修复、二次修复，还是退潮后的弱反抽；例如 PCB、CPO、半导体设备材料不能只看最近 5 篇报告，要回查其上一轮主线、第一次退潮和本次修复路径。
- 使用 `market-regime.md` 输出市场环境门控：指数、成交额、市场宽度、涨停跌停、短线情绪、板块扩散、外部扰动，以及当前状态：进攻 / 观察 / 防守 / 退潮。
- 识别热门板块、题材、龙头、成交额核心、快速跟随、补涨观察、待扩散观察和未启动但相关候选；同组股票必须说明映射关系和失效条件。
- 对候选股给出观察分层、评分、证据、风险标签、观察条件、剔除条件和信息缺口。
- 输出“隔夜映射盘后验证”：逐条检查盘前美股/海外映射到 A 股主线和子线后的结果，标注确认 / 部分确认 / 未确认 / 证伪，并写清 A 股验证依据。
- 对当天最强 1-3 个主题，读取 `stock-sector-analysis/references/stock-relationship-map.md`，生成独立股票关系地图报告。
- 股票关系地图必须按“主线 -> 子线 -> 产业链环节 -> 相关公司 -> 证据与验证”组织，先拆主线、子线、产业链环节，再放公司；不得把主线、子线、静态行业和产业链环节用斜杠混写成同一层级。
- 股票关系地图必须输出“今日主线学习卡”，说明主线定义、市场别名、最强子线、掉队子线、不应混淆的标签、对应静态行业、后续验证条件和失效条件。
- 股票关系地图必须输出“子线拆解”和“自选标签建议”，帮助把报告中的主线、子线和用户自选软件标签对应起来。
- 股票关系地图需要拆分上游、中游、下游、配套服务，输出已异动、待扩散、未启动、掉队、证伪公司。
- 每家代表公司说明主营业务、产品、客户、上游、下游、题材、催化、财务验证、市场验证和信息缺口。
- 如发现公司信息更新、之前遗漏、之前错误、关系强弱调整或被证伪，必须在“历史地图更新”中显式说明。

保存与发送：
- 观察池报告保存到 `output.stock_selection_report_path`；该路径按 `after_close.trade_date` 生成。如果文件已存在，追加“更新版本”，不要覆盖。
- 股票关系地图保存到 `output.stock_relationship_map_path`；该路径按 `after_close.trade_date` 生成。如果文件已存在，追加“更新版本”，不要覆盖。
- 保存观察池报告成功后发送：

```text
python -m stock_selection.tools.send_text_email --subject "A股收盘后观察池复盘 YYYY-MM-DD" --body-file <最终观察池报告文件路径> --env-file "C:\Users\runyu.luo\Documents\codex 教程\.env"
```

- 保存股票关系地图成功后发送：

```text
python -m stock_selection.tools.send_text_email --subject "A股股票关系地图 YYYY-MM-DD" --body-file <最终地图报告文件路径> --env-file "C:\Users\runyu.luo\Documents\codex 教程\.env"
```

- 如果任一邮件发送失败，记录是哪封失败和错误摘要；不要泄露 `.env`、邮箱授权码、token 或密码。
- 最终摘要包含观察池报告路径、股票关系地图路径、邮件发送结果和历史地图更新要点。
- 不输出买入、卖出、持有、仓位或目标价。

## 早盘前情报校准

默认时间：A 股交易日 08:30，中国时区。

定位：盘前任务是“隔夜事件雷达 + 昨日观察池校准”，不是行情缓存生产者，也不是新观察池生成器。

执行入口：

```text
python scripts/prepare_preopen_context.py --date YYYYMMDD
```

硬性边界：
- 盘前自动化默认不拉取全市场 Tushare 数据，不运行 `prepare_after_close_data.py`，不写入 `data_cache/tushare`，不写入 `analysis_records/_tmp_automation_*`。
- 盘前只读取最新正式盘后 manifest、正式 Tushare 缓存、上一交易日观察池、上一交易日股票关系地图和最近历史记录。
- 如确需验证交易日或少量候选公司事实，可以使用已有正式缓存或少量公开来源；不要把盘前变成全市场数据采集。
- 盘前不生成完整新观察池，不改写收盘后股票关系地图；只给昨日观察池和昨日地图做强化、削弱、无变化、待验证、证伪校准。

基线读取：
- 先运行 `scripts/prepare_preopen_context.py`，读取输出 JSON。
- 使用 `after_close_baseline.trade_date` 作为上一正式有效交易日。
- 使用 `baseline_records.stock_selection` 和 `baseline_records.stock_relationship_map` 作为昨日观察池和昨日关系地图基线。
- 同时读取最近 5 篇 `stock_selection`、`stock_relationship_map`、`sector_analysis`、`stock_selection_preopen` 记录，检查历史判断验证、历史地图更新、之前遗漏、之前错误和公司信息变化。
- 如果未找到昨日正式报告，明确写出缺口，并使用最近可用完整报告作为降级基线。

盘前重点：
- 海外市场：美股三大指数、纳指/费半、纳指100、罗素2000、港股 ADR/中国资产、美元指数、人民币汇率、美债收益率、黄金、原油、铜、主要商品、海外科技股和地缘政策事件。
- 隔夜美股与海外映射：必须把海外变化按“海外事件/行情 -> A 股主线 -> A 股子线 -> 产业链环节 -> A 股公司验证点”组织，只输出强化 / 削弱 / 待验证 / 证伪，不把美股表现直接写成 A 股主线恢复。
- 核心海外观察篮子：AI 硬件链关注 NVIDIA、Broadcom、AMD、Marvell、Arista、Super Micro、Dell；半导体链关注 TSMC、ASML、Applied Materials、Lam Research、KLA、Micron；云和 AI 应用关注 Microsoft、Amazon、Google、Meta、Oracle；新能源和汽车关注 Tesla 及主要海外车企。
- 国内信息：交易所公告、公司公告、政策、财经新闻、行业事件、监管变化、重大风险提示、公司澄清、业绩预告和订单/合同变化。
- 只把可靠来源写成事实；对传闻、媒体解读和海外映射要标注“待验证”。
- 新闻和行情数据分开写；外部市场涨跌不等于 A 股公司订单或收入兑现。

输出要求：
- 标明今天是否为 A 股交易日；如果不是交易日，输出休市说明、隔夜风险变化和下个交易日需验证事项，不强行生成新观察池。
- 输出“隔夜重大事件雷达”：海外冲击、国内公告、政策/监管、商品/汇率/利率、地缘风险。
- 输出“隔夜美股与海外映射”：列出海外行情事实、新闻/公告解释、映射到的昨日主线/子线、A 股开盘验证点和不能直接确认的缺口。
- 对昨日观察池逐项标注：强化 / 削弱 / 无变化 / 待验证 / 证伪，并写出依据和开盘后验证点。
- 对昨日股票关系地图逐主线和子线标注：关系增强、关系减弱、之前遗漏、之前错误、公司信息更新、关系强弱调整或证伪。
- 更新市场环境预判：进攻 / 观察 / 防守 / 退潮。该预判只能作为开盘前情景，不替代收盘后正式门控。
- 输出今日关注重点、风险提示和开盘后需要验证的数据。
- 不输出买入、卖出、持有、仓位或目标价。

保存与发送：
- 保存到 `analysis_records/stock_selection_preopen/YYYY-MM/YYYY-MM-DD.md`；如果当天文件已存在，追加“更新版本”，不要覆盖。
- 保存成功后发送完整报告正文：

```text
python -m stock_selection.tools.send_text_email --subject "A股早盘前情报校准 YYYY-MM-DD" --body-file <报告文件路径> --env-file "C:\Users\runyu.luo\Documents\codex 教程\.env"
```

- 如果邮件发送失败，记录错误摘要；不要泄露 `.env`、邮箱授权码、token 或密码。


## Hotspot Discovery Gate

After-close automation must treat the project as a hotspot lifecycle tracker:

- First use `hotspot_discovery.market_hotspots`, `hotspot_discovery.industry_hotspots`, and `hotspot_discovery.unseeded_themes` to identify today's active themes from full-market data.
- Use `hotspot_discovery.seeded_theme_matches` only to validate known historical themes: continuation, repair, weakening, retreat, or falsification.
- Recent reports are history evidence, not today's theme universe. If an old theme is mentioned repeatedly but lacks current breadth, turnover, leader confirmation, or capital participation, downgrade it to history tracking.
- If the top 1-3 selected themes are all seeded themes, explicitly list the top non-seeded themes and explain why they were not selected.
- Track every important theme through lifecycle states: not started, emerging, confirmed, diffusing, diverging, retreating, repair/rebound, returning, or falsified.
- Select external / overseas sources dynamically by active A-share theme. Do not default to technology sources unless technology is the current confirmed hotspot.

Map-driven discovery requirements:

- Read `hotspot_discovery.relationship_map_context` before selecting related stocks. Use it to identify historical themes, sub-lines, value-chain nodes, pending diffusion, not-started, fallen-behind, downgraded, and falsified companies.
- Read `hotspot_discovery.theme_stock_roles` before writing candidate roles. Roles must distinguish leaders, middle army / amount cores, diffusers, fast followers, laggards, fallen-behind, and falsified names.
- Read `hotspot_discovery.theme_rotation_context` before judging old and new themes. The report must explain whether old themes are continuing, diverging, retreating, repairing, returning, or falsified.
- Do not promote a relationship-map candidate only because it exists in the map. It still needs current market confirmation or a clearly labeled pending-diffusion / not-started status.
- If a mapped company was previously falsified or downgraded, keep the risk tag unless both company evidence and sustained market confirmation improve.
