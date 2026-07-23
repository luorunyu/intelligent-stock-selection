from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_selection.context.multi_day_market import build_multi_day_market_context
from stock_selection.context.report_context import build_report_context


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build multi-day research context for after-close stock selection.")
    parser.add_argument("--date", default=None, help="End date in YYYYMMDD or YYYY-MM-DD. Defaults to latest cached date.")
    parser.add_argument("--lookback", type=int, default=5, help="Number of cached trading days to summarize.")
    parser.add_argument("--cache-root", default=None, help="Override formal Tushare cache root.")
    parser.add_argument("--records-root", default="analysis_records", help="Analysis records root.")
    parser.add_argument("--recent-reports", type=int, default=5)
    parser.add_argument("--topic-lookback-days", type=int, default=30, help="Days of historical reports to keyword-backtrack by topic.")
    parser.add_argument("--output", default=None, help="Write Markdown output to this file.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    end_date = _compact_date(args.date) if args.date else None
    report_context = build_report_context(
        records_root=args.records_root,
        limit=args.recent_reports,
        end_date=end_date,
        topic_lookback_days=args.topic_lookback_days,
    )
    market_context = build_multi_day_market_context(
        end_date=end_date,
        lookback=args.lookback,
        cache_root=args.cache_root,
        focus_ts_codes=report_context.mentioned_stocks[:80],
    )
    theme_discovery = _load_theme_discovery(Path(args.records_root), market_context.end_date)
    markdown = render_markdown(
        report_context=report_context,
        market_context=market_context,
        theme_discovery=theme_discovery,
    )
    output = Path(args.output) if args.output else _default_output_path(Path(args.records_root), market_context.end_date)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(markdown, encoding="utf-8")
    print(markdown)
    return 0


def render_markdown(*, report_context, market_context, theme_discovery: dict | None = None) -> str:
    report = report_context.to_dict()
    market = market_context.to_dict()
    lines = [
        f"# 收盘后多日研究上下文 - {_dash_date(market_context.end_date)}",
        "",
        "## 数据范围",
        f"- 生成时间：{datetime.now().isoformat(timespec='seconds')}",
        f"- 有效交易日：{', '.join(_dash_date(date) for date in market_context.trade_dates)}",
        "- 数据源：正式 Tushare 缓存 `data_cache/tushare/<api>/<trade_date>.parquet`。",
        "- 用途：给 skill 判断当前板块/主题延续、强化、分化、削弱、退潮时使用；本上下文只提供事实统计，不替代最终判断。",
    ]
    if market_context.missing_datasets:
        lines.append(f"- 缺失数据：{', '.join(market_context.missing_datasets[:20])}")
    lines.extend(["", "## 多日市场概况"])
    lines.extend(_market_table(market["market_by_date"]))
    lines.extend(["", "## 今日全市场热点扫描"])
    lines.extend(_hotspot_discovery_section(theme_discovery or {}))
    lines.extend(["", "## 股票关系地图索引命中"])
    lines.extend(_relationship_map_context_section(theme_discovery or {}))
    lines.extend(["", "## 今日最强主题的板块内角色"])
    lines.extend(_stock_role_context_section(theme_discovery or {}))
    lines.extend(["", "## 今日非种子新热点"])
    lines.extend(_unseeded_theme_section(theme_discovery or {}))
    lines.extend(["", "## 热点生命周期追踪"])
    lines.extend(_theme_lifecycle_section(theme_discovery or {}))
    lines.extend(["", "## 板块轮动路径"])
    lines.extend(_theme_rotation_context_section(theme_discovery or {}))
    lines.extend(["", "## 地图待扩散/未启动节点今日验证"])
    lines.extend(_map_related_candidates_section(theme_discovery or {}))
    lines.extend(["", "## 行业多日表现（按最新交易日排序）"])
    lines.extend(_industry_table(market["industries"][:20]))
    lines.extend(["", "## 已知种子主题验证（仅作历史主线验证）"])
    lines.extend(_seeded_theme_section(theme_discovery or {}))

    lines.extend(["", "## 最近报告线索"])
    lines.extend(_recent_reports_table(report["recent_reports"]))
    lines.extend(["", "## 最近报告提到的主题"])
    lines.append("- " + "、".join(report["mentioned_themes"][:30]) if report["mentioned_themes"] else "- 未抽取到主题。")
    lines.extend(["", "## 历史判断关键词"])
    if report["judgement_lines"]:
        for row in report["judgement_lines"][:20]:
            lines.append(f"- {row['date']}：{row['line']}")
    else:
        lines.append("- 未抽取到判断关键词。")

    lines.extend(["", "## 历史主题生命周期事件"])
    lines.extend(_historical_theme_events_section(report.get("historical_theme_events", [])))

    lines.extend(["", "## 历史报告关键词回溯（最近30天）"])
    lines.extend(_topic_backtrack_section(report.get("topic_backtracks", [])))

    lines.extend(["", "## 历史报告核心股票近几日表现"])
    lines.extend(_stock_table(market["focus_stocks"][:30]))
    lines.extend(
        [
            "",
            "## 使用提示",
            "- 判断今日热点时，先看全市场热点扫描和非种子新热点，再用历史报告验证生命周期，不要让旧主题自动占据今日主线。",
            "- `seeded_theme_matches` 只表示已知主题是否被当天数据验证，不等同于今日最强主线。",
            "- 如果报告说主题强化，但多日数据表现为放量下跌、上涨占比恶化，应在正式复盘中降级。",
            "- 如果报告只提到故事，但行业和核心股没有成交、宽度或资金确认，应标注为待验证。",
            "- 本文仅作研究观察和后续事件跟踪，不构成投资建议。",
            "",
        ]
    )
    return "\n".join(lines)


def _recent_reports_table(reports: list[dict]) -> list[str]:
    if not reports:
        return ["- 未找到最近报告。"]
    rows = [_row(["日期", "类别", "主题线索", "股票数", "路径"]), _row(["---", "---", "---", "---:", "---"])]
    for item in reports:
        rows.append(_row([item["date"], item["category"], "、".join(item["themes"][:6]), str(item["stock_count"]), item["path"]]))
    return rows


def _topic_backtrack_section(topics: list[dict]) -> list[str]:
    if not topics:
        return ["- 未抽取到关键词回溯线索。"]
    lines: list[str] = []
    for group in topics:
        lines.append(f"### {group['keyword']}")
        lines.extend(_keyword_hits_table(group.get("hits", [])))
        lines.append("")
    return lines


def _historical_theme_events_section(events: list[dict]) -> list[str]:
    if not events:
        return ["- 未抽取到结构化历史主题事件。"]
    rows = [
        _row(["日期", "类别", "主题", "状态", "摘要", "路径"]),
        _row(["---", "---", "---", "---", "---", "---"]),
    ]
    for item in events[:20]:
        rows.append(
            _row(
                [
                    item.get("date", ""),
                    item.get("category", ""),
                    item.get("theme", ""),
                    item.get("state", ""),
                    item.get("line", ""),
                    item.get("source", ""),
                ]
            )
        )
    return rows


def _keyword_hits_table(hits: list[dict]) -> list[str]:
    if not hits:
        return ["- 无事件线索。"]
    rows = [_row(["日期", "类别", "摘要", "路径"]), _row(["---", "---", "---", "---"])]
    for hit in hits[:10]:
        rows.append(
            _row(
                [
                    hit["date"],
                    hit["category"],
                    hit["line"],
                    hit["source"],
                ]
            )
        )
    return rows


def _market_table(market_by_date: dict[str, dict]) -> list[str]:
    rows = [_row(["日期", "上涨/下跌", "平均涨跌幅", "成交额(亿元)", "涨停/跌停"]), _row(["---", "---:", "---:", "---:", "---:"])]
    for trade_date, item in market_by_date.items():
        if not item:
            continue
        rows.append(
            _row([
                _dash_date(trade_date),
                f"{item.get('up', 0)}/{item.get('down', 0)}",
                _fmt_pct(item.get("avg_pct")),
                _fmt_num(item.get("amount_yi")),
                f"{item.get('limit_up', 0)}/{item.get('limit_down', 0)}",
            ])
        )
    return rows


def _industry_table(industries: list[dict]) -> list[str]:
    if not industries:
        return ["- 无行业统计。"]
    rows = [_row(["行业", "最新涨跌", "上涨占比", "成交额", "资金净额(万)", "涨停/跌停", "成交趋势", "数据状态", "多日涨跌路径"]), _row(["---", "---:", "---:", "---:", "---:", "---:", "---", "---", "---"])]
    for item in industries:
        rows.append(
            _row([
                item["industry"],
                _fmt_pct(item["latest_avg_pct"]),
                f"{item['latest_up_ratio']:.0%}",
                _fmt_num(item["latest_amount_yi"]),
                _fmt_num(item["latest_net_mf_wan"]),
                f"{item['limit_up']}/{item['limit_down']}",
                item["amount_trend"],
                item["data_state"],
                ", ".join(_fmt_pct(value) for value in item["avg_pct_path"]),
            ])
        )
    return rows


def _stock_table(stocks: list[dict]) -> list[str]:
    if not stocks:
        return ["- 最近报告股票未在正式缓存中形成可统计样本。"]
    rows = [_row(["股票", "行业", "最新涨跌", "区间涨跌", "成交额", "换手", "资金净额(万)", "多日涨跌路径"]), _row(["---", "---", "---:", "---:", "---:", "---:", "---:", "---"])]
    for item in stocks:
        rows.append(
            _row([
                f"{item['name']} {item['ts_code']}",
                item["industry"],
                _fmt_pct(item["latest_pct"]),
                _fmt_pct(item["lookback_cum_pct"]),
                _fmt_num(item["latest_amount_yi"]),
                _fmt_num(item["latest_turnover"]),
                _fmt_num(item["latest_net_mf_wan"]),
                ", ".join(_fmt_pct(value) for value in item["pct_path"]),
            ])
        )
    return rows


def _hotspot_discovery_section(discovery: dict) -> list[str]:
    if not discovery:
        return ["- 未找到结构化热点发现记录。"]
    if discovery.get("error"):
        return [f"- 热点发现失败：{discovery['error']}"]
    rows = [
        f"- 发现交易日：{_dash_date(discovery['trade_date'])}" if discovery.get("trade_date") else "- 发现交易日：-",
        f"- 发现记录：{discovery.get('output_path') or '-'}",
    ]
    hotspots = discovery.get("market_hotspots") or []
    if not hotspots:
        rows.append("- 未发现达到阈值的全市场热点候选。")
        return rows
    table = [
        _row(["候选热点", "来源", "种子", "状态", "评分", "均涨", "上涨占比", "涨停", "成交额", "证据"]),
        _row(["---", "---", "---", "---", "---:", "---:", "---:", "---:", "---:", "---"]),
    ]
    for item in hotspots[:12]:
        table.append(
            _row(
                [
                    item.get("theme", ""),
                    item.get("source_type", ""),
                    "是" if item.get("seed_based") else "否",
                    item.get("status", ""),
                    f"{float(item.get('score') or 0):.2f}",
                    _fmt_pct(item.get("latest_avg_pct")),
                    _fmt_ratio(item.get("latest_up_ratio")),
                    str(item.get("limit_up", 0)),
                    _fmt_num(item.get("latest_amount_yi")),
                    item.get("evidence", ""),
                ]
            )
        )
    rows.extend(table)
    return rows


def _relationship_map_context_section(discovery: dict) -> list[str]:
    context = discovery.get("relationship_map_context") or {}
    if not context:
        return ["- 未找到股票关系地图索引。"]
    rows = [
        f"- 地图来源文件：{len(context.get('source_files') or [])} 个。",
        f"- 结构化公司节点：{len(context.get('companies') or [])} 个；待扩散：{len(context.get('pending_diffusion') or [])}；未启动：{len(context.get('not_started') or [])}；证伪/降级：{len(context.get('falsified') or [])}。",
    ]
    themes = context.get("themes") or []
    if themes:
        table = [
            _row(["地图主题", "公司数", "子线数", "待扩散", "未启动", "证伪"]),
            _row(["---", "---:", "---:", "---:", "---:", "---:"]),
        ]
        for item in themes[:12]:
            table.append(
                _row(
                    [
                        item.get("theme", ""),
                        str(item.get("company_count", 0)),
                        str(item.get("subline_count", 0)),
                        str(item.get("pending_diffusion", 0)),
                        str(item.get("not_started", 0)),
                        str(item.get("falsified", 0)),
                    ]
                )
            )
        rows.extend(table)
    return rows


def _stock_role_context_section(discovery: dict) -> list[str]:
    role_sets = discovery.get("theme_stock_roles") or []
    if not role_sets:
        return ["- 未生成板块内股票角色分类。"]
    rows: list[str] = []
    for item in role_sets[:6]:
        roles = item.get("roles") or {}
        rows.append(f"### {item.get('theme', '-')}")
        rows.append(
            "- 角色计数：龙头 {leaders}；中军 {middle}; 扩散 {diffusers}; 快速跟随 {followers}; 补涨观察 {laggards}; 掉队 {fallen}; 证伪 {falsified}。".format(
                leaders=len(roles.get("leaders") or []),
                middle=len(roles.get("middle_army") or []),
                diffusers=len(roles.get("diffusers") or []),
                followers=len(roles.get("fast_followers") or []),
                laggards=len(roles.get("laggards") or []),
                fallen=len(roles.get("fallen_behind") or []),
                falsified=len(roles.get("falsified") or []),
            )
        )
        rows.extend(_role_table(roles))
        rows.append("")
    return rows


def _role_table(roles: dict) -> list[str]:
    rows = [
        _row(["角色", "股票", "涨跌", "成交额", "地图主题", "子线", "地图状态", "评分"]),
        _row(["---", "---", "---:", "---:", "---", "---", "---", "---:"]),
    ]
    role_names = {
        "leaders": "龙头",
        "middle_army": "中军",
        "diffusers": "扩散",
        "fast_followers": "快速跟随",
        "laggards": "补涨观察",
        "fallen_behind": "掉队",
        "falsified": "证伪",
    }
    for key, label in role_names.items():
        for stock in (roles.get(key) or [])[:4]:
            rows.append(
                _row(
                    [
                        label,
                        f"{stock.get('name', '')} {stock.get('ts_code', '')}",
                        _fmt_pct(stock.get("pct_chg")),
                        _fmt_num(stock.get("amount_yi")),
                        stock.get("map_theme", "") or "",
                        stock.get("subline", "") or "",
                        stock.get("map_status", "") or "",
                        _fmt_num(stock.get("role_score")),
                    ]
                )
            )
    return rows


def _theme_rotation_context_section(discovery: dict) -> list[str]:
    context = discovery.get("theme_rotation_context") or {}
    if not context:
        return ["- 未生成主题轮动上下文。"]
    rows: list[str] = []
    edges = context.get("rotation_edges") or []
    if edges:
        rows.extend([
            _row(["日期", "旧主题", "新主题", "强度", "证据"]),
            _row(["---", "---", "---", "---:", "---"]),
        ])
        for item in edges[:10]:
            rows.append(_row([item.get("date", ""), item.get("from_theme", ""), item.get("to_theme", ""), _fmt_num(item.get("strength")), item.get("evidence", "")]))
    else:
        rows.append("- 未形成明确轮动边。")
    transitions = context.get("theme_transitions") or []
    if transitions:
        rows.append("")
        rows.append("- 主题状态变化：" + "；".join(f"{item.get('theme')}={item.get('transition')}" for item in transitions[:10]))
    return rows


def _map_related_candidates_section(discovery: dict) -> list[str]:
    rows_data = discovery.get("map_related_candidates") or []
    if not rows_data:
        return ["- 未发现地图相关候选命中今日热点。"]
    rows = [
        _row(["今日热点", "股票", "地图主题", "子线", "产业链", "关系强度", "地图状态"]),
        _row(["---", "---", "---", "---", "---", "---", "---"]),
    ]
    for item in rows_data[:30]:
        rows.append(
            _row(
                [
                    item.get("hotspot", ""),
                    f"{item.get('name', '')} {item.get('ts_code', '')}",
                    item.get("map_theme", ""),
                    item.get("subline", ""),
                    item.get("chain", ""),
                    item.get("relationship_strength", ""),
                    item.get("market_status", ""),
                ]
            )
        )
    return rows


def _unseeded_theme_section(discovery: dict) -> list[str]:
    themes = discovery.get("unseeded_themes") or []
    if not themes:
        return ["- 未发现非种子新热点，或新热点强度不足。"]
    rows = [
        _row(["非种子热点", "状态", "评分", "行业/环节", "均涨", "上涨占比", "涨停", "证据"]),
        _row(["---", "---", "---:", "---", "---:", "---:", "---:", "---"]),
    ]
    for item in themes[:12]:
        rows.append(
            _row(
                [
                    item.get("theme", ""),
                    item.get("status", ""),
                    f"{float(item.get('score') or 0):.2f}",
                    "、".join(item.get("industries") or []),
                    _fmt_pct(item.get("latest_avg_pct")),
                    _fmt_ratio(item.get("latest_up_ratio")),
                    str(item.get("limit_up", 0)),
                    item.get("evidence", ""),
                ]
            )
        )
    return rows


def _theme_lifecycle_section(discovery: dict) -> list[str]:
    lifecycle = discovery.get("theme_lifecycle") or []
    if not lifecycle:
        return ["- 未形成热点生命周期记录。"]
    rows = [
        _row(["主题", "生命周期状态", "来源", "种子", "评分", "下一步验证"]),
        _row(["---", "---", "---", "---", "---:", "---"]),
    ]
    for item in lifecycle[:15]:
        rows.append(
            _row(
                [
                    item.get("theme", ""),
                    item.get("state", ""),
                    item.get("source_type", ""),
                    "是" if item.get("seed_based") else "否",
                    f"{float(item.get('score') or 0):.2f}",
                    item.get("next_check", ""),
                ]
            )
        )
    return rows


def _seeded_theme_section(discovery: dict) -> list[str]:
    themes = discovery.get("seeded_theme_matches") or discovery.get("themes") or []
    if not themes:
        return ["- 未发现已知种子主题获得当日数据验证。"]
    rows = [
        _row(["已知主题", "状态", "评分", "涨跌", "涨停", "均涨", "成交额", "跨行业", "证据"]),
        _row(["---", "---", "---:", "---:", "---:", "---:", "---:", "---", "---"]),
    ]
    for theme in themes[:10]:
        rows.append(
            _row(
                [
                    theme.get("theme", ""),
                    theme.get("status", ""),
                    f"{float(theme.get('score') or 0):.2f}",
                    f"{theme.get('up', 0)}/{theme.get('down', 0)}",
                    str(theme.get("limit_up", 0)),
                    _fmt_pct(theme.get("avg_pct")),
                    _fmt_num(theme.get("amount_yi")),
                    "是" if theme.get("cross_industry") else "否",
                    theme.get("evidence", ""),
                ]
            )
        )
    return rows


def _theme_discovery_section(discovery: dict) -> list[str]:
    if not discovery:
        return ["- 未找到结构化主题发现记录。"]
    if discovery.get("error"):
        return [f"- 主题发现失败：{discovery['error']}"]
    themes = discovery.get("themes") or []
    clusters = discovery.get("tag_clusters") or []
    lines = [
        f"- 发现交易日：{_dash_date(discovery['trade_date'])}" if discovery.get("trade_date") else "- 发现交易日：-",
        f"- 发现记录：{discovery.get('output_path') or '-'}",
    ]
    if not themes:
        lines.append("- 未发现达到阈值的主题候选。")
    else:
        rows = [
            _row(["候选主题", "状态", "评分", "涨跌", "涨停", "均涨", "成交额", "跨行业", "证据"]),
            _row(["---", "---", "---:", "---:", "---:", "---:", "---:", "---", "---"]),
        ]
        for theme in themes[:8]:
            rows.append(
                _row(
                    [
                        theme["theme"],
                        theme["status"],
                        f"{theme['score']:.2f}",
                        f"{theme['up']}/{theme['down']}",
                        str(theme["limit_up"]),
                        _fmt_pct(theme["avg_pct"]),
                        _fmt_num(theme["amount_yi"]),
                        "是" if theme.get("cross_industry") else "否",
                        theme.get("evidence", ""),
                    ]
                )
            )
        lines.extend(rows)
    if clusters:
        lines.append("")
        lines.append("- 高频标签：" + "、".join(f"{item['tag']}({item['stock_count']}只)" for item in clusters[:10]))
    lines.append("- 使用提示：标签发现只作为新主题候选，正式报告仍需结合历史记录、主营业务和公司级证据确认。")
    return lines


def _load_theme_discovery(records_root: Path, trade_date: str) -> dict:
    dashed = _dash_date(trade_date)
    path = records_root / "theme_discovery" / dashed[:7] / f"{dashed}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"error": f"invalid JSON in {path}: {exc}"}


def _row(values: list[str]) -> str:
    return "| " + " | ".join(str(value).replace("|", "\\|").replace("\n", " ") for value in values) + " |"


def _compact_date(value: str) -> str:
    compact = value.replace("-", "")
    if len(compact) != 8 or not compact.isdigit():
        raise ValueError(f"invalid date: {value}")
    return compact


def _dash_date(value: str) -> str:
    compact = _compact_date(value)
    return f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"


def _default_output_path(records_root: Path, end_date: str) -> Path:
    dashed = _dash_date(end_date)
    return records_root / "research_context" / dashed[:7] / f"{dashed}.md"


def _fmt_pct(value) -> str:
    if value is None:
        return "-"
    return f"{float(value):+.2f}%"


def _fmt_ratio(value) -> str:
    if value is None:
        return "-"
    return f"{float(value):.0%}"


def _fmt_num(value) -> str:
    if value is None:
        return "-"
    return f"{float(value):.2f}"


if __name__ == "__main__":
    raise SystemExit(main())
