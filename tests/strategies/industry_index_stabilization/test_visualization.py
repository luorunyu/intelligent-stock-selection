from pathlib import Path

import pandas as pd
import pytest


pytest.importorskip("plotly")

from stock_selection.strategies.industry_index_stabilization.screen import (
    IndustryStabilizationState,
)
from stock_selection.strategies.industry_index_stabilization.visualization import (
    build_industry_stabilization_figure,
    build_stabilization_state_intervals,
    normalise_industry_code,
    read_chart_codes,
)


def _states() -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=7, freq="D")
    return pd.DataFrame(
        {
            "index_code": "801010.SI",
            "name": "农林牧渔",
            "trade_date": dates.strftime("%Y%m%d"),
            "open": [100, 99, 98, 98, 99, 100, 112],
            "high": [101, 100, 99, 99, 100, 101, 113],
            "low": [98, 97, 97, 97, 98, 99, 110],
            "close": [99, 98, 98, 99, 100, 100, 112],
            "vol": [10, 11, 9, 8, 10, 12, 20],
            "state": [
                0,
                int(IndustryStabilizationState.STABILIZED),
                int(IndustryStabilizationState.STABILIZED),
                int(IndustryStabilizationState.STABILIZED),
                int(IndustryStabilizationState.STABILIZED),
                int(IndustryStabilizationState.STABILIZED),
                0,
            ],
            "state_name": [
                "NORMAL",
                "STABILIZED",
                "STABILIZED",
                "STABILIZED",
                "STABILIZED",
                "STABILIZED",
                "NORMAL",
            ],
            "state_description": [
                "普通状态",
                "已确认企稳区间",
                "已确认企稳区间",
                "已确认企稳区间",
                "已确认企稳区间",
                "已确认企稳区间",
                "普通状态",
            ],
            "episode_id": [0, 1, 1, 1, 1, 1, 0],
            "state_days": [0, 1, 2, 3, 4, 5, 0],
            "stable_range_points": [None, 2, 2, 2, 3, 3, None],
        }
    )


def test_normalises_industry_codes_and_reads_code_file(tmp_path: Path):
    path = tmp_path / "codes.txt"
    path.write_text("801010\n801010.SI\nbad\n", encoding="utf-8")

    codes, errors = read_chart_codes(path)

    assert normalise_industry_code("801010") == "801010.SI"
    assert codes == ["801010.SI"]
    assert len(errors) == 1


def test_builds_one_interval_for_confirmed_segment():
    intervals = build_stabilization_state_intervals(_states())

    assert len(intervals) == 1
    assert intervals[0].start_date == "20260102"
    assert intervals[0].end_date == "20260106"
    assert intervals[0].trading_days == 5


def test_builds_kline_with_state_region_and_signal_marker():
    episodes = pd.DataFrame(
        [
            {
                "index_code": "801010.SI",
                "stable_start_date": "20260102",
                "stable_end_date": "20260106",
                "signal_date": "20260106",
                "stable_price_min": 97,
                "stable_price_max": 101,
                "validation_status": "success",
                "target_hit_date": "20260107",
            }
        ]
    )

    figure = build_industry_stabilization_figure(
        _states(),
        episodes,
        index_code="801010.SI",
        index_name="农林牧渔",
    )

    assert len(figure.data) >= 5
    assert len(figure.layout.shapes) >= 3
