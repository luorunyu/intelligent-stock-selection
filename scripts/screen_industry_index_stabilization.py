"""Compatibility entry point for strategy three."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_selection.strategies.industry_index_stabilization.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
