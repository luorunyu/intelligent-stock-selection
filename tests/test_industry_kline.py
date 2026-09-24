from __future__ import annotations

import pandas as pd
import pytest

from stock_selection.visualization.industry_kline import _common_cached_dates, build_industry_kline_figure


pytest.importorskip("plotly")


def _bars(code: str, base: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": [code] * 3,
            "trade_date": ["20260105", "20260106", "20260107"],
            "open": [base, base + 1, base + 2],
            "high": [base + 2, base + 3, base + 4],
            "low": [base - 1, base, base + 1],
            "close": [base + 1, base + 0.5, base + 3],
            "vol": [100.0, 120.0, 150.0],
        }
    )


def test_panels_are_sorted_by_circ_mv_and_share_x_axis() -> None:
    info = pd.DataFrame(
        {
            "ts_code": ["SMALL.SZ", "LARGE.SH"],
            "name": ["小市值", "大市值"],
            "circ_mv": [100_000.0, 900_000.0],
        }
    )
    figure = build_industry_kline_figure(
        _bars("INDEX.SI", 1000),
        {"SMALL.SZ": _bars("SMALL.SZ", 10), "LARGE.SH": _bars("LARGE.SH", 20)},
        index_code="INDEX.SI",
        index_name="测试行业",
        stock_info=info,
        panel_height=240,
    )

    assert [trace.name for trace in figure.data[::2]] == [
        "测试行业  INDEX.SI  指数",
        "大市值  LARGE.SH  流通市值 90.0亿",
        "小市值  SMALL.SZ  流通市值 10.0亿",
    ]
    assert figure.layout.height == 810
    assert figure.layout.xaxis.matches == "x"
    assert figure.layout.xaxis6.matches == "x"
    assert figure.layout.xaxis.showticklabels is False
    assert figure.layout.xaxis6.showticklabels is True


def test_invalid_ohlc_is_rejected() -> None:
    bad = _bars("BAD.SZ", 10).drop(columns="high")
    with pytest.raises(ValueError, match="missing OHLC"):
        build_industry_kline_figure(_bars("INDEX.SI", 100), {"BAD.SZ": bad})


def test_chart_dates_are_not_limited_by_market_value_snapshot(tmp_path) -> None:
    for api_name, dates in {
        "daily": ["20260105", "20260106", "20260107"],
        "sw_daily": ["20260105", "20260106", "20260107"],
        "daily_basic": ["20260105"],
    }.items():
        folder = tmp_path / api_name
        folder.mkdir()
        for trade_date in dates:
            (folder / f"{trade_date}.parquet").touch()

    assert _common_cached_dates(
        tmp_path, start_date="2026-01-05", end_date="2026-01-07"
    ) == ["20260105", "20260106", "20260107"]
