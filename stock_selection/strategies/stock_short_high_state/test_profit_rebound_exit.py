"""Profit exits require current profit above 5% and three consecutive daily rises."""

import json

import pandas as pd
import pytest

from stock_selection.strategies.stock_short_high_state.backtest import (
    CYCLE_CHANGE_EXIT, HIGH_INVALIDATION_EXIT, MAX_DRAWDOWN_STOP,
    PROFIT_REBOUND_EXIT, TROUGH_CONFIRM_EXIT, ShortTradeBacktestConfig,
    build_short_trade_backtest, split_short_trade_backtest_by_average_market_cap,
)
from stock_selection.strategies.stock_short_high_state.report import (
    render_short_trade_backtest_html, render_short_trade_backtest_markdown,
)
from stock_selection.strategies.stock_short_high_state.screen import (
    StockShortState, calculate_stock_short_states,
)


def _states(after_entry):
    closes = [70, 80, 90, 120, 100, *after_entry]
    states = calculate_stock_short_states(pd.DataFrame({
        "ts_code": "000001.SZ", "stock_name": "测试股票",
        "trade_date": pd.bdate_range("2026-01-05", periods=len(closes)).strftime("%Y%m%d"),
        "open": closes, "high": [close + 1 for close in closes], "close": closes, "vol": 1000,
    }), lookback_days=3, min_breakout_gain_pct=0)
    # Retain the genuine entry at 100; isolate execution from the independent
    # detector by providing explicit subsequent observable states/signals.
    states["cycle_id"] = 1
    states["peak_number"] = 0
    states[["state", "observed_state"]] = int(StockShortState.HIGH_CANDIDATE_PULLBACK)
    states[["state_name", "observed_state_name"]] = StockShortState.HIGH_CANDIDATE_PULLBACK.name
    states["trough_confirm_date"] = ""
    states["entry_signal"] = 0
    states["entry_signal_type"] = ""
    states.loc[4, "entry_signal"] = int(StockShortState.HIGH_CANDIDATE_PULLBACK)
    states.loc[4, "entry_signal_type"] = "CANDIDATE_DRAWDOWN_THRESHOLD"
    states["total_mv"] = 2_000_000
    return states


def _run(states, **kwargs):
    return build_short_trade_backtest(states, as_of_date=states.trade_date.max(), **kwargs)


def test_third_consecutive_rise_exits_at_current_profitable_close():
    states = _states([80, 81, 82, 83])
    saved = states.copy(deep=True)
    before = build_short_trade_backtest(states, as_of_date=states.iloc[-2].trade_date)
    assert before["trades"][0]["status"] == "OPEN"
    assert before["trades"][0]["evaluation_consecutive_up_days"] == 2
    trade, = _run(states)["trades"]
    assert trade["status_reason"] == PROFIT_REBOUND_EXIT
    assert trade["exit_close"] == 83
    assert trade["evaluation_consecutive_up_days"] == 3
    pd.testing.assert_frame_equal(states, saved)


def test_three_rises_can_exit_below_ma5():
    states = _states([60, 61, 62, 63])
    assert states.close.iloc[-1] < states.close.rolling(5).mean().iloc[-1]
    trade, = _run(states)["trades"]
    assert trade["status_reason"] == PROFIT_REBOUND_EXIT
    assert trade["exit_close"] == 63


def test_above_ma_closes_without_three_rises_cannot_exit():
    trade, = _run(_states([80, 80, 80, 90, 89, 89]))["trades"]
    assert trade["status"] == "OPEN"
    assert trade["evaluation_consecutive_up_days"] == 0


@pytest.mark.parametrize("reset_close", [81, 82])
def test_down_or_flat_close_resets_consecutive_rises(reset_close):
    states = _states([80, 81, 82, reset_close, 83, 84, 85])
    reset = build_short_trade_backtest(states, as_of_date=states.loc[8, "trade_date"])
    assert reset["trades"][0]["evaluation_consecutive_up_days"] == 0
    before = build_short_trade_backtest(states, as_of_date=states.iloc[-2].trade_date)
    assert before["trades"][0]["status"] == "OPEN"
    trade, = _run(states)["trades"]
    assert trade["exit_date"] == states.iloc[-1].trade_date
    assert trade["evaluation_consecutive_up_days"] == 3


@pytest.mark.parametrize(("last_close", "exits"), [
    (94.99, True), (95, False), (95.01, False), (100, False), (101, False),
])
def test_exit_uses_current_profit_strictly_above_five_percent(last_close, exits):
    trade, = _run(_states([80, 90, 91, last_close]))["trades"]
    assert trade["evaluation_consecutive_up_days"] == 3
    assert (trade["status_reason"] == PROFIT_REBOUND_EXIT) is exits
    assert trade["mark_return_pct"] == pytest.approx(1 - last_close / 100)


def test_previous_five_percent_profit_does_not_preserve_exit_eligibility():
    trade, = _run(_states([80, 90, 91, 95, 96, 97]))["trades"]
    assert trade["status"] == "OPEN"
    assert trade["evaluation_consecutive_up_days"] == 5
    assert trade["mark_return_pct"] == pytest.approx(.03)


def test_entry_day_and_pre_entry_rises_do_not_count():
    states = _states([101, 102, 103])
    states.loc[3, ["open", "close"]] = 90
    for offset in [1, 2, 3]:
        result = build_short_trade_backtest(states, as_of_date=states.loc[4 + offset, "trade_date"])
        assert result["trades"][0]["evaluation_consecutive_up_days"] == offset
        assert result["trades"][0]["status"] == "OPEN"


def test_each_stock_uses_its_own_current_return_and_streak():
    first = _states([80, 81, 82, 83])
    second = _states([80, 90, 91, 101]).assign(ts_code="000002.SZ")
    trades = _run(pd.concat([first, second]).sample(frac=1, random_state=7))["trades"]
    by_code = {t["ts_code"]: t for t in trades}
    assert by_code["000001.SZ"]["status_reason"] == PROFIT_REBOUND_EXIT
    assert by_code["000002.SZ"]["status"] == "OPEN"


def test_future_closes_cannot_change_a_known_exit():
    states = _states([80, 81, 82, 83, 200])
    for length in range(5, len(states) + 1):
        prefix = states.iloc[:length]
        result = build_short_trade_backtest(states, as_of_date=prefix.iloc[-1].trade_date)
        assert result["trades"] == _run(prefix)["trades"]


@pytest.mark.parametrize("reason", [MAX_DRAWDOWN_STOP, HIGH_INVALIDATION_EXIT, CYCLE_CHANGE_EXIT])
def test_stop_and_invalidation_keep_priority_on_third_rise(reason):
    states = _states([80, 81, 82, 91])
    config = ShortTradeBacktestConfig(max_drawdown_pct=.09 if reason == MAX_DRAWDOWN_STOP else .2)
    if reason == HIGH_INVALIDATION_EXIT:
        states.loc[states.index[-1], "observed_state"] = int(StockShortState.INVALID_HIGH_NEW_HIGH)
    elif reason == CYCLE_CHANGE_EXIT:
        states.loc[states.index[-1], "cycle_id"] = 2
    trade, = _run(states, config=config)["trades"]
    assert trade["evaluation_consecutive_up_days"] == 3
    assert trade["status_reason"] == reason


@pytest.mark.parametrize("confirm_position", [6, 8])
def test_trough_can_exit_earlier_but_profit_exit_wins_on_same_day(confirm_position):
    states = _states([80, 81, 82, 83])
    states.loc[confirm_position, "observed_state"] = int(StockShortState.TROUGH_REGION)
    states.loc[confirm_position, "trough_confirm_date"] = states.loc[confirm_position, "trade_date"]
    states.loc[confirm_position, "trough_ma_close"] = states.loc[confirm_position, "close"] - 1
    trade, = _run(states)["trades"]
    assert trade["status_reason"] == (TROUGH_CONFIRM_EXIT if confirm_position == 6 else PROFIT_REBOUND_EXIT)
    assert trade["exit_date"] == states.loc[confirm_position, "trade_date"]


@pytest.mark.parametrize("value", [0, True, 2.5, float("inf")])
def test_invalid_rise_count_is_rejected(value):
    with pytest.raises(ValueError, match="profit_rebound_up_days"):
        ShortTradeBacktestConfig(profit_rebound_up_days=value)


def test_consecutive_rise_count_is_configurable():
    states = _states([80, 81, 82])
    trade, = _run(states, config=ShortTradeBacktestConfig(profit_rebound_up_days=2))["trades"]
    assert trade["exit_close"] == 82
    assert trade["evaluation_consecutive_up_days"] == 2


@pytest.mark.parametrize("empty", [False, True])
def test_reports_describe_current_profit_and_consecutive_rises(empty):
    states = _states([80, 81, 82, 83])
    result = _run(states.iloc[:0] if empty else states)
    rule = result["metadata"]["profit_rebound_exit_rule"]
    assert rule["consecutive_up_days"] == 3
    assert rule["return_comparison"] == ">"
    for backtest in [result, *split_short_trade_backtest_by_average_market_cap(result).values()]:
        json.dumps(backtest, allow_nan=False)
        for text in [render_short_trade_backtest_html(backtest), render_short_trade_backtest_markdown(backtest)]:
            assert "盈利后连续上涨平仓" in text
            assert "严格大于5.00%" in text
            assert "只要求触发当天收益达标" in text
            assert "结束日开仓后连续收盘上涨天数" in text


@pytest.mark.parametrize("trough_ma_days", [5, 8])
def test_chart_and_backtest_share_current_profit_exit(trough_ma_days):
    pytest.importorskip("plotly")
    from stock_selection.strategies.stock_short_high_state.visualization import (
        build_stock_short_state_figure, merge_bars_and_states,
    )
    states = _states([80, 81, 82, 83])
    states["trough_ma_days"] = trough_ma_days
    states["trough_ma_close"] = states.close.rolling(trough_ma_days).mean()
    bars = states[["ts_code", "trade_date", "open", "high", "close", "vol"]].copy()
    bars["low"] = bars.close - 1
    figure = build_stock_short_state_figure(merge_bars_and_states(bars, states), stock_code="000001.SZ")
    exits = next(t for t in figure.data if t.name == "做空平仓")
    assert list(exits.y) == [83]
    assert "PROFIT_REBOUND_EXIT" in exits.text[0]
    assert "连续收盘上涨：3 天" in exits.text[0]
