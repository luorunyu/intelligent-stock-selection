"""在策略一目录生成申万 L1/L2/L3 行业 K 线和行业查询入口。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_selection.data.tushare_cache import DEFAULT_CACHE_ROOT
from stock_selection.strategies.industry_index_rebound.visualization import (
    generate_industry_rebound_charts,
)


DEFAULT_OUTPUT_ROOT = Path(__file__).resolve().parent / "output"


def _project_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="截止日期，YYYYMMDD；默认使用最新缓存日期。")
    parser.add_argument("--window", type=int, default=20, help="原行业图的近期低点统计窗口，默认20个交易日。")
    parser.add_argument("--cache-root", type=_project_path, default=_project_path(DEFAULT_CACHE_ROOT))
    parser.add_argument("--dictionary", type=_project_path, default=None, help="默认读取 cache-root/static 下的行业字典。")
    parser.add_argument("--output-root", type=_project_path, default=DEFAULT_OUTPUT_ROOT, help="默认保存到策略一的 output/日期/charts/。")
    parser.add_argument("--output", type=_project_path, default=None, help="直接指定图表输出目录，优先于 output-root。")
    parser.add_argument("--initial-bars", type=int, default=120, help="初始显示的交易日数量，0表示全部。")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    dictionary = args.dictionary or args.cache_root / "static" / "sw_industry_stock_dictionary.json"
    result = generate_industry_rebound_charts(
        as_of_date=args.date,
        window=args.window,
        dictionary_path=dictionary,
        cache_root=args.cache_root,
        output_root=args.output_root,
        output_directory=args.output,
        initial_bars=args.initial_bars or None,
        page_title="策略一：申万行业近期低点反弹",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
