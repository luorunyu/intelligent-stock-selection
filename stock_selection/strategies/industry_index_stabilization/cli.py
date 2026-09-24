"""CLI for historical SW L2 stabilization-signal validation."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_selection.strategies.industry_index_stabilization.report import (
    build_industry_index_stabilization_report,
    save_industry_index_stabilization_report,
)
from stock_selection.strategies.industry_index_stabilization.screen import (
    calculate_industry_index_stabilization,
    load_industry_dictionary,
    read_cached_industry_index_data,
)


def parse_args() -> argparse.Namespace:
    """企稳策略，找到下跌通道的票，判断是否企稳，"""
    parser = argparse.ArgumentParser(
        description="Backtest stabilization signals in SW2021 L2 or L3 industry indexes."
    )
    parser.add_argument("--date", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--level", choices=("L2", "L3", "l2", "l3"), default="L2")
    parser.add_argument("--cache-root", default=None)
    parser.add_argument(
        "--dictionary",
        default="data_cache/tushare/static/sw_industry_stock_dictionary.json",
    )
    parser.add_argument("--industry-code", action="append", default=None)
    parser.add_argument("--trend-lookback", type=int, default=120)
    parser.add_argument("--min-decline-days", type=int, default=20)
    parser.add_argument("--min-decline-pct", type=float, default=0.30)
    parser.add_argument("--min-stable-days", type=int, default=5)
    parser.add_argument("--max-stable-range-points", type=float, default=10.0)
    parser.add_argument("--forward-window", type=int, default=60)
    parser.add_argument("--target-peak-ratio", type=float, default=0.80)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--records-root", default="analysis_records")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    level = args.level.upper()
    signals = calculate_industry_index_stabilization(
        read_cached_industry_index_data(cache_root=args.cache_root),
        load_industry_dictionary(args.dictionary),
        level=level,
        as_of_date=args.date,
        trend_lookback=args.trend_lookback,
        min_decline_days=args.min_decline_days,
        min_decline_pct=args.min_decline_pct,
        min_stable_days=args.min_stable_days,
        max_stable_range_points=args.max_stable_range_points,
        forward_window=args.forward_window,
        target_peak_ratio=args.target_peak_ratio,
        industry_codes=args.industry_code,
    )
    report = build_industry_index_stabilization_report(
        signals,
        as_of_date=args.date,
        level=level,
        trend_lookback=args.trend_lookback,
        min_decline_days=args.min_decline_days,
        min_decline_pct=args.min_decline_pct,
        min_stable_days=args.min_stable_days,
        max_stable_range_points=args.max_stable_range_points,
        forward_window=args.forward_window,
        target_peak_ratio=args.target_peak_ratio,
    )
    if args.write_report:
        csv_path, json_path, markdown_path = save_industry_index_stabilization_report(
            report, signals, records_root=args.records_root
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
