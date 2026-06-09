---
name: stock-selection-research
description: Build traceable A-share stock observation watchlists on top of project skills `tushare-5000-a-stock` and `stock-sector-analysis`. Use when Codex needs to screen A-share candidates for manual observation, follow capital flows and market-confirmed themes, connect later events back to prior candidates, score candidates with transparent evidence, maintain watchlist records, review follow-up performance, or explain why a candidate entered or left the observation pool. Exclude ST stocks by default, do not provide buy/sell/hold instructions or target prices.
---

# Stock Selection Research

## Goal

Generate A-share observation candidates that follow verified capital behavior rather than unsupported narratives. Use this skill to create a reproducible watchlist for manual review and later event analysis, with every candidate backed by structured market data, source links, scoring, risk tags, and review conditions.

## Required Skill Order

1. Use project skill `tushare-5000-a-stock` for structured A-share data and Tushare permission boundaries.
2. Use project skill `stock-sector-analysis` when current sectors, themes, news catalysts, or historical sector records matter.
3. Use this skill to turn the data and sector context into a traceable observation pool.

## Default Boundaries

- Market: A shares.
- Purpose: manual observation and later event analysis, not automated trading advice.
- Default exclusion: ST stocks and stocks currently suspended.
- Do not exclude STAR Market, ChiNext, BSE, new stocks, or low-priced stocks unless the user asks.
- Follow capital first: price strength,成交 confirmation,资金 participation,板块扩散,龙虎榜/融资融券/北向等 evidence outrank narrative-only stories.
- Separate facts from inference. Announcements, exchange filings, company official records, and published data are facts; market mapping, concept association, and source interpretation are inference.
- Do not output buy, sell, hold, position size, target price, or guaranteed outcome language.

## Workflow

1. Confirm the analysis date and latest valid trading day. For relative dates, write exact dates.
2. Read recent sector analysis records if available, especially the latest 5 records and any records mentioning the same theme or stock.
3. Judge market regime before selecting stocks. Read `references/market-regime.md` when deciding whether current conditions support attack, observation, defense, or retreat.
4. Build the stock universe and apply default filters. Read `references/universe-and-filters.md` when universe details matter.
5. Collect structured data with `tushare-5000-a-stock` using its 5000-point usage strategy: batch by `trade_date`, cache daily results, screen a candidate pool first, then deep-dive only candidates. Use daily行情, daily_basic, moneyflow, stk_limit, sw_daily, index_member_all, margin/margin_detail, top_list/top_inst, and event-risk interfaces as needed.
6. For every selected stock or hot theme, build a peer observation group: leaders, high-turnover cores, limit-up cores, slow movers, and possible laggards within the same industry/theme/value-chain mapping.
7. Collect catalysts from source lists. Read `references/source-list.md` when choosing news, announcements, company, industry, or overseas sources.
8. Score candidates using `references/scoring-model.md`. Do not invent scores for unavailable data; mark missing evidence.
9. Produce a watchlist using the output template in `references/output-template.md`.
10. Save or update records using `references/recording-and-review.md` unless the user explicitly says not to save.
11. For scheduled runs, follow `references/automation-workflow.md`.
12. In later analyses, connect new events back to prior candidates: whether the original capital evidence strengthened, faded, or was contradicted.

## Candidate Selection Principles

- Prefer candidates where market behavior confirms the story: strong relative performance, expanded成交, active换手, rising sector breadth, visible leaders, and fresh catalysts.
- When a leader confirms a theme, add related stocks to a peer observation group instead of analyzing the leader alone. Slow movers can be observation candidates only when the theme is still active, sector breadth is expanding or stable, their business relation is real, and risk tags do not dominate.
- Penalize one-stock themes, old news reused as new logic, high-position acceleration without成交 support, and company-level catalyst gaps.
- Treat资金流 as corroborating evidence, not a standalone truth, because vendor口径 can differ.
- Give each candidate an observation reason and a falsification condition. Example: "sector remains strong but this stock fails to outperform its sector for 3 trading days" is a valid observation condition.
- If a stock is selected mainly because of资金 or情绪 and no company catalyst is found, label it clearly as "资金/情绪驱动， company-level catalyst unclear".

## Peer Observation Groups

Use peer groups to catch板块扩散 and补涨 candidates:

- 龙头确认：identify the stock with strongest limit-up height,成交额,涨幅,辨识度, or company catalyst.
- 同组映射：include stocks from the same申万行业, concept/theme, supply chain, customer chain, product line, policy beneficiary group, or overseas映射 chain.
- 补涨候选：mark stocks that lag the leader but show成交放大,换手改善,资金参与, or early price response.
- 失效条件：remove laggards if the leader weakens,板块成交收缩,扩散失败,同组股票转弱, or the laggard cannot outperform its industry/theme after the observation window.
- 风险提示：补涨 is a probability observation, not certainty. Avoid treating a slow stock as attractive merely because the leader has risen.

## Required Output

Every watchlist must include:

- Analysis date and latest trading day.
- Data interfaces and date ranges used.
- Source links or source names with publication dates.
- Stock code, name, market, industry/theme, and whether it is sector leader, high-turnover core, rebound candidate, or event-driven candidate.
- Peer group: related leaders, fast followers, slow movers, and laggard observation candidates with the mapping reason.
- Evidence split into market data, capital behavior, sector confirmation, and catalyst.
- Score, confidence, risk tags, observation conditions, and removal conditions.
- "For research observation only, not investment advice."

## References

- `references/universe-and-filters.md`: stock pool, default exclusions, data quality and risk filters.
- `references/market-regime.md`: market environment gate for attack, observation, defense, and retreat.
- `references/scoring-model.md`: candidate scoring, confidence labels, risk deductions, and ranking rules.
- `references/source-list.md`: China and overseas source priority for news, announcements, industry data, and macro context.
- `references/output-template.md`: watchlist and single-stock event analysis output templates.
- `references/recording-and-review.md`: save paths, review cadence, and follow-up verification rules.
- `references/automation-workflow.md`: scheduled close-after and pre-open task workflows.
