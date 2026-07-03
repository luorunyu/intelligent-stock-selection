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

from stock_selection.data.after_close import build_after_close_report_context, prepare_after_close_data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare formal after-close cache and emit report-generation context."
    )
    parser.add_argument("--date", default=None, help="Requested date in YYYYMMDD or YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--cache-root", default=None, help="Override formal Tushare cache root.")
    parser.add_argument("--records-root", default="analysis_records", help="Analysis record root.")
    parser.add_argument("--recent", type=int, default=5, help="Number of recent records to list.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing cached datasets.")
    parser.add_argument("--lookback-days", type=int, default=10)
    parser.add_argument("--available-only", action="store_true", help="Respect available_apis.json filtering.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    requested_date = _compact_date(args.date or date.today().strftime("%Y%m%d"))
    records_root = Path(args.records_root)
    manifest = prepare_after_close_data(
        requested_date,
        cache_root=args.cache_root,
        force=args.force,
        lookback_days=args.lookback_days,
        available_only=args.available_only,
    )
    context = build_after_close_report_context(manifest, cache_root=args.cache_root)
    report_date = context.report_date

    payload = {
        "mode": "after_close_formal_cache_context",
        "rules": [
            "Use this context as the report-generation input after formal Tushare cache preparation.",
            "Read structured data only from data_cache/tushare formal cache paths listed here.",
            "Do not write or depend on analysis_records/_tmp_automation_* for daily production.",
            "If requested_date differs from trade_date, state the fallback explicitly in the report.",
            "Review any pre-open overseas mapping against A-share close data: confirm, partially confirm, leave unconfirmed, or falsify with market evidence.",
            "Do not generate buy/sell/hold, position, or target-price language.",
        ],
        "after_close": asdict(context),
        "recent_records": {
            "sector_analysis": _recent_records(records_root / "sector_analysis", args.recent),
            "stock_selection": _recent_records(records_root / "stock_selection", args.recent),
            "stock_relationship_map": _recent_records(records_root / "stock_relationship_map", args.recent),
        },
        "output": {
            "stock_selection_report_path": str(
                records_root / "stock_selection" / report_date[:7] / f"{report_date}.md"
            ),
            "stock_relationship_map_path": str(
                records_root / "stock_relationship_map" / report_date[:7] / f"{report_date}.md"
            ),
            "append_if_exists": True,
            "stock_selection_email_subject": f"A股收盘后观察池复盘 {report_date}",
            "relationship_map_email_subject": f"A股股票关系地图 {report_date}",
        },
        "information_gaps": _information_gaps(context),
    }

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _compact_date(value: str) -> str:
    compact = value.replace("-", "")
    if len(compact) != 8 or not compact.isdigit():
        raise ValueError(f"invalid date: {value}")
    return compact


def _recent_records(root: Path, limit: int) -> list[str]:
    if not root.exists():
        return []
    files = sorted(root.glob("*/*.md"), key=lambda path: path.stat().st_mtime, reverse=True)
    return [str(path) for path in files[:limit]]


def _information_gaps(context) -> list[str]:
    gaps: list[str] = []
    if context.missing_datasets:
        gaps.append("Missing formal cache datasets: " + ", ".join(context.missing_datasets))
    if not context.data_ready_for_requested_date:
        gaps.append(
            f"Requested date {context.requested_date} is not fully ready; "
            f"using latest complete trade date {context.trade_date}."
        )
    return gaps


if __name__ == "__main__":
    raise SystemExit(main())
