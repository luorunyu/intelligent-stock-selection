"""兼容启动入口；方案一实现位于其独立目录。"""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_selection.screening_schemes.plan_one.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
