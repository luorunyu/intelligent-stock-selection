import pandas as pd
import pytest

from stock_selection.strategies.stock_peak_pullback_rebound.screen import (
    PeakPullbackReboundState,
)
from stock_selection.strategies.stock_peak_pullback_rebound.visualization import (
    ACTIVE_FILL_COLOR,
    ENDED_FILL_COLOR,
    build_peak_pullback_rebound_figure,
    build_state_intervals,
    read_chart_codes,
    resolve_states_csv,
)
from stock_selection.strategies.stock_short_high_state.visualization import (
    merge_bars_and_states,
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
            "close": [10.5, 11.5, 11.0, 10.2, 10.0],
            "total_mv": [120000, 121000, 122000, 123000, 123456],
            "circ_mv": [90000, 91000, 92000, 93000, 100000],
            "state": [
                None,
                int(PeakPullbackReboundState.REBOUND_ACTIVE),
                int(PeakPullbackReboundState.REBOUND_ACTIVE),
                int(PeakPullbackReboundState.REBOUND_ENDED),
                None,
            ],
            "state_name": [
                "",
                "REBOUND_ACTIVE",
                "REBOUND_ACTIVE",
                "REBOUND_ENDED",
                "",
            ],
            "state_description": [
                "",
                "高点回落后反弹中",
                "高点回落后反弹中",
                "反弹结束",
                "",
            ],
            "cycle_id": [0, 1, 1, 1, 0],
            "alert_code": ["", "", "", "", "BREAK_PREVIOUS_HIGH"],
            "alert_message": ["", "", "", "", "反弹突破前高"],
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
            "open": [10.0, 11.0, 11.5, 10.8, 10.2],
            "high": [11.0, 12.0, 12.0, 11.0, 10.5],
            "low": [9.8, 10.8, 10.7, 10.0, 9.8],
            "close": [10.5, 11.5, 11.0, 10.2, 10.0],
            "vol": [100, 120, 90, 150, 180],
        }
    )


def test_reads_strategy_four_codes_file(tmp_path):
    path = tmp_path / "codes.txt"
    path.write_text("# test\n000001\n600000.SH\n", encoding="utf-8")

    codes, errors = read_chart_codes(path)

    assert codes == ["000001.SZ", "600000.SH"]
    assert errors == []


def test_resolves_dated_strategy_four_states_csv(tmp_path):
    path = tmp_path / "2026-10-09" / "states.csv"
    path.parent.mkdir()
    path.write_text("test", encoding="utf-8")

    assert resolve_states_csv(
        as_of_date="20261009",
        output_root=tmp_path,
    ) == path


def test_builds_active_and_ended_intervals():
    intervals = build_state_intervals(_states())

    assert [(item.state_name, item.trading_days) for item in intervals] == [
        ("REBOUND_ACTIVE", 2),
        ("REBOUND_ENDED", 1),
    ]


def test_figure_marks_both_states_without_changing_candle_colors():
    merged = merge_bars_and_states(_bars(), _states())
    figure = build_peak_pullback_rebound_figure(
        merged,
        stock_code="000001.SZ",
        stock_name="测试股票",
    )

    candle = figure.data[0]
    assert candle.increasing.line.color == "#d84a3a"
    assert candle.decreasing.line.color == "#169c72"
    fill_colors = {shape.fillcolor for shape in figure.layout.shapes}
    assert ACTIVE_FILL_COLOR in fill_colors
    assert ENDED_FILL_COLOR in fill_colors
    assert any(trace.name == "突破前高告警" for trace in figure.data)
    assert "12.35亿元" in figure.layout.title.text
    assert "10.00亿元" in figure.layout.title.text
    assert any(trace.name == "状态详情" for trace in figure.data)
