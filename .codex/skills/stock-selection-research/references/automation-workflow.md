# 自动化工作流

## 收盘后观察池复盘

默认时间：A 股交易日 16:30，中国时区。

目标：

- 用收盘后更完整的数据复盘市场环境、板块主线、龙头、同组补涨和观察池。
- 保存当天观察池和复盘记录，保证后续事件分析有证据链。

步骤：

1. 确认今天是否为 A 股交易日；如果不是，使用最近一个交易日并明确说明。
2. 读取最近 5 篇 `analysis_records/sector_analysis` 和 `analysis_records/stock_selection` 记录。
3. 使用 `market-regime.md` 输出市场环境门控。
4. 使用 `stock-sector-analysis` 判断热门板块、题材、新闻催化和历史验证。
5. 使用 `tushare-5000-a-stock` 获取结构化行情、成交、资金、涨跌停、行业、龙虎榜、融资融券等数据；必须遵守其 `usage-strategy.md`：先按 `trade_date` 批量拉横截面并缓存，再筛候选池，不要全市场逐票深挖。
6. 对当天最强 1-3 个主题读取 `stock-sector-analysis/references/stock-relationship-map.md`，生成独立股票关系地图：
   - 拆分上游、中游、下游、配套服务。
   - 输出已异动、待扩散、未启动、掉队、证伪公司。
   - 每家代表公司说明主营、产品、客户、上游、下游、题材、催化、财务验证、市场验证和信息缺口。
   - 生成前读取最近 5 篇 `analysis_records/stock_relationship_map`，如果发现公司信息更新、之前遗漏、之前错误、关系强弱调整或证伪，必须在地图报告中显式写出。
   - 保存到 `analysis_records/stock_relationship_map/YYYY-MM/YYYY-MM-DD.md`，当天文件已存在时追加更新版本，不直接覆盖。
7. 生成龙头、快速跟随、补涨观察、待扩散观察、未启动但相关、暂缓/剔除六类观察结果。
8. 对候选池再补近 20/60 日行情或资金数据；重点观察池通常控制在 20-50 只。
9. 对已有观察池和已有股票关系地图做 T+1/T+3/T+5/T+10/T+20 复盘，标注验证、部分验证、未验证、被证伪、信息更新或之前遗漏。
10. 保存观察池到 `analysis_records/stock_selection/YYYY-MM/YYYY-MM-DD.md`。当天文件已存在时追加更新版本，不直接覆盖。
11. 保存成功后，使用项目邮件工具发送完整观察池报告正文：

```text
python -m stock_selection.tools.send_text_email --subject "A股收盘后观察池复盘 YYYY-MM-DD" --body-file <报告文件路径> --env-file "C:\Users\runyu.luo\Documents\codex 教程\.env"
```

12. 如果股票关系地图已保存，继续发送完整地图报告正文：

```text
python -m stock_selection.tools.send_text_email --subject "A股股票关系地图 YYYY-MM-DD" --body-file <地图报告文件路径> --env-file "C:\Users\runyu.luo\Documents\codex 教程\.env"
```

13. 如果任一邮件发送失败，记录错误摘要，但不要输出 `.env`、邮箱授权码、token 或密码。
14. 输出摘要，不输出买入、卖出、持有、仓位或目标价。

## 早盘前情报校准

默认时间：A 股交易日 08:30，中国时区。

目标：

- 用隔夜海外市场、国内早间公告和新闻校准昨日观察池。
- 判断今天题材是否被强化、削弱或需要暂缓观察。

步骤：

1. 确认今天是否为 A 股交易日；如果不是，输出休市说明和最近观察池风险变化，不强行生成新观察池。
2. 读取上一交易日观察池、上一交易日股票关系地图，以及最近 5 篇板块分析记录。
3. 检查海外来源：美股、港股、美元指数、人民币汇率、商品、海外科技股、地缘和政策新闻。
4. 检查国内来源：交易所公告、公司公告、政策、财经新闻、行业事件。
5. 早盘前默认不做全市场 Tushare 深度拉取；优先读取昨晚缓存和观察池记录，只在必要时少量查询候选池或交易日数据。
6. 对昨日观察池和股票关系地图逐项标注：强化、削弱、无变化、待验证；如果盘前发现公司信息更新、之前遗漏或之前错误，明确写出但不强行改写收盘后地图。
7. 更新市场环境预判：进攻、观察、防守或退潮。
8. 输出今日关注重点、风险提示、需要开盘后验证的数据。不输出买卖指令。
9. 保存到 `analysis_records/stock_selection_preopen/YYYY-MM/YYYY-MM-DD.md`。当天文件已存在时追加更新版本，不直接覆盖。
10. 保存成功后，使用项目邮件工具发送完整报告正文：

```text
python -m stock_selection.tools.send_text_email --subject "A股早盘前情报校准 YYYY-MM-DD" --body-file <报告文件路径> --env-file "C:\Users\runyu.luo\Documents\codex 教程\.env"
```

11. 如果邮件发送失败，记录错误摘要，但不要输出 `.env`、邮箱授权码、token 或密码。
