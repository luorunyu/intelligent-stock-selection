from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_selection.data.tushare_collector import TushareCollector
from stock_selection.data.tushare_permissions import probe_available_apis


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe Tushare API availability for the configured token.")
    parser.add_argument("--date", default="latest", help="Trade date in YYYYMMDD, or latest.")
    parser.add_argument("--api", action="append", dest="apis", help="Probe only this API. Repeatable.")
    parser.add_argument("--cache-root", default=None, help="Override cache root directory.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    trade_date = args.date
    if trade_date == "latest":
        trade_date = TushareCollector(cache_root=args.cache_root).latest_trade_date()

    results = probe_available_apis(trade_date=trade_date, api_names=args.apis, cache_root=args.cache_root)
    for api_name, info in results.items():
        status = "ok" if info.get("available") else "failed"
        reason = f" reason={info.get('reason')}" if info.get("reason") else ""
        rows = f" sample_rows={info.get('sample_rows')}" if info.get("sample_rows") is not None else ""
        print(f"{status:7s} {api_name}{rows}{reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
