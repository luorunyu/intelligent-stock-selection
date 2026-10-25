import pandas as pd
import pytest

from stock_selection.strategies.stock_short_high_state.backtest import (
    TROUGH_CONFIRM_EXIT,
    build_short_trade_backtest,
)
from stock_selection.strategies.stock_short_high_state.screen import (
    StockShortState,
    calculate_stock_short_states,
)


def _states(closes, **kwargs):
    # Keep the existing short MA5 fixtures explicit; MA8 cases override below.
    kwargs.setdefault("trough_ma_days", 5)
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
        **kwargs,
    )


def test_fast_rebound_confirms_and_exits_one_day_after_low():
    states = _states([90, 90, 90, 90, 90, 90, 100, 79, 92], trough_ma_days=8)
    low, confirmation = states.iloc[7], states.iloc[8]

    assert confirmation["trough_ma_close"] == pytest.approx(90.125)
    assert confirmation["close"] > confirmation["trough_ma_close"]
    assert confirmation["observed_state"] == StockShortState.TROUGH_REGION
    assert confirmation["trough_confirm_date"] == confirmation["trade_date"]
    assert confirmation["reference_low_date"] == low["trade_date"]
    assert confirmation["trough_zone_low"] == 79
    assert confirmation["trough_zone_high"] == 92
    assert low["state"] == StockShortState.TROUGH_REGION
    assert low["observed_state"] == StockShortState.TROUGH_CANDIDATE

    trade, = build_short_trade_backtest(states, as_of_date=confirmation["trade_date"])["trades"]
    assert trade["status_reason"] == TROUGH_CONFIRM_EXIT
    assert trade["exit_date"] == confirmation["trade_date"]
    assert trade["exit_close"] == 92
    assert trade["return_pct"] == pytest.approx(1 - 92 / 79)


def test_equal_to_ma_waits_until_close_is_strictly_above():
    states = _states([70, 80, 90, 100, 79, 78, 77, 76, 75, 76.5, 77])

    assert states.loc[9, "trough_ma_close"] == 76.5
    assert states.loc[9, "observed_state"] == StockShortState.TROUGH_CANDIDATE
    assert states.loc[10, "trough_ma_close"] == pytest.approx(76.3)
    assert states.loc[10, "observed_state"] == StockShortState.TROUGH_REGION
    assert states.loc[10, "reference_low_date"] == states.loc[8, "trade_date"]


def test_ma5_crossing_does_not_confirm_until_above_ma8():
    states = _states([110] * 6 + [120, 90, 91, 92, 93, 92, 93, 94, 95], trough_ma_days=8)

    ma5 = states["close"].rolling(5).mean()
    assert states.loc[11, "close"] > ma5.loc[11]
    assert states.loc[11, "close"] < states.loc[11, "trough_ma_close"]
    assert states.loc[11:13, "observed_state"].eq(StockShortState.TROUGH_CANDIDATE).all()
    assert states.loc[14, "trough_ma_close"] == 92.5
    assert states.loc[14, "observed_state"] == StockShortState.TROUGH_REGION
    trade, = build_short_trade_backtest(states, as_of_date=states.iloc[-1]["trade_date"])["trades"]
    assert trade["status_reason"] == TROUGH_CONFIRM_EXIT
    assert trade["exit_date"] == states.loc[14, "trade_date"]
    assert trade["exit_close"] == 95


def test_requires_full_ma_history_and_includes_current_close():
    closes = [70, 100, 79, 92, 93, 94, 95, 96]
    states = calculate_stock_short_states(
        pd.DataFrame({
            "ts_code": "000001.SZ",
            "trade_date": pd.bdate_range("2026-01-05", periods=8).strftime("%Y%m%d"),
            "open": closes, "high": [close + 1 for close in closes], "close": closes,
        }),
        lookback_days=1, high_heat_days=1,
    )

    assert states["trough_ma_days"].eq(8).all()
    assert states.loc[:6, "trough_ma_close"].isna().all()
    assert states.loc[6, "observed_state"] == StockShortState.TROUGH_CANDIDATE
    assert states.loc[7, "trough_ma_close"] == pytest.approx(89.875)
    assert states.loc[7, "observed_state"] == StockShortState.TROUGH_REGION


def test_breaking_old_trough_starts_a_new_low_and_confirmation():
    states = _states([70, 80, 90, 100, 79, 92, 78, 90])

    assert states.loc[5, "trough_confirm_date"] == states.loc[5, "trade_date"]
    assert states.loc[6, "observed_state"] == StockShortState.TROUGH_CANDIDATE
    assert states.loc[7, "observed_state"] == StockShortState.TROUGH_REGION
    assert states.loc[7, "reference_low_date"] == states.loc[6, "trade_date"]
    assert states.loc[7, "trough_confirm_date"] == states.loc[7, "trade_date"]
    assert states.loc[4, "reference_low_close"] == 79
    assert states.loc[6, "reference_low_close"] == 78


def test_observable_signals_and_trades_do_not_use_future_prices():
    closes = [70, 80, 90, 100, 79, 92, 78, 90]
    states = _states(closes)
    observable = ["trade_date", "trough_ma_close", "observed_state", "entry_signal"]
    for length in range(4, len(closes) + 1):
        prefix = _states(closes[:length])
        pd.testing.assert_frame_equal(
            prefix[observable], states.iloc[:length][observable],
        )
        date = prefix.iloc[-1]["trade_date"]
        from_full = build_short_trade_backtest(states, as_of_date=date)["trades"]
        from_prefix = build_short_trade_backtest(prefix, as_of_date=date)["trades"]
        assert from_full == from_prefix


def test_suspended_placeholder_does_not_count_towards_ma():
    closes = [70, 80, 90, 100, 79, 0, 92]
    daily = pd.DataFrame({
        "ts_code": "000001.SZ",
        "trade_date": pd.bdate_range("2026-01-05", periods=7).strftime("%Y%m%d"),
        "open": closes,
        "high": [close + 1 if close else 0 for close in closes],
        "close": closes,
        "vol": [1000 if close else 0 for close in closes],
    })
    states = calculate_stock_short_states(daily, lookback_days=3, high_heat_days=2, trough_ma_days=5)

    assert len(states) == 6
    assert states.iloc[-1]["trough_ma_close"] == pytest.approx(88.2)
    assert states.iloc[-1]["trough_confirm_date"] == daily.iloc[-1]["trade_date"]


def test_configurable_ma_window_is_used_instead_of_waiting_period():
    states = _states([70, 80, 90, 100, 79, 80], trough_ma_days=2)

    assert states.iloc[-1]["trough_ma_close"] == 79.5
    assert states.iloc[-1]["observed_state"] == StockShortState.TROUGH_REGION
    result = build_short_trade_backtest(states, as_of_date=states.iloc[-1]["trade_date"])
    assert result["metadata"]["normal_exit_rule"]["trough_ma_days"] == 2


@pytest.mark.parametrize("window", [0, 1, 2.5, True])
def test_rejects_invalid_ma_window(window):
    with pytest.raises(ValueError, match="trough_ma_days"):
        _states([70, 80, 90, 100, 79, 92], trough_ma_days=window)


def test_legacy_window_argument_now_configures_ma_for_existing_callers():
    states = _states([70, 80, 90, 100, 79, 80], trough_confirm_days=2)

    assert states.iloc[-1]["trough_ma_days"] == 2
    assert states.iloc[-1]["trough_ma_close"] == 79.5
    assert states.iloc[-1]["observed_state"] == StockShortState.TROUGH_REGION


def test_old_day_count_states_must_be_recalculated(tmp_path):
    from stock_selection.strategies.stock_short_high_state.visualization import read_saved_states

    states = _states([70, 80, 90, 100, 79, 92]).drop(columns=["trough_ma_days", "trough_ma_close"])
    states["trough_confirm_days"] = 5
    states["trough_min_up_days"] = 3
    states["trough_up_days"] = 0
    path = tmp_path / "states.csv"
    states.to_csv(path, index=False)

    with pytest.raises(ValueError, match="重新运行策略五"):
        build_short_trade_backtest(states, as_of_date=states.iloc[-1]["trade_date"])
    with pytest.raises(ValueError, match="重新运行策略五"):
        read_saved_states(path)


@pytest.mark.parametrize("ma", [float("nan"), float("inf"), 92, 93])
def test_backtest_rejects_confirmation_without_close_above_ma(ma):
    states = _states([70, 80, 90, 100, 79, 92])
    states.loc[5, "trough_ma_close"] = ma

    with pytest.raises(ValueError, match="重新运行策略五"):
        build_short_trade_backtest(states, as_of_date=states.iloc[-1]["trade_date"])


def test_state_report_describes_ma_confirmation_and_historical_low():
    from stock_selection.strategies.stock_short_high_state.report import (
        build_stock_short_state_report,
        render_stock_short_state_markdown,
    )

    states = _states([90, 90, 90, 90, 90, 90, 100, 79, 92], trough_ma_days=8)
    report = build_stock_short_state_report(
        states, as_of_date=states.iloc[-1]["trade_date"], lookback_days=3,
        confirm_drawdown_pct=0.20, rebound_pct=0.10, high_heat_days=2,
        trough_ma_days=8,
    )
    markdown = render_stock_short_state_markdown(report)

    assert "MA8" in markdown
    assert "实际平仓仅使用确认当天收盘价" in markdown
    assert "至少观察" not in markdown
    assert "trough_min_up_days" not in report["metadata"]
