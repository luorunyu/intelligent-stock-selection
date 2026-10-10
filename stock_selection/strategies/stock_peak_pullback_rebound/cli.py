"""CLI for daily stock peak-pullback-rebound states."""

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
    DEFAULT_OUTPUT_ROOT,
    build_peak_pullback_rebound_state_report,
    save_peak_pullback_rebound_state_report,
)
from stock_selection.strategies.stock_peak_pullback_rebound.screen import (
    DEFAULT_MAX_REBOUND_DAYS,
    DEFAULT_MIN_PULLBACK_PCT,
    DEFAULT_MIN_SECOND_PEAK_RATIO,
    DEFAULT_PEAK_LOOKBACK_DAYS,
    DEFAULT_REBOUND_END_DRAWDOWN_PCT,
    DEFAULT_REBOUND_ENDED_STATE_DAYS,
    calculate_peak_pullback_rebound_states,
    read_cached_stock_daily,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Classify daily close-only peak-pullback-rebound states."
    )
    parser.add_argument("--date", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--cache-root", default=None)
    parser.add_argument("--stock-code", action="append", default=None)
    parser.add_argument(
        "--peak-lookback-days", type=int, default=DEFAULT_PEAK_LOOKBACK_DAYS
    )
    parser.add_argument(
        "--max-rebound-days", type=int, default=DEFAULT_MAX_REBOUND_DAYS
    )
    parser.add_argument(
        "--min-pullback-pct", type=float, default=DEFAULT_MIN_PULLBACK_PCT
    )
    parser.add_argument(
        "--min-second-peak-ratio",
        type=float,
        default=DEFAULT_MIN_SECOND_PEAK_RATIO,
    )
    parser.add_argument(
        "--rebound-end-drawdown-pct",
        type=float,
        default=DEFAULT_REBOUND_END_DRAWDOWN_PCT,
    )
    parser.add_argument(
        "--rebound-ended-state-days",
        type=int,
        default=DEFAULT_REBOUND_ENDED_STATE_DAYS,
    )
    parser.add_argument("--include-st", action="store_true")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--records-root", default=str(DEFAULT_OUTPUT_ROOT))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    daily, basic = read_cached_stock_daily(
        cache_root=args.cache_root,
        as_of_date=args.date,
    )
    states = calculate_peak_pullback_rebound_states(
        daily,
        as_of_date=args.date,
        peak_lookback_days=args.peak_lookback_days,
        max_rebound_days=args.max_rebound_days,
        min_pullback_pct=args.min_pullback_pct,
        min_second_peak_ratio=args.min_second_peak_ratio,
        rebound_end_drawdown_pct=args.rebound_end_drawdown_pct,
        rebound_ended_state_days=args.rebound_ended_state_days,
        stock_codes=args.stock_code,
        stock_basic=basic,
        include_st=args.include_st,
    )
    report = build_peak_pullback_rebound_state_report(
        states,
        as_of_date=args.date,
        peak_lookback_days=args.peak_lookback_days,
        max_rebound_days=args.max_rebound_days,
        min_pullback_pct=args.min_pullback_pct,
        min_second_peak_ratio=args.min_second_peak_ratio,
        rebound_end_drawdown_pct=args.rebound_end_drawdown_pct,
        rebound_ended_state_days=args.rebound_ended_state_days,
    )
    if args.write_report:
        csv_path, json_path, markdown_path = save_peak_pullback_rebound_state_report(
            report,
            states,
            records_root=args.records_root,
        )
        output = {
            "csv": str(csv_path),
            "json": str(json_path),
            "markdown": str(markdown_path),
            "summary": report["summary"],
            "latest_states": report["latest_states"],
        }
    else:
        output = report
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
