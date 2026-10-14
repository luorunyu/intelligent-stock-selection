#!/usr/bin/env python3
"""Generate all strategy-one L1/L2/L3 industry charts and a browse page."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_selection.strategies.industry_index_rebound.visualization import (
    DEFAULT_DICTIONARY_PATH,
    generate_industry_rebound_charts,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="截止日期，格式 YYYYMMDD；默认使用最新缓存日期")
    parser.add_argument("--window", type=int, default=20, help="策略一最近交易日窗口")
    parser.add_argument("--dictionary", default=str(DEFAULT_DICTIONARY_PATH))
    parser.add_argument("--cache-root", default=None)
    parser.add_argument("--output", default=None, help="图表输出目录")
    parser.add_argument("--initial-bars", type=int, default=120, help="图表初始显示的交易日数量，0 表示全部")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = generate_industry_rebound_charts(
        as_of_date=args.date,
        window=args.window,
        dictionary_path=args.dictionary,
        cache_root=args.cache_root,
        output_directory=args.output,
        initial_bars=args.initial_bars or None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
