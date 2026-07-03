from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_selection.data.after_close import latest_after_close_baseline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build read-only pre-open context from formal after-close records."
    )
    parser.add_argument("--date", default=None, help="Report date in YYYYMMDD or YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--cache-root", default=None, help="Override formal Tushare cache root.")
    parser.add_argument("--records-root", default="analysis_records", help="Analysis record root.")
    parser.add_argument("--recent", type=int, default=5, help="Number of recent records to list.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report_date = _normal_date(args.date or date.today().strftime("%Y%m%d"))
    records_root = Path(args.records_root)
    baseline = latest_after_close_baseline(cache_root=args.cache_root)
    trade_date_dash = _dash_date(baseline.trade_date) if baseline.trade_date else None

    payload = {
        "report_date": report_date,
        "mode": "preopen_read_only_context",
        "rules": [
            "Do not collect full-market Tushare data in pre-open automation.",
            "Use the latest formal after-close cache/report/map as baseline.",
            "Focus on overnight events, severe volatility, announcements, and calibration.",
            "Include overnight US/overseas mapping as clues only: overseas event/market -> A-share main line -> sub-line -> value-chain segment -> opening validation point.",
            "Do not treat US market moves or overseas news as confirmation of A-share orders, revenue, profits, or trend recovery without A-share market validation.",
            "Do not generate buy/sell/hold, position, or target-price language.",
        ],
        "overnight_mapping_requirements": {
            "market_facts": [
                "US major indexes",
                "Nasdaq 100",
                "Philadelphia Semiconductor Index",
                "US-listed China assets or ADRs",
                "US dollar index",
                "US treasury yields",
                "RMB exchange rate",
                "gold",
                "crude oil",
                "copper",
            ],
            "watch_baskets": {
                "AI hardware": ["NVIDIA", "Broadcom", "AMD", "Marvell", "Arista", "Super Micro", "Dell"],
                "semiconductor": ["TSMC", "ASML", "Applied Materials", "Lam Research", "KLA", "Micron"],
                "cloud and AI applications": ["Microsoft", "Amazon", "Google", "Meta", "Oracle"],
                "new energy and auto": ["Tesla", "major overseas automakers"],
            },
            "output_labels": ["strengthen", "weaken", "unchanged", "to_verify", "falsified"],
        },
        "after_close_baseline": asdict(baseline),
        "baseline_records": {
            "stock_selection": _dated_record_path(records_root, "stock_selection", trade_date_dash),
            "stock_relationship_map": _dated_record_path(records_root, "stock_relationship_map", trade_date_dash),
        },
        "recent_records": {
            "stock_selection": _recent_records(records_root / "stock_selection", args.recent),
            "stock_relationship_map": _recent_records(records_root / "stock_relationship_map", args.recent),
            "sector_analysis": _recent_records(records_root / "sector_analysis", args.recent),
            "stock_selection_preopen": _recent_records(records_root / "stock_selection_preopen", args.recent),
        },
        "output": {
            "preopen_report_path": str(
                records_root
                / "stock_selection_preopen"
                / report_date[:7]
                / f"{report_date}.md"
            ),
            "append_if_exists": True,
            "email_subject": f"A股早盘前情报校准 {report_date}",
        },
        "information_gaps": _information_gaps(baseline, records_root, trade_date_dash),
    }

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _normal_date(value: str) -> str:
    compact = value.replace("-", "")
    if len(compact) != 8 or not compact.isdigit():
        raise ValueError(f"invalid date: {value}")
    return f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"


def _dash_date(value: str | None) -> str | None:
    return _normal_date(value) if value else None


def _dated_record_path(records_root: Path, category: str, report_date: str | None) -> str | None:
    if not report_date:
        return None
    path = records_root / category / report_date[:7] / f"{report_date}.md"
    return str(path) if path.exists() else None


def _recent_records(root: Path, limit: int) -> list[str]:
    if not root.exists():
        return []
    files = sorted(root.glob("*/*.md"), key=lambda path: path.stat().st_mtime, reverse=True)
    return [str(path) for path in files[:limit]]


def _information_gaps(baseline, records_root: Path, trade_date: str | None) -> list[str]:
    gaps: list[str] = []
    if not baseline.trade_date:
        gaps.append("No formal after-close baseline was found in data_cache/tushare.")
    if trade_date:
        if not _dated_record_path(records_root, "stock_selection", trade_date):
            gaps.append(f"No stock_selection report found for baseline trade date {trade_date}.")
        if not _dated_record_path(records_root, "stock_relationship_map", trade_date):
            gaps.append(f"No stock_relationship_map report found for baseline trade date {trade_date}.")
    if baseline.source == "cache_scan":
        gaps.append("Baseline came from cache scan because no after_close manifest was found.")
    return gaps


if __name__ == "__main__":
    raise SystemExit(main())
