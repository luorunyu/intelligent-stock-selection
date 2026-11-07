"""Charts must use the current strategy run, without a stale global code list."""

from pathlib import Path

import pandas as pd
import pytest

from stock_selection.strategies.stock_short_high_state import (
    plot_stock_short_high_states as plot_cli,
    visualization,
)
from stock_selection.strategies.stock_short_high_state.screen import calculate_stock_short_states


@pytest.fixture
def run(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    (cache / "daily").mkdir(parents=True)
    daily = pd.concat([
        pd.DataFrame({
            "ts_code": code, "stock_name": f"股票{code}",
            "trade_date": pd.bdate_range("2026-01-05", periods=len(closes)).strftime("%Y%m%d"),
            "open": closes, "high": [v + 1 for v in closes],
            "low": [v - 1 for v in closes], "close": closes, "vol": 1000,
        })
        for code, closes in {
            "000001.SZ": [100, 80, 90, 150, 135, 130],
            "000002.SZ": [100, 80, 90, 150, 135, 140],
        }.items()
    ], ignore_index=True)
    daily.to_parquet(cache / "daily/20260112.parquet", index=False)
    folder = tmp_path / "output/2026-01-12/charts_20260112_210000_000001"
    folder.mkdir(parents=True)
    states = calculate_stock_short_states(daily, lookback_days=3, min_breakout_gain_pct=0)
    path = folder / "states.csv"
    states.to_csv(path, index=False)
    global_codes = tmp_path / "old_global_codes.txt"
    global_codes.write_text("688521.SH\n600172.SH\n", encoding="utf-8")
    monkeypatch.setattr(visualization, "DEFAULT_CODES_FILE", global_codes)
    return path, cache, global_codes


def _generate(run, **kwargs):
    path, cache, _ = run
    return visualization.generate_stock_short_state_charts(
        states_csv=path, cache_root=cache, **kwargs,
    )


def test_default_samples_current_run_and_creates_a_new_render_directory(run):
    path, _, global_codes = run
    original_codes = global_codes.read_bytes()
    result = _generate(run)
    assert result["errors"] == []
    assert result["states_csv"] == str(path)
    assert result["codes_source"] == "current_run_backtest"
    assert {chart["ts_code"] for chart in result["charts"]} == {"000001.SZ", "000002.SZ"}
    assert result["output_directory"] != str(path.parent)
    assert Path(result["output_directory"]).parent == path.parent.parent
    saved_codes = Path(result["output_directory"]) / visualization.RUN_CODES_FILENAME
    assert result["codes_file"] == str(saved_codes)
    assert set(saved_codes.read_text().splitlines()) == {"000001.SZ", "000002.SZ"}
    assert global_codes.read_bytes() == original_codes
    assert Path(result["index"]).is_file()
    # A second plot of this batch keeps the source run unchanged and creates a
    # separate render directory.
    again = _generate(run)
    assert again["codes_source"] == "current_run_backtest"
    assert again["output_directory"] != result["output_directory"]
    assert again["errors"] == []
    assert len(list(path.parent.parent.glob("charts_*"))) == 3


@pytest.mark.parametrize(("filename", "source"), [
    ("chart_codes.txt", "run_file"),
    ("sampled_chart_codes_20260112_210000_000001.txt", "run_sample_snapshot"),
])
def test_batch_selection_is_preferred_to_global_list(run, filename, source):
    path, _, _ = run
    (path.parent / filename).write_text("000002.SZ\n", encoding="utf-8")
    result = _generate(run)
    assert result["codes_source"] == source
    assert result["errors"] == []
    assert [row["ts_code"] for row in result["charts"]] == ["000002.SZ"]


def test_explicit_list_still_reports_stocks_missing_from_selected_states(run):
    path, _, _ = run
    explicit = path.parent / "custom.txt"
    explicit.write_text("000001.SZ\n688521.SH\n", encoding="utf-8")
    result = _generate(run, codes_file=explicit)
    assert result["codes_source"] == "explicit_file"
    assert result["errors"] == ["688521.SH: not found in states CSV"]
    assert [row["ts_code"] for row in result["charts"]] == ["000001.SZ"]


def test_empty_explicit_list_draws_all_stocks_and_invalid_list_is_rejected(run):
    path, _, _ = run
    explicit = path.parent / "custom.txt"
    explicit.write_text("# all saved stocks\n", encoding="utf-8")
    result = _generate(run, codes_file=explicit)
    assert len(result["charts"]) == 2
    assert result["errors"] == []
    explicit.write_text("invalid-code\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no valid stock codes"):
        _generate(run, codes_file=explicit)


def test_no_trades_uses_current_saved_stocks(run):
    path, _, _ = run
    states = pd.read_csv(path)
    states["entry_signal"] = 0
    states["entry_signal_type"] = ""
    states.to_csv(path, index=False)
    result = _generate(run)
    assert result["codes_source"] == "current_run_all_stocks"
    assert len(result["charts"]) == 2
    assert {row["return_category"] for row in result["charts"]} == {"unclassified"}
    assert result["errors"] == []


def test_explicit_stock_codes_override_batch_file(run):
    path, _, _ = run
    (path.parent / visualization.RUN_CODES_FILENAME).write_text("000002.SZ\n")
    result = _generate(run, stock_codes=["000001"])
    assert result["codes_source"] == "stock_codes"
    assert [row["ts_code"] for row in result["charts"]] == ["000001.SZ"]


def test_plot_cli_default_does_not_inject_shared_global_list(monkeypatch):
    monkeypatch.setattr("sys.argv", ["plot"])
    assert plot_cli.parse_args().codes_file is None
    monkeypatch.setattr("sys.argv", ["plot", "--codes-file", "custom.txt"])
    assert plot_cli.parse_args().codes_file == "custom.txt"
