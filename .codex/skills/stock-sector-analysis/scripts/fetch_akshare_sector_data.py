#!/usr/bin/env python
"""Fetch optional A-share sector fund-flow data with AKShare.

Prints JSON to stdout. This helper avoids Tushare and depends on public
data sources exposed through AKShare. Upstream schemas may change.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime

TODAY = "\u4eca\u65e5"
FIVE_DAYS = "5\u65e5"
TEN_DAYS = "10\u65e5"
INDUSTRY_FUND_FLOW = "\u884c\u4e1a\u8d44\u91d1\u6d41"
CONCEPT_FUND_FLOW = "\u6982\u5ff5\u8d44\u91d1\u6d41"


def dataframe_to_records(df, limit: int):
    df = df.head(limit)
    return json.loads(df.to_json(orient="records", force_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument(
        "--indicator",
        default=TODAY,
        choices=[TODAY, FIVE_DAYS, TEN_DAYS],
        help="AKShare indicator for Eastmoney sector fund-flow ranking.",
    )
    args = parser.parse_args()

    try:
        import akshare as ak
    except ImportError:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "akshare is not installed. Install with: pip install akshare",
                },
                ensure_ascii=False,
            )
        )
        return 1

    payload = {
        "ok": True,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "indicator": args.indicator,
        "data": {},
        "notes": [
            "Data is sourced through AKShare public interfaces.",
            "Fund-flow fields are vendor-specific and should be cross-checked.",
        ],
    }

    calls = {
        "industry_fund_flow": (INDUSTRY_FUND_FLOW,),
        "concept_fund_flow": (CONCEPT_FUND_FLOW,),
    }

    for key, (sector_type,) in calls.items():
        try:
            df = ak.stock_sector_fund_flow_rank(
                indicator=args.indicator,
                sector_type=sector_type,
            )
            payload["data"][key] = dataframe_to_records(df, args.limit)
        except Exception as exc:  # noqa: BLE001 - report upstream failures clearly
            payload["data"][key] = {
                "error": str(exc),
                "sector_type": sector_type,
            }

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
