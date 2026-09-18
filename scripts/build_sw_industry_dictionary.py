"""命令行入口：生成申万一、二、三级行业成分股代码大字典。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_selection.data.sw_industry_dictionary import collect_build_and_save_sw_dictionary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect complete SW2021 constituents and save level-1/2/3 stock-code dictionaries."
    )
    parser.add_argument("--output", default=None, help="Output JSON path.")
    parser.add_argument("--cache-root", default=None, help="Override formal Tushare cache root.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    path, payload = collect_build_and_save_sw_dictionary(
        output_path=args.output,
        cache_root=args.cache_root,
    )
    result = {
        "output": str(path),
        "counts": payload["metadata"]["counts"],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
