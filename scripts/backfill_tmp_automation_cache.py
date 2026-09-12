"""把历史自动化留下的 CSV 补录为正式 Tushare Parquet 缓存。

这是一次性迁移工具；正常盘后流程只应读取正式缓存，不再依赖临时 CSV。
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

import pandas as pd
from pandas.errors import EmptyDataError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_selection.data.tushare_cache import write_dataset


TMP_PATTERN = re.compile(r"^(?P<api>[A-Za-z0-9_]+)_(?P<date>20\d{6})(?:_main)?\.csv$")
API_ALIASES = {
    "index_daily": "index_daily_selected",
}


def parse_args() -> argparse.Namespace:
    """定义迁移范围、缓存目录和覆盖策略的命令行参数。"""
    parser = argparse.ArgumentParser(description="Backfill formal Tushare cache from automation CSV files.")
    parser.add_argument("--tmp-dir", default="analysis_records/_tmp_automation_a")
    parser.add_argument("--cache-root", default=None)
    parser.add_argument("--date", action="append", dest="dates", help="Only backfill this YYYYMMDD date. Repeatable.")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    """筛选符合命名规范的 CSV，跳过空文件并写入正式缓存。"""
    args = parse_args()
    tmp_dir = Path(args.tmp_dir)
    if not tmp_dir.exists():
        raise SystemExit(f"tmp dir not found: {tmp_dir}")

    selected_dates = set(args.dates or [])
    count = 0
    # 文件名同时携带接口名与交易日；只迁移能够可靠解析的历史文件。
    for csv_path in sorted(tmp_dir.glob("*.csv")):
        match = TMP_PATTERN.match(csv_path.name)
        if not match:
            continue
        trade_date = match.group("date")
        if selected_dates and trade_date not in selected_dates:
            continue
        api_name = API_ALIASES.get(match.group("api"), match.group("api"))
        # 空 CSV 没有可恢复的数据，记录后继续处理下一份文件。
        try:
            data = pd.read_csv(csv_path)
        except EmptyDataError:
            print(f"skip empty_csv {csv_path}")
            continue
        if data.empty:
            print(f"skip empty {csv_path}")
            continue
        output_path = write_dataset(data, api_name, trade_date, cache_root=args.cache_root)
        print(f"ok {api_name} {trade_date} rows={len(data)} path={output_path}")
        count += 1
    print(f"backfilled={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
