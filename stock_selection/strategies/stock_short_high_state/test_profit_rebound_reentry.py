"""Reopen after a 10% post-exit rise followed by a return to the exit price."""

import json

import pandas as pd
import pytest

from stock_selection.strategies.stock_short_high_state.backtest import (
    CYCLE_CHANGE_EXIT, HIGH_INVALIDATION_EXIT, MAX_DRAWDOWN_STOP,
    PROFIT_REBOUND_EXIT, PROFIT_REBOUND_REENTRY, TROUGH_CONFIRM_EXIT,
    ShortTradeBacktestConfig, build_short_trade_backtest,
    filter_short_trade_backtest_by_stock_pool,
    split_short_trade_backtest_by_average_market_cap,
)
from stock_selection.strategies.stock_short_high_state.report import (
    render_short_trade_backtest_html, render_short_trade_backtest_markdown,
)
from stock_selection.strategies.stock_short_high_state.screen import (
    StockShortState, calculate_stock_short_states,
)


def _states(tail):
    # Enter at 100 and exit at 89 after three consecutive daily rises.
    closes = [70, 80, 90, 120, 100, 80, 80, 80, 83, 86, 89, *tail]
    states = calculate_stock_short_states(pd.DataFrame({
        "ts_code": "000001.SZ", "stock_name": "测试股票",
        "trade_date": pd.bdate_range("2026-01-05", periods=len(closes)).strftime("%Y%m%d"),
        "open": closes, "high": [close + 50 for close in closes], "close": closes, "vol": 1000,
    }), lookback_days=3, min_breakout_gain_pct=0)
    # Isolate the new execution rule from ordinary state-machine entry signals.
    states["cycle_id"] = 1
    states["peak_number"] = 0
    states[["state", "observed_state"]] = int(StockShortState.HIGH_CANDIDATE_PULLBACK)
    states[["state_name", "observed_state_name"]] = StockShortState.HIGH_CANDIDATE_PULLBACK.name
    states["trough_confirm_date"] = ""
    states["entry_signal"] = 0
    states["entry_signal_type"] = ""
    states.loc[4, "entry_signal"] = 15
    states.loc[4, "entry_signal_type"] = "CANDIDATE_DRAWDOWN_THRESHOLD"
    states["total_mv"] = 2_000_000
    return states


def _run(states, **kwargs):
    return build_short_trade_backtest(states, as_of_date=states.trade_date.max(), **kwargs)


def _reentries(result):
    return [trade for trade in result["trades"] if trade["entry_source"] == PROFIT_REBOUND_REENTRY]


@pytest.mark.parametrize(("tail", "entry_index"), [
    ([97.89, 89], None), ([97.9, 89], 12), ([97.9001, 89], 12),
    ([89.01, 89], None), ([100, 89.01], None),
    ([100, 89], 12), ([100, 88], 12), ([88, 87], None), ([90, 88], None),
    ([89, 89], None), ([89, 90], None), ([100, 102, 105], None),
])
def test_reentry_needs_a_ten_percent_post_exit_rise_before_return(tail, entry_index):
    states = _states(tail)
    saved = states.copy(deep=True)
    result = _run(states)
    original = result["trades"][0]
    assert original["status_reason"] == PROFIT_REBOUND_EXIT
    assert original["exit_close"] == 89
    assert len(_reentries(result)) == int(entry_index is not None)
    if entry_index is not None:
        trade, = _reentries(result)
        assert trade["entry_close"] == states.loc[entry_index, "close"]
        assert trade["entry_state"] == PROFIT_REBOUND_REENTRY
        assert trade["entry_reentry_exit_date"] == original["exit_date"]
        assert trade["entry_reentry_exit_close"] == 89
        assert trade["entry_reentry_previous_close"] == states.loc[entry_index - 1, "close"]
        assert trade["entry_reentry_previous_close"] > trade["entry_close"]
        assert trade["entry_reentry_origin_entry_date"] == original["entry_date"]
        assert trade["entry_date"] > original["exit_date"]
    pd.testing.assert_frame_equal(states, saved)


def test_fixed_exit_reference_does_not_follow_a_higher_rebound_peak():
    states = _states([98, 110, 99, 89.01, 89])
    trade, = _reentries(_run(states))
    assert trade["entry_date"] == states.iloc[-1].trade_date
    assert trade["entry_reentry_high_close"] == 110
    assert trade["entry_reentry_high_date"] == states.loc[12, "trade_date"]
    assert trade["entry_reentry_exit_close"] == 89


def test_previous_holdings_high_cannot_qualify_post_exit_rise():
    states = _states([89, 88, 87])
    assert states.loc[:4, "close"].max() > 89 * 1.10
    assert not _reentries(_run(states))


def test_intraday_touch_of_exit_price_does_not_trigger_reentry():
    states = _states([90, 91])
    states.loc[11:, "open"] = 88
    states.loc[11:, "high"] = 150
    assert not _reentries(_run(states))


def test_no_expiry_or_candidate_state_or_cycle_requirement():
    states = _states([98, *([99] * 20), 89])
    states.loc[11:, "cycle_id"] = 2
    states.loc[11:, ["state", "observed_state"]] = int(StockShortState.TROUGH_CANDIDATE)
    states.loc[11:, "entry_breakout_qualified"] = False
    trade, = _reentries(_run(states))
    assert trade["entry_date"] == states.iloc[-1].trade_date
    assert trade["cycle_id"] == 2
    assert trade["entry_observed_state"] == StockShortState.TROUGH_CANDIDATE.name


def test_ordinary_entry_can_open_earlier_and_consumes_old_observation():
    states = _states([94, 97.9, 89])
    states.loc[11, "entry_signal"] = 15
    states.loc[11, "entry_signal_type"] = "CANDIDATE_DRAWDOWN_THRESHOLD"
    result = _run(states)
    assert not _reentries(result)
    assert len(result["trades"]) == 2
    assert result["trades"][1]["entry_close"] == 94


def test_collision_with_ordinary_signal_opens_one_trade_and_exit_day_cannot_reenter():
    states = _states([98, 89])
    states.loc[[10, 12], "entry_signal"] = 15
    states.loc[[10, 12], "entry_signal_type"] = "CANDIDATE_DRAWDOWN_THRESHOLD"
    result = _run(states)
    assert len(result["trades"]) == 2
    trade, = _reentries(result)
    assert trade["entry_date"] == states.loc[12, "trade_date"]
    assert result["summary"]["skipped_on_exit_day"] == 1


def test_real_peak_one_collision_is_classified_as_reentry_without_changing_states():
    closes = [70, 80, 90, 100, 79, 70, 65, 60, 61, 62, 63, 64, 65, 66, 90, 60]
    states = calculate_stock_short_states(pd.DataFrame({
        "ts_code": "000001.SZ", "trade_date": pd.bdate_range("2026-01-05", periods=len(closes)).strftime("%Y%m%d"),
        "open": closes, "high": [close + 1 for close in closes], "close": closes, "vol": 1000,
    }), lookback_days=3, high_heat_days=2, min_breakout_gain_pct=0)
    saved = states.copy(deep=True)
    assert states.iloc[-1].entry_signal == int(StockShortState.PEAK_1)
    result = _run(states)
    assert len(result["trades"]) == 2
    trade, = _reentries(result)
    assert trade["entry_close"] == 60
    assert trade["status"] == "OPEN"
    pd.testing.assert_frame_equal(states, saved)


@pytest.mark.parametrize("reason", [MAX_DRAWDOWN_STOP, HIGH_INVALIDATION_EXIT, CYCLE_CHANGE_EXIT, TROUGH_CONFIRM_EXIT])
def test_other_exit_reasons_cannot_create_an_observation(reason):
    states = _states([98, 89])
    config = ShortTradeBacktestConfig()
    if reason == MAX_DRAWDOWN_STOP:
        config = ShortTradeBacktestConfig(max_drawdown_pct=.07)
    elif reason == HIGH_INVALIDATION_EXIT:
        states.loc[10, "observed_state"] = int(StockShortState.INVALID_HIGH_NEW_HIGH)
    elif reason == CYCLE_CHANGE_EXIT:
        states.loc[10:, "cycle_id"] = 2
    else:
        states.loc[8, "observed_state"] = int(StockShortState.TROUGH_REGION)
        states.loc[8, "trough_confirm_date"] = states.loc[8, "trade_date"]
        states.loc[8, "trough_ma_close"] = 82
    result = _run(states, config=config)
    assert result["trades"][0]["status_reason"] == reason
    assert not _reentries(result)


def test_reentry_uses_normal_profit_exit_and_starts_a_fresh_observation():
    states = _states([98, 89, 70, 70, 70, 72, 74, 77, 84.7, 77])
    result = _run(states)
    first, second, third = result["trades"]
    assert first["exit_close"] == 89
    assert second["status_reason"] == PROFIT_REBOUND_EXIT
    assert second["exit_close"] == 77
    assert third["entry_source"] == PROFIT_REBOUND_REENTRY
    assert third["entry_reentry_exit_close"] == 77
    assert third["entry_reentry_exit_date"] == second["exit_date"]
    assert third["entry_reentry_previous_close"] == pytest.approx(84.7)
    assert result["summary"]["profit_rebound_reentry_trades"] == 2


def test_reentry_stop_does_not_reuse_the_consumed_reference():
    result = _run(_states([98, 89, 107, 98, 89]))
    assert len(result["trades"]) == 2
    trade, = _reentries(result)
    assert trade["status_reason"] == MAX_DRAWDOWN_STOP


def test_reentry_requires_its_own_current_profit_and_consecutive_rises():
    result = _run(_states([98, 89, 90, 91, 94, 95, 96]))
    trade, = _reentries(result)
    assert trade["status"] == "OPEN"
    assert trade["evaluation_consecutive_up_days"] == 5
    assert trade["mark_return_pct"] < 0


def test_historical_profit_followed_by_loss_does_not_create_exit_observation():
    states = _states([98, 89])
    states.loc[8:10, ["open", "close"]] = [[96, 96], [98, 98], [101, 101]]
    result = _run(states)
    trade, = result["trades"]
    assert trade["status"] == "OPEN"
    assert not _reentries(result)


def test_prefix_and_multi_stock_results_do_not_use_future_or_other_stock_data():
    states = _states([98, 100, 89, 85])
    for length in range(5, len(states) + 1):
        prefix = states.iloc[:length]
        result = build_short_trade_backtest(states, as_of_date=prefix.iloc[-1].trade_date)
        assert result["trades"] == _run(prefix)["trades"]
    other = states.assign(ts_code="000002.SZ", entry_signal=0, entry_signal_type="")
    result = _run(pd.concat([states, other]).sample(frac=1, random_state=4))
    assert {trade["ts_code"] for trade in result["trades"]} == {"000001.SZ"}


def test_industry_filter_is_honoured_and_a_blocked_trigger_is_consumed():
    from stock_selection.strategies.stock_short_high_state.industry_state import (
        IndustryConfig, attach_industry_states, calculate_industry_states,
    )
    states = _states([98, 89, 88])
    config = IndustryConfig(lookback_days=3)
    industry = calculate_industry_states(pd.DataFrame({
        "ts_code": "801011.SI", "trade_date": states.trade_date,
        "close": [100, 100, 100, 100, 80, 80, 80, 80, 80, 80, 80, 80, 110, 80],
    }), config=config)
    members = pd.DataFrame({
        "ts_code": ["000001.SZ"], "industry_code": ["801011.SI"], "industry_name": ["测试行业"],
    })
    states = attach_industry_states(states, industry, members, config=config)
    result = _run(states)
    assert result["trades"][0]["status_reason"] == PROFIT_REBOUND_EXIT
    assert not _reentries(result)
    blocked, = result["industry_blocked_entries"]
    assert blocked["entry_signal_type"] == PROFIT_REBOUND_REENTRY
    assert blocked["trade_date"] == states.loc[12, "trade_date"]
    assert blocked["entry_reentry_exit_close"] == 89


@pytest.mark.parametrize("empty", [False, True])
def test_reports_and_filtered_summaries_keep_reentry_evidence(empty):
    states = _states([98, 89])
    result = _run(states.iloc[:0] if empty else states)
    filtered = filter_short_trade_backtest_by_stock_pool(
        result, ["000001.SZ"], pool_label="测试池", pool_date="20261108",
        source_file="test.csv", selection_rule="全部标的",
    )
    for report in [result, filtered, *split_short_trade_backtest_by_average_market_cap(result).values()]:
        json.dumps(report, allow_nan=False)
        rule = report["metadata"]["entry_rule"]["profit_rebound_reentry_rule"]
        assert rule["requires_post_exit_rise"] is True
        assert rule["min_rise_pct"] == .10
        assert rule["entry_formula"] == "post_exit_high_close >= exit_close * (1 + min_rise_pct) THEN close <= exit_close"
        assert report["summary"]["profit_rebound_reentry_trades"] == len(_reentries(report))
        for text in [render_short_trade_backtest_markdown(report), render_short_trade_backtest_html(report)]:
            assert PROFIT_REBOUND_REENTRY in text
            assert "重开关联平仓日" in text
            assert "收盘价先上涨至少10.00%" in text
            assert "5.00%" in text


def test_chart_uses_the_same_reentry_date_and_explains_the_reference():
    pytest.importorskip("plotly")
    from stock_selection.strategies.stock_short_high_state.visualization import (
        build_stock_short_state_figure, merge_bars_and_states,
    )
    states = _states([98, 89, 85])
    bars = states[["ts_code", "trade_date", "open", "high", "close", "vol"]].copy()
    bars["low"] = bars.close - 1
    figure = build_stock_short_state_figure(merge_bars_and_states(bars, states), stock_code="000001.SZ")
    entries = next(trace for trace in figure.data if trace.name == "做空开仓")
    assert list(entries.y) == [100, 89]
    assert entries.x[1] == pd.to_datetime(states.loc[12, "trade_date"])
    assert PROFIT_REBOUND_REENTRY in entries.text[1]
    assert "关联盈利平仓" in entries.text[1]
    assert "平仓后上涨门槛：97.90" in entries.text[1]
    assert "上涨达标日：" + states.loc[11, "trade_date"] in entries.text[1]


@pytest.mark.parametrize("value", [0, -.1, 1, True, "0.10", float("nan"), float("inf")])
def test_invalid_post_exit_rise_threshold_is_rejected(value):
    with pytest.raises(ValueError, match="profit_rebound_reentry_min_rise_pct"):
        ShortTradeBacktestConfig(profit_rebound_reentry_min_rise_pct=value)


def test_post_exit_rise_threshold_is_configurable():
    states = _states([94, 89])
    assert not _reentries(_run(states))
    result = _run(states, config=ShortTradeBacktestConfig(profit_rebound_reentry_min_rise_pct=.05))
    trade, = _reentries(result)
    assert trade["entry_close"] == 89
    assert trade["entry_reentry_rise_threshold_close"] == pytest.approx(93.45)
