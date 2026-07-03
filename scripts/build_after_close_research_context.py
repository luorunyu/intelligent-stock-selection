from __future__ import annotations

import argparse
from datetime import datetime
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
    markdown = render_markdown(report_context=report_context, market_context=market_context)
    output = Path(args.output) if args.output else _default_output_path(Path(args.records_root), market_context.end_date)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(markdown, encoding="utf-8")
    print(markdown)
    return 0


def render_markdown(*, report_context, market_context) -> str:
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

    lines.extend(["", "## 历史报告关键词回溯（最近30天）"])
    lines.extend(_topic_backtrack_section(report.get("topic_backtracks", [])))

    lines.extend(["", "## 多日市场概况"])
    lines.extend(_market_table(market["market_by_date"]))
    lines.extend(["", "## 行业多日表现（按最新交易日排序）"])
    lines.extend(_industry_table(market["industries"][:20]))
    lines.extend(["", "## 历史报告核心股票近几日表现"])
    lines.extend(_stock_table(market["focus_stocks"][:30]))
    lines.extend(
        [
            "",
            "## 使用提示",
            "- 判断某个热线时，先看历史报告是否反复提到，再看对应行业和核心股的多日数据是否强化或证伪。",
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


def _fmt_num(value) -> str:
    if value is None:
        return "-"
    return f"{float(value):.2f}"


if __name__ == "__main__":
    raise SystemExit(main())
