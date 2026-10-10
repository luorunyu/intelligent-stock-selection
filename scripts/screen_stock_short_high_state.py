"""Compatibility entry point for strategy five."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_selection.strategies.stock_short_high_state.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
