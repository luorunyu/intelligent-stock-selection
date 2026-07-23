from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_selection.context.theme_discovery import discover_active_themes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover active cross-industry themes from cached A-share data.")
    parser.add_argument("--date", default=None, help="Trade date in YYYYMMDD or YYYY-MM-DD. Defaults to latest cached date.")
    parser.add_argument("--cache-root", default=None, help="Override formal Tushare cache root.")
    parser.add_argument("--records-root", default="analysis_records", help="Analysis records root.")
    parser.add_argument("--seed-path", default=None, help="Override theme seed tag YAML path.")
    parser.add_argument("--lookback", type=int, default=6, help="Cached trading days to compare.")
    parser.add_argument("--top-n-pct", type=int, default=120)
    parser.add_argument("--top-n-amount", type=int, default=120)
    parser.add_argument("--min-theme-score", type=float, default=6.0)
    parser.add_argument("--write", action="store_true", help="Write JSON record to analysis_records/theme_discovery.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = discover_active_themes(
        args.date,
        cache_root=args.cache_root,
        records_root=args.records_root,
        seed_path=args.seed_path,
        lookback=args.lookback,
        top_n_pct=args.top_n_pct,
        top_n_amount=args.top_n_amount,
        min_theme_score=args.min_theme_score,
        write=args.write,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
