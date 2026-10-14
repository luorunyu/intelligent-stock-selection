"""策略二命令行：筛选个股与申万行业的同方向比例。"""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_selection.strategies.stock_industry_direction.report import (
    build_stock_industry_direction_report,
    save_stock_industry_direction_history_batches,
    save_stock_industry_direction_report,
    summarize_stock_industry_direction_history_batches,
)
from stock_selection.strategies.stock_industry_direction.screen import (
    calculate_stock_industry_direction,
    iter_stock_industry_direction_history,
    load_industry_dictionary,
    read_cached_direction_inputs,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Calculate rolling direction, correlation, beta, and R-squared metrics "
            "between stocks and SW2021 L2/L3 indexes."
        )
    )
    parser.add_argument(
        "--date",
        default=date.today().strftime("%Y%m%d"),
        help="History end date and latest snapshot date.",
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help="Optional first output date; earlier loaded returns can still seed its window.",
    )
    parser.add_argument(
        "--level",
        choices=("L2", "L3", "l2", "l3"),
        default="L2",
        help="Industry level, default L2.",
    )
    parser.add_argument("--window", type=int, default=20)
    parser.add_argument("--industry-code", action="append", default=None)
    parser.add_argument(
        "--min-ratio",
        type=float,
        default=0.60,
        help="Minimum same-direction ratio, default 0.60.",
    )
    parser.add_argument("--include-st", action="store_true")
    parser.add_argument("--cache-root", default=None)
    parser.add_argument(
        "--dictionary",
        default="data_cache/tushare/static/sw_industry_stock_dictionary.json",
    )
    parser.add_argument("--max-report-results", type=int, default=200)
    parser.add_argument(
        "--latest-only",
        action="store_true",
        help="Skip the full rolling history and keep the previous latest-snapshot behavior.",
    )
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--records-root", default="analysis_records")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    level = args.level.upper()
    stock_daily, industry_daily, stock_basic = read_cached_direction_inputs(
        as_of_date=args.date,
        cache_root=args.cache_root,
    )
    dictionary = load_industry_dictionary(args.dictionary)
    metrics = calculate_stock_industry_direction(
        stock_daily,
        industry_daily,
        dictionary,
        level=level,
        as_of_date=args.date,
        window=args.window,
        stock_basic=stock_basic,
        include_st=args.include_st,
        industry_codes=args.industry_code,
    )
    history_batches = None
    if not args.latest_only:
        history_batches = iter_stock_industry_direction_history(
            stock_daily,
            industry_daily,
            dictionary,
            level=level,
            window=args.window,
            start_date=args.start_date,
            end_date=args.date,
            stock_basic=stock_basic,
            include_st=args.include_st,
            industry_codes=args.industry_code,
        )
    report = build_stock_industry_direction_report(
        metrics,
        as_of_date=args.date,
        level=level,
        window=args.window,
        min_same_direction_ratio=args.min_ratio,
        max_results=args.max_report_results,
        industry_codes=tuple(args.industry_code) if args.industry_code else None,
    )
    if args.write_report:
        csv_path, json_path, markdown_path = save_stock_industry_direction_report(
            report,
            metrics,
            records_root=args.records_root,
        )
        output: dict[str, object] = {
            "csv": str(csv_path),
            "json": str(json_path),
            "markdown": str(markdown_path),
            "summary": report["summary"],
        }
        if history_batches is not None:
            (
                history_path,
                history_metadata_path,
                history_summary,
            ) = save_stock_industry_direction_history_batches(
                history_batches,
                end_date=args.date,
                level=level,
                window=args.window,
                industry_codes=(
                    tuple(args.industry_code) if args.industry_code else None
                ),
                records_root=args.records_root,
            )
            output["history_parquet"] = str(history_path)
            output["history_metadata"] = str(history_metadata_path)
            output["history_summary"] = history_summary
    else:
        output = report
        if history_batches is not None:
            output["history_summary"] = (
                summarize_stock_industry_direction_history_batches(history_batches)
            )
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
