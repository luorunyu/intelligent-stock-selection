import pandas as pd

from stock_selection.strategies.stock_peak_pullback_rebound.screen import (
    PeakPullbackReboundState,
    calculate_peak_pullback_rebound_states,
    calculate_peak_pullback_rebounds,
    normalise_stock_daily,
)


def _bars(code: str, closes: list[float], name: str = "测试股票") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": code,
            "name": name,
            "trade_date": pd.date_range(
                "2025-01-01", periods=len(closes), freq="D"
            ).strftime("%Y%m%d"),
            "close": closes,
        }
    )


def _pattern(prefix: float = 50.0, second_peak: float = 75.0) -> list[float]:
    before = [prefix] * 120
    first_leg = list(range(51, 101))
    pullback = [90, 80, 70, 65]
    rebound = [68, second_peak - 2, second_peak, second_peak - 1]
    after = [60] * 120
    return before + first_leg + pullback + rebound + after


def test_finds_close_only_peak_pullback_rebound():
    result = calculate_peak_pullback_rebounds(
        _bars("000001.SZ", _pattern()),
        peak_lookback_days=10,
        peak_lookforward_days=10,
        max_rebound_days=8,
    )

    assert len(result) == 1
    event = result.iloc[0]
    assert event["peak_close"] == 100
    assert event["pullback_low_close"] == 65
    assert event["pullback_pct"] == -0.35
    assert event["second_peak_close"] == 75
    assert event["second_peak_ratio"] == 0.75


def test_requires_rebound_after_pullback_and_allows_multiple_events():
    first = _pattern()
    second = _pattern(prefix=60, second_peak=80)
    data = _bars("000001.SZ", first + second)
    result = calculate_peak_pullback_rebounds(
        data,
        peak_lookback_days=10,
        peak_lookforward_days=10,
        max_rebound_days=8,
    )

    assert len(result) == 2
    assert result["ts_code"].tolist() == ["000001.SZ", "000001.SZ"]
    assert result["peak_date"].is_monotonic_increasing


def test_seventy_percent_is_inclusive_and_lower_ratio_is_excluded():
    exact = calculate_peak_pullback_rebounds(
        _bars("000001.SZ", _pattern(second_peak=70)),
        peak_lookback_days=10,
        peak_lookforward_days=10,
        max_rebound_days=8,
    )
    below = calculate_peak_pullback_rebounds(
        _bars("000001.SZ", _pattern(second_peak=69.9)),
        peak_lookback_days=10,
        peak_lookforward_days=10,
        max_rebound_days=8,
    )

    assert len(exact) == 1
    assert below.empty


def test_excludes_st_by_default_and_normalises_invalid_close():
    data = _bars("000001.SZ", _pattern(), name="ST测试")
    data.loc[0, "close"] = None
    normalised = normalise_stock_daily(data)
    assert len(normalised) == len(data) - 1
    assert calculate_peak_pullback_rebounds(
        data,
        peak_lookback_days=10,
        peak_lookforward_days=10,
        max_rebound_days=8,
    ).empty


def test_returns_daily_active_and_time_limited_ended_states():
    closes = [10, 10, 10, 12, 10.5, 10.6, 11.5, 10.3, 10.2, 10.1, 10.0]

    result = calculate_peak_pullback_rebound_states(
        _bars("000001.SZ", closes),
        peak_lookback_days=3,
        max_rebound_days=20,
        rebound_ended_state_days=3,
    )

    assert len(result) == len(closes)
    assert result.loc[5, "state"] == int(
        PeakPullbackReboundState.REBOUND_ACTIVE
    )
    assert result.loc[6, "state_description"] == "高点回落后反弹中"
    assert result.loc[7:9, "state"].tolist() == [
        int(PeakPullbackReboundState.REBOUND_ENDED)
    ] * 3
    assert result.loc[7, "state_days"] == 1
    assert pd.isna(result.loc[10, "state"])


def test_breaking_reference_high_adds_warning_and_resets_state():
    closes = [10, 10, 10, 12, 10.5, 10.6, 12.1]

    result = calculate_peak_pullback_rebound_states(
        _bars("000001.SZ", closes),
        peak_lookback_days=3,
        max_rebound_days=20,
    )

    assert result.loc[5, "state"] == int(
        PeakPullbackReboundState.REBOUND_ACTIVE
    )
    assert pd.isna(result.loc[6, "state"])
    assert result.loc[6, "alert_code"] == "BREAK_PREVIOUS_HIGH"
    assert "原高点定义可能无效" in result.loc[6, "alert_message"]


def test_latest_day_can_be_classified_without_future_prices():
    closes = [10, 10, 10, 12, 10.5, 10.6]

    result = calculate_peak_pullback_rebound_states(
        _bars("000001.SZ", closes),
        peak_lookback_days=3,
        max_rebound_days=20,
    )

    latest = result.iloc[-1]
    assert latest["state"] == int(PeakPullbackReboundState.REBOUND_ACTIVE)
    assert latest["reference_high_close"] == 12
    assert latest["reference_low_close"] == 10.5
