#!/usr/bin/env python3
"""Render a cached SW industry and its largest constituents to one HTML chart."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_selection.visualization.industry_kline import (
    load_cached_industry_klines,
    write_industry_kline_html,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--industry-code", required=True, help="SW L1/L2/L3 code, for example 801080.SI")
    parser.add_argument("--start-date", default=None, help="First cached trade date, YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--end-date", default=None, help="Last cached trade date, YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--top-n", type=int, default=5, help="Number of constituents ranked by latest circ_mv")
    parser.add_argument("--cache-root", default=None, help="Override the formal Tushare cache root")
    parser.add_argument("--output", type=Path, default=None, help="Output HTML path")
    parser.add_argument("--title", default=None, help="Optional chart title")
    parser.add_argument("--panel-height", type=int, default=300, help="Pixel height per index/stock panel")
    parser.add_argument("--initial-bars", type=int, default=80, help="Initially visible trading bars; 0 shows all")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    index_bars, stock_bars, stock_info = load_cached_industry_klines(
        args.industry_code,
        start_date=args.start_date,
        end_date=args.end_date,
        top_n=args.top_n,
        cache_root=args.cache_root,
    )
    industry_name = str(index_bars["name"].iloc[0])
    output = args.output or ROOT / "charts" / f"{args.industry_code.replace('.', '_')}_constituents.html"
    path = write_industry_kline_html(
        index_bars,
        stock_bars,
        output,
        index_code=args.industry_code,
        index_name=industry_name,
        stock_info=stock_info,
        title=args.title,
        panel_height=args.panel_height,
        initial_bars=args.initial_bars or None,
    )
    ordered = "\n".join(
        f"  {position}. {row['name']} {row['ts_code']} ({row['circ_mv'] / 10000:.1f}亿)"
        for position, (_, row) in enumerate(stock_info.iterrows(), start=1)
    )
    print(f"已生成: {path.resolve()}")
    print(f"指数: {industry_name} {args.industry_code}")
    print("成分股（按最新流通市值降序）:")
    print(ordered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
