import pandas as pd

from stock_selection.strategies.industry_index_rebound.screen import (
    calculate_industry_index_screen,
    normalise_industry_index_data,
    screen_industry_index_rebound,
)


def _data() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": ["801010.SI"] * 8,
            "name": ["农林牧渔"] * 8,
            "level": ["L1"] * 8,
            "trade_date": pd.date_range("2026-09-01", periods=8).strftime("%Y%m%d"),
            "close": [100, 98, 96, 97, 99, 98, 100, 102],
            "pct_change": [0, -2, -2.0408, 1.0417, 2.0619, -1.0101, 2.0408, 2],
        }
    )


def test_calculates_recent_low_and_simple_up_down_sums():
    result = calculate_industry_index_screen(
        _data(),
        as_of_date="20260908",
        window=8,
    ).iloc[0]

    assert result["window_low_date"] == "20260903"
    assert result["days_since_low"] == 5
    assert result["rise_from_low_pct"] == 0.0625
    assert result["up_days_after_low"] == 4
    assert round(result["up_pct_sum_after_low"], 4) == round(1.0417 + 2.0619 + 2.0408 + 2, 4)
    assert result["down_days_after_low"] == 1
    assert round(result["down_pct_sum_after_low"], 4) == -1.0101
    assert result["flat_days_after_low"] == 0


def test_latest_equal_low_is_used_and_short_history_is_marked():
    data = _data().iloc[[0, 1, 2, 5, 6, 7]].copy()
    data.loc[data.index[-1], "close"] = 96
    result = calculate_industry_index_screen(
        data,
        as_of_date="20260908",
        window=8,
    ).iloc[0]

    assert result["data_quality"] == "insufficient_history"
    assert pd.isna(result["days_since_low"])


def test_screen_applies_simple_thresholds():
    metrics = calculate_industry_index_screen(
        _data(),
        as_of_date="20260908",
        window=8,
    )
    result = screen_industry_index_rebound(metrics, min_rise_from_low=0.05, min_up_days=3)

    assert len(result) == 1


def test_normalise_supports_tushare_pct_chg_alias():
    result = normalise_industry_index_data(_data().rename(columns={"pct_change": "pct_chg"}))

    assert result["pct_chg"].notna().all()
