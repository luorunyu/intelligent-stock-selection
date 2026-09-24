"""策略一命令行：按申万行业指数近月低点进行简单初筛。"""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_selection.strategies.industry_index_rebound.screen import (
    calculate_industry_index_screen,
    read_cached_industry_index_data,
)
from stock_selection.strategies.industry_index_rebound.report import (
    build_industry_index_report,
    load_industry_hierarchy,
    save_industry_index_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Screen SW industry indexes from their recent low.")
    parser.add_argument("--date", default=date.today().strftime("%Y%m%d"), help="As-of date,例如20260924, default today.")
    parser.add_argument("--cache-root", default=None)
    parser.add_argument("--window", type=int, default=20)
    parser.add_argument("--min-rise", type=float, default=None, help="Minimum rise threshold from low, decimal form.")
    parser.add_argument("--min-days", type=int, default=None, help="Minimum trading days threshold since low.")
    parser.add_argument("--min-up-days", type=int, default=None, help="Minimum up days threshold since low.")
    parser.add_argument(
        "--dictionary",
        default="data_cache/tushare/static/sw_industry_stock_dictionary.json",
        help="Nested SW industry dictionary path.",
    )
    parser.add_argument("--write-report", action="store_true", help="Write JSON and Markdown report files.")
    parser.add_argument("--records-root", default="analysis_records")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data = read_cached_industry_index_data(cache_root=args.cache_root)
    metrics = calculate_industry_index_screen(
        data,
        as_of_date=args.date,
        window=args.window,
    )
    hierarchy = load_industry_hierarchy(args.dictionary)
    report = build_industry_index_report(
        metrics,
        hierarchy,
        as_of_date=args.date,
        window=args.window,
        min_rise_from_low=args.min_rise,
        min_days_since_low=args.min_days,
        min_up_days=args.min_up_days,
    )
    if args.write_report:
        json_path, markdown_path = save_industry_index_report(
            report,
            records_root=args.records_root,
        )
        print(json.dumps({"json": str(json_path), "markdown": str(markdown_path)}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
