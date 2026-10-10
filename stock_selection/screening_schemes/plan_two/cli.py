"""Command line interface for screening plan two."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_selection.screening_schemes.plan_two.lending import (
    DEFAULT_LENDING_ROOT,
    read_lending_file,
    resolve_lending_file,
)
from stock_selection.screening_schemes.plan_two.report import (
    build_plan_two_report,
    save_plan_two_report,
)
from stock_selection.screening_schemes.plan_two.scheme import run_plan_two
from stock_selection.strategies.stock_short_high_state.screen import (
    DEFAULT_CONFIRM_DRAWDOWN_PCT,
    DEFAULT_HIGH_HEAT_DAYS,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_REBOUND_PCT,
    DEFAULT_TROUGH_HEAT_DAYS,
    read_cached_stock_daily,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plan two: classify lendable stocks with strategy five states."
    )
    parser.add_argument("--date", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--lending-file", default=None)
    parser.add_argument("--lending-root", default=str(DEFAULT_LENDING_ROOT))
    parser.add_argument("--cache-root", default=None)
    parser.add_argument("--min-available-quantity", type=int, default=1)
    parser.add_argument("--required-lending-status", default="正常")
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
    parser.add_argument("--allow-stale-market-data", action="store_true")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--records-root", default="analysis_records")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    lending_path = resolve_lending_file(
        as_of_date=args.date,
        lending_file=args.lending_file,
        lending_root=args.lending_root,
    )
    lending_data = read_lending_file(lending_path)
    stock_daily, stock_basic = read_cached_stock_daily(
        cache_root=args.cache_root,
        as_of_date=args.date,
    )
    eligible, latest_states, result = run_plan_two(
        lending_data,
        stock_daily,
        as_of_date=args.date,
        min_available_quantity=args.min_available_quantity,
        required_lending_status=args.required_lending_status,
        lookback_days=args.lookback_days,
        confirm_drawdown_pct=args.confirm_drawdown_pct,
        rebound_pct=args.rebound_pct,
        high_heat_days=args.high_heat_days,
        trough_heat_days=args.trough_heat_days,
        stock_basic=stock_basic,
        include_st=args.include_st,
        allow_stale_market_data=args.allow_stale_market_data,
    )
    report = build_plan_two_report(
        eligible,
        latest_states,
        result,
        as_of_date=args.date,
        lending_file=str(lending_path),
        min_available_quantity=args.min_available_quantity,
        required_lending_status=args.required_lending_status,
        lookback_days=args.lookback_days,
        confirm_drawdown_pct=args.confirm_drawdown_pct,
        rebound_pct=args.rebound_pct,
        high_heat_days=args.high_heat_days,
        trough_heat_days=args.trough_heat_days,
        include_st=args.include_st,
        allow_stale_market_data=args.allow_stale_market_data,
    )
    if args.write_report:
        csv_path, json_path, markdown_path = save_plan_two_report(
            report,
            result,
            records_root=args.records_root,
        )
        output = {
            "csv": str(csv_path),
            "json": str(json_path),
            "markdown": str(markdown_path),
            "summary": report["summary"],
            "short_scope": report["short_scope"],
            "alerts": report["alerts"],
        }
    else:
        output = report
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
