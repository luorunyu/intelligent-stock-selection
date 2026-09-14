"""采集或回填个股日K、申万行业日K和申万三级映射。"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_selection.data.tushare_collector import TushareCollector


def parse_args() -> argparse.Namespace:
    """定义静态初始化、每日增量和历史回填命令。"""
    parser = argparse.ArgumentParser(description="Collect stock and Shenwan industry data into the local cache.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    static_parser = subparsers.add_parser("static", help="Collect SW2021 static datasets and rebuild membership.")
    _add_common_options(static_parser)

    daily_parser = subparsers.add_parser("daily", help="Collect one complete market trading day.")
    _add_common_options(daily_parser)
    daily_parser.add_argument("--date", default="latest", help="Trade date in YYYYMMDD, or latest.")

    backfill_parser = subparsers.add_parser("backfill", help="Backfill complete market trading days.")
    _add_common_options(backfill_parser)
    backfill_parser.add_argument("--start", required=True, help="Start date in YYYYMMDD.")
    backfill_parser.add_argument("--end", required=True, help="End date in YYYYMMDD.")
    return parser.parse_args()


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--cache-root", default=None, help="Override cache root directory.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing cached datasets.")
    parser.add_argument("--include-unprobed", action="store_true", help="Ignore available_apis.json filtering.")


def main() -> int:
    """执行静态初始化、每日增量或历史回填。"""
    args = parse_args()
    collector = TushareCollector(cache_root=args.cache_root)
    available_only = not args.include_unprobed

    if args.command == "static":
        results = collector.collect_sw_static(
            force=args.force,
            available_only=available_only,
        )
    elif args.command == "daily":
        trade_date = None if args.date == "latest" else args.date
        results = collector.collect_market_daily(
            trade_date,
            force=args.force,
            available_only=available_only,
        )
    else:
        results = []
        for trade_date in collector.trade_dates(args.start, args.end):
            results.extend(
                collector.collect_market_daily(
                    trade_date,
                    force=args.force,
                    available_only=available_only,
                )
            )

    for result in results:
        detail = f" rows={result.rows}" if result.rows is not None else ""
        path = f" path={result.path}" if result.path else ""
        message = f" message={result.message}" if result.message else ""
        print(f"{result.status:7s} {result.api_name}{detail}{path}{message}")

    return 0 if all(result.status != "failed" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
