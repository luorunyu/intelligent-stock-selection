import pandas as pd
import pytest

from stock_selection.strategies.stock_short_high_state.screen import StockShortState
from stock_selection.strategies.stock_short_high_state.visualization import (
    SHORT_FILL_COLOR,
    build_short_state_intervals,
    build_stock_short_state_figure,
    merge_bars_and_states,
    normalise_stock_code,
    read_chart_codes,
    resolve_states_csv,
)


pytest.importorskip("plotly")


def _states() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ"] * 5,
            "stock_name": ["测试股票"] * 5,
            "trade_date": [
                "20260105",
                "20260106",
                "20260107",
                "20260108",
                "20260109",
            ],
            "close": [10.5, 11.5, 11.0, 12.0, 11.0],
            "total_mv": [120000, 121000, 122000, 123000, 123456],
            "circ_mv": [90000, 91000, 92000, 93000, 100000],
            "state": [
                int(StockShortState.NORMAL),
                int(StockShortState.HIGH_CANDIDATE),
                int(StockShortState.HIGH_CANDIDATE),
                int(StockShortState.PEAK_1),
                int(StockShortState.INVALID_HIGH_NEW_HIGH),
            ],
            "state_name": [
                "NORMAL",
                "HIGH_CANDIDATE",
                "HIGH_CANDIDATE",
                "PEAK_1",
                "INVALID_HIGH_NEW_HIGH",
            ],
            "state_description": [
                "普通状态",
                "最高点候选区域",
                "最高点候选区域",
                "第一波峰区域",
                "最高点确认后再次创新高，原判断失效",
            ],
            "cycle_id": [0, 1, 1, 1, 1],
            "peak_number": [0, 0, 0, 1, 1],
            "heat": [None, 0.1, 0.12, 0.11, None],
            "reference_high_date": ["", "20260106", "20260107", "20260107", "20260107"],
            "reference_high_close": [None, 11.5, 11.5, 11.5, 11.5],
            "reference_low_date": ["", "", "", "20260105", "20260105"],
            "reference_low_close": [None, None, None, 10.5, 10.5],
            "alert_code": ["", "", "", "", "CONFIRMED_HIGH_BROKEN"],
        }
    )


def _bars() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": [
                "20260105",
                "20260106",
                "20260107",
                "20260108",
                "20260109",
            ],
            "open": [10.0, 11.0, 11.5, 11.2, 11.8],
            "high": [11.0, 12.0, 12.0, 12.5, 12.0],
            "low": [9.8, 10.8, 10.7, 11.0, 10.8],
            "close": [10.5, 11.5, 11.0, 12.0, 11.0],
            "vol": [100, 120, 90, 150, 180],
        }
    )


def test_reads_codes_with_comments_suffixes_and_duplicates(tmp_path):
    path = tmp_path / "codes.txt"
    path.write_text(
        "# test\n000001\n600000.SH\n000001.SZ\nbad\n",
        encoding="utf-8",
    )

    codes, errors = read_chart_codes(path)

    assert codes == ["000001.SZ", "600000.SH"]
    assert len(errors) == 1
    assert normalise_stock_code("300750") == "300750.SZ"


def test_resolves_dated_states_csv_inside_strategy_output(tmp_path):
    path = tmp_path / "2026-09-24" / "states.csv"
    path.parent.mkdir()
    path.write_text("test", encoding="utf-8")

    assert resolve_states_csv(
        as_of_date="20260924",
        output_root=tmp_path,
    ) == path


def test_builds_separate_intervals_when_short_state_changes():
    intervals = build_short_state_intervals(_states())

    assert [(item.state_name, item.trading_days) for item in intervals] == [
        ("HIGH_CANDIDATE", 2),
        ("PEAK_1", 1),
    ]


def test_merges_saved_states_with_ohlc_and_checks_close():
    merged = merge_bars_and_states(_bars(), _states())

    assert len(merged) == 5
    assert merged["close"].tolist() == _states()["close"].tolist()
    assert merged["date"].iloc[0] == pd.Timestamp("2026-01-05")


def test_figure_keeps_red_green_candles_and_adds_yellow_regions():
    merged = merge_bars_and_states(_bars(), _states())
    figure = build_stock_short_state_figure(
        merged,
        stock_code="000001.SZ",
        stock_name="测试股票",
    )

    candle = figure.data[0]
    assert candle.increasing.line.color == "#d84a3a"
    assert candle.decreasing.line.color == "#169c72"
    assert any(shape.fillcolor == SHORT_FILL_COLOR for shape in figure.layout.shapes)
    assert any(trace.name == "做空区域开始" for trace in figure.data)
    assert any(trace.name == "策略异常" for trace in figure.data)
    assert "12.35亿元" in figure.layout.title.text
    assert "10.00亿元" in figure.layout.title.text
