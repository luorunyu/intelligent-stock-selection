"""筛选方案一命令行。"""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_selection.screening_schemes.plan_one.report import (
    build_plan_one_report,
    save_plan_one_report,
)
from stock_selection.screening_schemes.plan_one.scheme import run_plan_one
from stock_selection.strategies.stock_industry_direction.screen import (
    load_industry_dictionary,
    read_cached_direction_inputs,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plan one: screen L2 industries, then screen their stocks by direction ratio."
    )
    parser.add_argument("--date", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--industry-window", type=int, default=20)
    parser.add_argument("--min-rise", type=float, default=0.05)
    parser.add_argument("--min-days", type=int, default=5)
    parser.add_argument("--min-up-days", type=int, default=4)
    parser.add_argument("--stock-window", type=int, default=20)
    parser.add_argument("--min-direction-ratio", type=float, default=0.60)
    parser.add_argument("--include-st", action="store_true")
    parser.add_argument("--cache-root", default=None)
    parser.add_argument(
        "--dictionary",
        default="data_cache/tushare/static/sw_industry_stock_dictionary.json",
    )
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--records-root", default="analysis_records")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stock_daily, industry_daily, stock_basic = read_cached_direction_inputs(
        as_of_date=args.date,
        cache_root=args.cache_root,
    )
    qualified_industries, stock_metrics, selected = run_plan_one(
        industry_daily,
        stock_daily,
        load_industry_dictionary(args.dictionary),
        as_of_date=args.date,
        industry_window=args.industry_window,
        min_rise_from_low=args.min_rise,
        min_days_since_low=args.min_days,
        min_up_days=args.min_up_days,
        stock_window=args.stock_window,
        min_same_direction_ratio=args.min_direction_ratio,
        stock_basic=stock_basic,
        include_st=args.include_st,
    )
    report = build_plan_one_report(
        qualified_industries,
        stock_metrics,
        selected,
        as_of_date=args.date,
        industry_window=args.industry_window,
        min_rise_from_low=args.min_rise,
        min_days_since_low=args.min_days,
        min_up_days=args.min_up_days,
        stock_window=args.stock_window,
        min_same_direction_ratio=args.min_direction_ratio,
    )
    if args.write_report:
        csv_path, json_path, markdown_path = save_plan_one_report(
            report,
            selected,
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
