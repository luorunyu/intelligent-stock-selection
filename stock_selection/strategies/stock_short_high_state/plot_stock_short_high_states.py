"""Generate strategy-five K-line charts from a previously saved states CSV."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_selection.strategies.stock_short_high_state.report import (
    DEFAULT_OUTPUT_ROOT,
)
from stock_selection.strategies.stock_short_high_state.visualization import (
    DEFAULT_CODES_FILE,
    generate_stock_short_state_charts,
    resolve_states_csv,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="State date, YYYYMMDD")
    parser.add_argument("--states-csv", default=None)
    parser.add_argument(
        "--codes-file",
        default=str(DEFAULT_CODES_FILE),
        help="Stock code list; an empty or comments-only file selects all stocks in states.csv",
    )
    parser.add_argument("--cache-root", default=None)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--output", default=None)
    parser.add_argument("--initial-bars", type=int, default=250)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    states_csv = resolve_states_csv(
        states_csv=args.states_csv,
        as_of_date=args.date,
        output_root=args.output_root,
    )
    result = generate_stock_short_state_charts(
        states_csv=states_csv,
        codes_file=args.codes_file,
        cache_root=args.cache_root,
        output_directory=args.output,
        initial_bars=args.initial_bars or None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
