import pandas as pd

from stock_selection.strategies.stock_short_high_state.screen import (
    StockShortState,
    calculate_stock_short_states,
)


def _bars(closes: list[float], name: str = "测试股票") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": "000001.SZ",
            "name": name,
            "trade_date": pd.date_range(
                "2025-01-01", periods=len(closes), freq="D"
            ).strftime("%Y%m%d"),
            "close": closes,
        }
    )


def _states(closes: list[float]) -> pd.DataFrame:
    return calculate_stock_short_states(
        _bars(closes),
        lookback_days=5,
        confirm_drawdown_pct=0.30,
        rebound_pct=0.10,
        high_heat_days=3,
        trough_heat_days=3,
    )


def test_confirms_high_region_only_after_thirty_percent_drawdown():
    closes = [10, 10, 10, 10, 10, 11, 12, 11, 8.4]
    result = _states(closes)

    assert result.loc[5, "state"] == int(StockShortState.HIGH_CONFIRMED)
    assert result.loc[6, "state"] == int(StockShortState.HIGH_CONFIRMED)
    assert result.loc[7, "state"] == int(StockShortState.HIGH_DRAWDOWN)
    assert result.loc[8, "state"] == int(StockShortState.TROUGH_WAIT_REBOUND)
    assert result.loc[8, "reference_high_close"] == 12


def test_unconfirmed_latest_high_remains_candidate():
    result = _states([10, 10, 10, 10, 10, 11, 12, 11])

    assert result.loc[5:, "state"].tolist() == [
        int(StockShortState.HIGH_CANDIDATE),
        int(StockShortState.HIGH_CANDIDATE),
        int(StockShortState.HIGH_CANDIDATE),
    ]


def test_creates_numbered_peaks_and_ends_at_peak_start_price():
    closes = [
        10,
        10,
        10,
        10,
        10,
        20,
        21,
        14,
        12,
        13.2,
        14,
        13.2,
        11,
        12.1,
        12.8,
        12.1,
    ]
    result = _states(closes)

    assert result.loc[9, "state"] == int(StockShortState.PEAK_1)
    assert result.loc[10, "state"] == int(StockShortState.PEAK_1)
    assert result.loc[11, "state"] == int(StockShortState.PEAK_1_PULLBACK)
    assert result.loc[13, "state"] == int(StockShortState.PEAK_2)
    assert result.loc[15, "state"] == int(StockShortState.PEAK_2_PULLBACK)
    assert result.loc[13, "state_description"] == "第二波峰区域"


def test_warns_when_confirmed_high_is_broken_and_starts_new_cycle():
    closes = [
        10,
        10,
        10,
        10,
        10,
        20,
        21,
        14,
        12,
        13.2,
        22,
        23,
    ]
    result = _states(closes)

    assert result.loc[10, "state"] == int(StockShortState.INVALID_HIGH_NEW_HIGH)
    assert result.loc[10, "alert_code"] == "CONFIRMED_HIGH_BROKEN"
    assert result.loc[11, "state"] == int(StockShortState.HIGH_CANDIDATE)
    assert result.loc[11, "cycle_id"] > result.loc[10, "cycle_id"]


def test_excludes_st_by_default_and_keeps_enum_description():
    result = calculate_stock_short_states(
        _bars([10, 10, 10, 10, 10, 11], name="ST测试"),
        lookback_days=5,
    )
    included = calculate_stock_short_states(
        _bars([10, 10, 10, 10, 10, 11], name="ST测试"),
        lookback_days=5,
        include_st=True,
    )

    assert result.empty
    assert included.iloc[-1]["state_name"] == "HIGH_CANDIDATE"
    assert included.iloc[-1]["state_description"] == "最高点候选区域"
