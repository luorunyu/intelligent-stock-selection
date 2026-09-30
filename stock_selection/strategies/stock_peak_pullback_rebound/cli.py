"""CLI for historical stock peak-pullback-rebound screening."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_selection.strategies.stock_peak_pullback_rebound.report import (
    build_peak_pullback_rebound_report,
    save_peak_pullback_rebound_report,
)
from stock_selection.strategies.stock_peak_pullback_rebound.screen import (
    calculate_peak_pullback_rebounds,
    read_cached_stock_daily,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find historical close-only peak-pullback-rebound patterns."
    )
    parser.add_argument("--date", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--cache-root", default=None)
    parser.add_argument("--stock-code", action="append", default=None)
    parser.add_argument("--peak-lookback-days", type=int, default=120)
    parser.add_argument("--peak-lookforward-days", type=int, default=120)
    parser.add_argument("--max-rebound-days", type=int, default=40)
    parser.add_argument("--min-pullback-pct", type=float, default=0.10)
    parser.add_argument("--min-second-peak-ratio", type=float, default=0.70)
    parser.add_argument("--include-st", action="store_true")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--records-root", default="analysis_records")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    daily, basic = read_cached_stock_daily(
        cache_root=args.cache_root,
        as_of_date=args.date,
    )
    events = calculate_peak_pullback_rebounds(
        daily,
        as_of_date=args.date,
        peak_lookback_days=args.peak_lookback_days,
        peak_lookforward_days=args.peak_lookforward_days,
        max_rebound_days=args.max_rebound_days,
        min_pullback_pct=args.min_pullback_pct,
        min_second_peak_ratio=args.min_second_peak_ratio,
        stock_codes=args.stock_code,
        stock_basic=basic,
        include_st=args.include_st,
    )
    report = build_peak_pullback_rebound_report(
        events,
        as_of_date=args.date,
        peak_lookback_days=args.peak_lookback_days,
        peak_lookforward_days=args.peak_lookforward_days,
        max_rebound_days=args.max_rebound_days,
        min_pullback_pct=args.min_pullback_pct,
        min_second_peak_ratio=args.min_second_peak_ratio,
    )
    if args.write_report:
        csv_path, json_path, markdown_path = save_peak_pullback_rebound_report(
            report, events, records_root=args.records_root
        )
        output = {
            "csv": str(csv_path),
            "json": str(json_path),
            "markdown": str(markdown_path),
            "summary": report["summary"],
        }
    else:
        output = report
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
