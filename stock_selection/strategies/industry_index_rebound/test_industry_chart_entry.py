"""Verify the local industry chart entry and its output defaults."""

import json
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest

from scripts import plot_industry_index_rebound as legacy
from stock_selection.strategies.industry_index_rebound import plot_industry_index_rebound as charts


@pytest.fixture
def cache_root(tmp_path: Path) -> Path:
    root = tmp_path / "cache"
    (root / "sw_daily").mkdir(parents=True)
    (root / "static").mkdir()
    dictionary = {
        "level1": {
            "801010.SI": {
                "name": "农林牧渔",
                "children": {
                    "801016.SI": {
                        "name": "种植业",
                        "children": {
                            "850111.SI": {"name": "种子", "stock_codes": ["000998.SZ"]},
                            "850112.SI": {"name": "粮食种植", "stock_codes": []},
                        },
                    },
                },
            },
        },
    }
    (root / "static" / "sw_industry_stock_dictionary.json").write_text(
        json.dumps(dictionary, ensure_ascii=False), encoding="utf-8",
    )
    for date, close in (("20261008", 100.0), ("20261009", 101.0)):
        pd.DataFrame([
            {"ts_code": code, "trade_date": date, "open": close, "high": close + 1,
             "low": close - 1, "close": close, "vol": 100, "pct_change": 1.0}
            for code in ("801010.SI", "801016.SI", "850111.SI")
        ]).to_parquet(root / "sw_daily" / f"{date}.parquet", index=False)
    pd.DataFrame([
        {"ts_code": "000998.SZ", "symbol": "000998", "name": "隆平高科"},
    ]).to_parquet(root / "static" / "stock_basic.parquet", index=False)
    return root


def test_default_output_is_under_strategy_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["plot_industry_index_rebound"])
    args = charts.parse_args()
    assert args.output_root == Path(charts.__file__).parent / "output"
    assert args.cache_root.is_absolute()
    assert args.output is None


@pytest.mark.parametrize("explicit_output", [False, True])
def test_output_path_assets_and_industry_browser(
    cache_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], explicit_output: bool,
) -> None:
    output_root = tmp_path / "strategy_one_output"
    argv = ["plot_industry_index_rebound", "--cache-root", str(cache_root),
            "--output-root", str(output_root), "--date", "20261009", "--window", "2"]
    expected = output_root / "2026-10-09" / "charts"
    if explicit_output:
        expected = tmp_path / "custom_charts"
        argv.extend(["--output", str(expected)])
    monkeypatch.setattr(sys, "argv", argv)

    assert charts.main() == 0

    result = json.loads(capsys.readouterr().out)
    assert result["output_directory"] == str(expected)
    assert result["chart_count"] == 3
    assert result["missing_count"] == 1
    html = Path(result["index"]).read_text(encoding="utf-8")
    assert "策略一：申万行业近期低点反弹" in html
    assert "隆平高科" in html
    assert "L1 一级行业" in html and "L2 二级行业" in html and "L3 三级行业" in html
    assert (expected / "L2_801016_SI.html").exists()
    assert (expected / "plotly.min.js").exists()


@pytest.mark.parametrize("legacy_entry", [False, True])
def test_direct_script_can_run_from_another_directory(
    tmp_path: Path, legacy_entry: bool,
) -> None:
    path = Path(legacy.__file__ if legacy_entry else charts.__file__)
    result = subprocess.run(
        [sys.executable, str(path), "--help"], cwd=tmp_path,
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "--output-root" in result.stdout
    assert "策略一" in result.stdout


def test_legacy_script_forwards_to_strategy_one() -> None:
    assert legacy.main is charts.main
    assert legacy.parse_args is charts.parse_args
