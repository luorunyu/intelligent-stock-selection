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
    save_stock_industry_direction_report,
)
from stock_selection.strategies.stock_industry_direction.screen import (
    calculate_stock_industry_direction,
    load_industry_dictionary,
    read_cached_direction_inputs,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calculate the same-direction ratio between stocks and SW2021 L2/L3 indexes."
    )
    parser.add_argument("--date", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--level", choices=("L2", "L3", "l2", "l3"), required=True)
    parser.add_argument("--window", type=int, default=20)
    parser.add_argument("--industry-code", action="append", default=None)
    parser.add_argument("--min-ratio", type=float, default=None, help="Decimal, for example 0.6.")
    parser.add_argument("--include-st", action="store_true")
    parser.add_argument("--cache-root", default=None)
    parser.add_argument(
        "--dictionary",
        default="data_cache/tushare/static/sw_industry_stock_dictionary.json",
    )
    parser.add_argument("--max-report-results", type=int, default=200)
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
    metrics = calculate_stock_industry_direction(
        stock_daily,
        industry_daily,
        load_industry_dictionary(args.dictionary),
        level=level,
        as_of_date=args.date,
        window=args.window,
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
