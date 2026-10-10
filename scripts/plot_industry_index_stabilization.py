"""Generate strategy-three K-line charts from saved state and episode CSV files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_selection.strategies.industry_index_stabilization.report import (
    DEFAULT_OUTPUT_ROOT,
)
from stock_selection.strategies.industry_index_stabilization.visualization import (
    DEFAULT_CODES_FILE,
    generate_industry_stabilization_charts,
    resolve_state_files,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="State date, YYYYMMDD")
    parser.add_argument("--states-csv", default=None)
    parser.add_argument("--episodes-csv", default=None)
    parser.add_argument("--codes-file", default=str(DEFAULT_CODES_FILE))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--output", default=None)
    parser.add_argument("--initial-bars", type=int, default=250)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    states_csv, episodes_csv = resolve_state_files(
        states_csv=args.states_csv,
        episodes_csv=args.episodes_csv,
        as_of_date=args.date,
        output_root=args.output_root,
    )
    result = generate_industry_stabilization_charts(
        states_csv=states_csv,
        episodes_csv=episodes_csv,
        codes_file=args.codes_file,
        output_directory=args.output,
        initial_bars=args.initial_bars or None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
