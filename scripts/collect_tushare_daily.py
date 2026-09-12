"""命令行入口：按交易日或静态分类采集 Tushare 数据到本地正式缓存。"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_selection.data.tushare_collector import TushareCollector


def parse_args() -> argparse.Namespace:
    """定义日期、接口过滤、缓存位置和强制刷新选项。"""
    parser = argparse.ArgumentParser(description="Collect daily Tushare datasets into the local cache.")
    parser.add_argument("--date", default="latest", help="Trade date in YYYYMMDD, or latest.")
    parser.add_argument("--api", action="append", dest="apis", help="Collect only this API. Repeatable.")
    parser.add_argument("--cache-root", default=None, help="Override cache root directory.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing cached datasets.")
    parser.add_argument("--include-unprobed", action="store_true", help="Ignore available_apis.json filtering.")
    parser.add_argument("--static", action="store_true", help="Collect static/low-frequency datasets instead.")
    return parser.parse_args()


def main() -> int:
    """根据模式调用采集器，并将每个接口的结果打印为可审计摘要。"""
    args = parse_args()
    collector = TushareCollector(cache_root=args.cache_root)

    # 静态表（股票池、行业分类等）与每日横截面使用不同的缓存路径和采集方式。
    if args.static:
        results = collector.collect_static(
            api_names=args.apis,
            force=args.force,
            available_only=not args.include_unprobed,
        )
    else:
        trade_date = None if args.date == "latest" else args.date
        results = collector.collect_daily(
            trade_date,
            api_names=args.apis,
            force=args.force,
            available_only=not args.include_unprobed,
        )

    # 保留每个接口的状态，便于定位权限、网络或数据为空的问题。
    for result in results:
        detail = f" rows={result.rows}" if result.rows is not None else ""
        path = f" path={result.path}" if result.path else ""
        message = f" message={result.message}" if result.message else ""
        print(f"{result.status:7s} {result.api_name}{detail}{path}{message}")

    return 0 if all(result.status != "failed" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
