"""CLI for stock short-side high-region state classification."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_selection.strategies.stock_short_high_state.report import (
    build_stock_short_state_report,
    save_stock_short_state_report,
)
from stock_selection.strategies.stock_short_high_state.screen import (
    DEFAULT_CONFIRM_DRAWDOWN_PCT,
    DEFAULT_HIGH_HEAT_DAYS,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_REBOUND_PCT,
    DEFAULT_TROUGH_HEAT_DAYS,
    calculate_stock_short_states,
    read_cached_stock_daily,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Classify close-only high regions and rebound-peak states."
    )
    parser.add_argument("--date", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--cache-root", default=None)
    parser.add_argument("--stock-code", action="append", default=None)
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument(
        "--confirm-drawdown-pct",
        type=float,
        default=DEFAULT_CONFIRM_DRAWDOWN_PCT,
    )
    parser.add_argument("--rebound-pct", type=float, default=DEFAULT_REBOUND_PCT)
    parser.add_argument(
        "--high-heat-days", type=int, default=DEFAULT_HIGH_HEAT_DAYS
    )
    parser.add_argument(
        "--trough-heat-days", type=int, default=DEFAULT_TROUGH_HEAT_DAYS
    )
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
    states = calculate_stock_short_states(
        daily,
        as_of_date=args.date,
        lookback_days=args.lookback_days,
        confirm_drawdown_pct=args.confirm_drawdown_pct,
        rebound_pct=args.rebound_pct,
        high_heat_days=args.high_heat_days,
        trough_heat_days=args.trough_heat_days,
        stock_codes=args.stock_code,
        stock_basic=basic,
        include_st=args.include_st,
    )
    report = build_stock_short_state_report(
        states,
        as_of_date=args.date,
        lookback_days=args.lookback_days,
        confirm_drawdown_pct=args.confirm_drawdown_pct,
        rebound_pct=args.rebound_pct,
        high_heat_days=args.high_heat_days,
        trough_heat_days=args.trough_heat_days,
    )
    if args.write_report:
        csv_path, json_path, markdown_path = save_stock_short_state_report(
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
