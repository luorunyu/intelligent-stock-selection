"""命令行入口：准备盘后复盘所需的正式 Tushare 数据包与清单。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_selection.data.after_close import prepare_after_close_data


def parse_args() -> argparse.Namespace:
    """定义请求日期、缓存目录和补数策略。"""
    parser = argparse.ArgumentParser(description="Prepare formal after-close Tushare cache bundle.")
    parser.add_argument("--date", default=None, help="Requested date in YYYYMMDD. Defaults to today.")
    parser.add_argument("--cache-root", default=None, help="Override cache root.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing cached datasets.")
    parser.add_argument("--lookback-days", type=int, default=10)
    parser.add_argument("--available-only", action="store_true", help="Respect available_apis.json filtering.")
    return parser.parse_args()


def main() -> int:
    """采集最新完整交易日的数据，并以 JSON 输出 manifest 供后续流程使用。"""
    args = parse_args()
    manifest = prepare_after_close_data(
        args.date,
        cache_root=args.cache_root,
        force=args.force,
        lookback_days=args.lookback_days,
        available_only=args.available_only,
    )
    print(json.dumps(manifest.__dict__, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
