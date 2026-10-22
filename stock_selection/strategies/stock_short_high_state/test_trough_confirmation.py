import pandas as pd

from stock_selection.strategies.stock_short_high_state.screen import (
    StockShortState,
    calculate_stock_short_states,
)


def _states(closes):
    return calculate_stock_short_states(
        pd.DataFrame({
            "ts_code": "000001.SZ",
            "stock_name": "测试股票",
            "trade_date": pd.bdate_range("2026-01-05", periods=len(closes)).strftime("%Y%m%d"),
            "open": closes,
            "high": [close + 1 for close in closes],
            "close": closes,
            "vol": 1000,
        }),
        lookback_days=3,
        high_heat_days=2,
    )


def test_trough_confirms_after_three_rising_days_without_five_percent_rebound():
    states = _states([70, 80, 90, 100, 79, 79.5, 80, 79.8, 80.2])

    assert (80.2 / 79 - 1) < 0.05
    assert states.loc[7, "observed_state"] == StockShortState.TROUGH_CANDIDATE
    assert states.loc[8, "observed_state"] == StockShortState.TROUGH_REGION
    assert states.loc[8, "trough_up_days"] == 3
    assert states.loc[8, "trough_confirm_date"] == states.loc[8, "trade_date"]


def test_trough_waits_for_three_rising_days_in_latest_four_day_window():
    states = _states([70, 80, 90, 100, 79, 79.5, 79.4, 79.6, 79.5, 79.6, 79.7])

    assert states.loc[8, "observed_state"] == StockShortState.TROUGH_CANDIDATE
    assert states.loc[8, "trough_up_days"] == 2
    assert states.loc[10, "observed_state"] == StockShortState.TROUGH_REGION
    assert states.loc[10, "trough_up_days"] == 3
