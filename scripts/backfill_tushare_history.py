"""Backfill a date range of core Tushare history into the formal cache."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sys
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_selection.data.after_close import DEFAULT_INDEX_CODES
from stock_selection.data.tushare_cache import dataset_exists, write_dataset
from stock_selection.data.tushare_collector import TushareCollector


DEFAULT_APIS = ["daily", "daily_basic", "stk_limit", "moneyflow", "sw_daily"]


@dataclass(frozen=True)
class IndexBackfillResult:
    """Summary of bulk index downloads split into daily cache files."""

    written_dates: list[str]
    skipped_dates: list[str]
    incomplete_dates: dict[str, list[str]]


def parse_date(value: str) -> str:
    """Accept only compact calendar dates so cache keys remain canonical."""
    try:
        parsed = datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYYMMDD") from exc
    return parsed.strftime("%Y%m%d")


def open_trade_dates(collector: TushareCollector, start_date: str, end_date: str) -> list[str]:
    """Fetch the exchange calendar once and return sorted open dates."""
    calendar = collector.fetch(
        "trade_cal",
        {
            "exchange": "SSE",
            "start_date": start_date,
            "end_date": end_date,
            "is_open": "1",
        },
    )
    if not isinstance(calendar, pd.DataFrame) or calendar.empty or "cal_date" not in calendar:
        raise RuntimeError("trade_cal returned no open trading days")
    return sorted(calendar["cal_date"].astype(str).unique().tolist())


def backfill_selected_indexes(
    collector: TushareCollector,
    trade_dates: list[str],
    *,
    index_codes: list[str],
    force: bool = False,
) -> IndexBackfillResult:
    """Download each selected index once for the range, then cache complete daily slices."""
    target_dates = [
        trade_date
        for trade_date in trade_dates
        if force
        or not dataset_exists(
            "index_daily_selected",
            trade_date,
            cache_root=collector.cache_root,
        )
    ]
    skipped = [trade_date for trade_date in trade_dates if trade_date not in target_dates]
    if not target_dates:
        return IndexBackfillResult([], skipped, {})

    frames: list[pd.DataFrame] = []
    start_date, end_date = min(target_dates), max(target_dates)
    for ts_code in index_codes:
        data = collector.fetch(
            "index_daily",
            {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
        )
        if isinstance(data, pd.DataFrame) and not data.empty:
            frames.append(data)

    if not frames:
        return IndexBackfillResult([], skipped, {date: list(index_codes) for date in target_dates})

    combined = pd.concat(frames, ignore_index=True)
    required_columns = {"ts_code", "trade_date"}
    missing_columns = required_columns - set(combined.columns)
    if missing_columns:
        raise RuntimeError(f"index_daily is missing columns: {', '.join(sorted(missing_columns))}")
    combined["trade_date"] = combined["trade_date"].astype(str)

    written: list[str] = []
    incomplete: dict[str, list[str]] = {}
    expected_codes = set(index_codes)
    for trade_date in target_dates:
        daily = combined.loc[combined["trade_date"] == trade_date].copy()
        present_codes = set(daily["ts_code"].astype(str))
        missing_codes = sorted(expected_codes - present_codes)
        if missing_codes:
            incomplete[trade_date] = missing_codes
            continue
        write_dataset(
            daily,
            "index_daily_selected",
            trade_date,
            cache_root=collector.cache_root,
        )
        written.append(trade_date)

    return IndexBackfillResult(written, skipped, incomplete)


def parse_args() -> argparse.Namespace:
    """Define the range, datasets, cache location, and overwrite behavior."""
    parser = argparse.ArgumentParser(description="Backfill core Tushare history by trading day.")
    parser.add_argument("--start-date", required=True, type=parse_date, help="First date, YYYYMMDD.")
    parser.add_argument("--end-date", required=True, type=parse_date, help="Last date, YYYYMMDD.")
    parser.add_argument("--api", action="append", dest="apis", help="Core API to backfill. Repeatable.")
    parser.add_argument("--cache-root", default=None, help="Override cache root directory.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing cached datasets.")
    parser.add_argument(
        "--workers",
        type=int,
        default=6,
        help="Concurrent trading-day tasks; all calls still share the project rate limiter.",
    )
    parser.add_argument("--include-unprobed", action="store_true", help="Ignore available_apis.json filtering.")
    parser.add_argument("--skip-indexes", action="store_true", help="Do not backfill selected broad indexes.")
    return parser.parse_args()


def main() -> int:
    """Backfill missing daily files and report any remaining coverage gaps."""
    args = parse_args()
    if args.start_date > args.end_date:
        raise SystemExit("--start-date must not be later than --end-date")
    if args.workers < 1:
        raise SystemExit("--workers must be at least 1")

    api_names = args.apis or list(DEFAULT_APIS)
    collector = TushareCollector(cache_root=args.cache_root)
    trade_dates = open_trade_dates(collector, args.start_date, args.end_date)
    print(
        f"range={args.start_date}..{args.end_date} trade_dates={len(trade_dates)} "
        f"apis={','.join(api_names)}"
    )

    failures: list[str] = []
    completed_datasets = 0
    total_datasets = len(trade_dates) * len(api_names)

    def collect_date(trade_date: str) -> tuple[str, list[Any]]:
        results = collector.collect_daily(
            trade_date,
            api_names=api_names,
            force=args.force,
            available_only=not args.include_unprobed,
        )
        return trade_date, results

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(collect_date, trade_date) for trade_date in trade_dates]
        for position, future in enumerate(as_completed(futures), start=1):
            trade_date, results = future.result()
            completed_datasets += len(results)
            for result in results:
                exists = dataset_exists(result.api_name, trade_date, cache_root=args.cache_root)
                if not exists:
                    failures.append(f"{trade_date}:{result.api_name}:{result.status}")
            print(
                f"[{position:03d}/{len(trade_dates):03d}] {trade_date} "
                f"datasets={completed_datasets}/{total_datasets} gaps={len(failures)}"
            )

    index_result = IndexBackfillResult([], [], {})
    if not args.skip_indexes:
        index_result = backfill_selected_indexes(
            collector,
            trade_dates,
            index_codes=list(DEFAULT_INDEX_CODES),
            force=args.force,
        )
        failures.extend(f"{date}:index_daily_selected:incomplete" for date in index_result.incomplete_dates)

    print(
        f"complete trade_dates={len(trade_dates)} gaps={len(failures)} "
        f"index_written={len(index_result.written_dates)} "
        f"index_skipped={len(index_result.skipped_dates)}"
    )
    if failures:
        print("remaining_gaps=" + ",".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
