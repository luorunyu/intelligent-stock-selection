"""Break-even protection and entry-price reentry after confirmed declines."""

from dataclasses import replace
import json

import pandas as pd
import pytest

from stock_selection.strategies.stock_short_high_state.backtest import (
    CYCLE_CHANGE_EXIT, EXIT_PRICE_REENTRY, HIGH_INVALIDATION_EXIT,
    MAX_DRAWDOWN_STOP, PROFIT_GIVEBACK_EXIT, PROFIT_REBOUND_EXIT,
    TROUGH_CONFIRM_EXIT, TRADING_DATES_FILENAME, ShortTradeBacktestConfig,
    build_short_trade_backtest, filter_short_trade_backtest_by_stock_pool,
    split_short_trade_backtest_by_average_market_cap,
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
        "open": closes, "high": [value + 1 for value in closes],
        "close": closes, "vol": 1000,
    }), lookback_days=3, high_heat_days=2, min_breakout_gain_pct=0)
    # Isolate the trade rules from the independent detector; retain the
    # genuine qualified entry at 100 and supply observable events explicitly.
    states["entry_signal"] = 0
    states["entry_signal_type"] = ""
    _signal(states, 4)
    states["cycle_id"] = 1
    _observe(states, states.index, StockShortState.HIGH_CANDIDATE_PULLBACK)
    states["trough_confirm_date"] = ""
    states["total_mv"] = 2_000_000
    return states


def _observe(states, positions, state):
    states.loc[positions, ["state", "observed_state"]] = int(state)
    states.loc[positions, ["state_name", "observed_state_name"]] = state.name


def _signal(states, position):
    states.loc[position, "entry_signal"] = int(StockShortState.HIGH_CANDIDATE_PULLBACK)
    states.loc[position, "entry_signal_type"] = "CANDIDATE_DRAWDOWN_THRESHOLD"


def _run(states, **kwargs):
    return build_short_trade_backtest(states, as_of_date=states.trade_date.max(), **kwargs)


@pytest.mark.parametrize(("prices", "exit_return"), [
    ([97, 100], 0), ([95, 100], 0), ([95, 101], -.01),
])
def test_giveback_exits_at_entry_or_actual_gap_price(prices, exit_return):
    result = _run(_states(prices))
    trade, = result["trades"]
    assert trade["status_reason"] == PROFIT_GIVEBACK_EXIT
    assert trade["return_pct"] == pytest.approx(exit_return)
    assert trade["exit_close"] == prices[-1]
    assert trade["peak_profit_pct"] == pytest.approx(1 - min(prices) / 100)
    assert trade["profit_protection_armed"] is True
    assert trade["reentry_watch_status"] == "WATCHING"
    assert result["summary"]["profit_giveback_exit_trades"] == 1


@pytest.mark.parametrize("prices", [
    [95, 98], [95, 99], [98, 100], [100, 101], [101, 100], [95, 95],
])
def test_no_exit_before_break_even_or_without_activation(prices):
    trade, = _run(_states(prices))["trades"]
    assert trade["status"] == "OPEN"


def test_zero_activation_can_protect_a_small_positive_profit():
    trade, = _run(_states([99, 100]), config=ShortTradeBacktestConfig(
        profit_protection_activation_pct=0,
    ))["trades"]
    assert trade["status_reason"] == PROFIT_GIVEBACK_EXIT


def test_one_down_day_or_a_continuing_rise_cannot_reenter():
    for prices in ([95, 100, 99], [95, 100, 101, 102, 103]):
        result = _run(_states(prices))
        trade, = result["trades"]
        assert trade["reentry_watch_status"] == "WATCHING"
        assert result["summary"]["reentry_waiting_decline"] == 1


@pytest.mark.parametrize("reentry_state", [
    StockShortState.HIGH_CANDIDATE, StockShortState.HIGH_CANDIDATE_PULLBACK,
    StockShortState.HIGH_DRAWDOWN, StockShortState.TROUGH_CANDIDATE,
    StockShortState.TROUGH_REGION, StockShortState.PEAK_1,
    StockShortState.PEAK_1_PULLBACK,
])
def test_two_down_days_reenter_without_ongoing_candidate_state_or_fresh_crossing(reentry_state):
    states = _states([95, 100, 99, 98])
    _observe(states, [7, 8], reentry_state)
    _signal(states, 7)  # Even after leaving the candidate phase, day one waits.
    prefix = build_short_trade_backtest(states, as_of_date=states.loc[7, "trade_date"])
    trade, = prefix["trades"]
    assert trade["reentry_watch_status"] == "WATCHING"
    assert trade["reentry_watch_down_days"] == 1
    first, second = _run(states)["trades"]
    assert first["reentry_watch_status"] == "CONSUMED"
    assert second["entry_date"] == states.iloc[-1].trade_date
    assert second["entry_close"] == 98
    assert second["entry_source"] == EXIT_PRICE_REENTRY
    assert second["entry_signal"] == 0
    assert second["entry_state"] == reentry_state.name
    assert second["reentry_consecutive_down_days"] == 2
    assert second["reentry_elapsed_trade_days"] == 2
    assert second["qualification_entry_date"] == first["entry_date"]
    assert first["reentry_consumed_by_trade_id"] == second["trade_id"]


def test_two_declines_equal_to_previous_entry_price_can_reenter():
    states = _states([95, 100, 103, 102, 100, 99])
    prefix = build_short_trade_backtest(states, as_of_date=states.iloc[-2].trade_date)
    assert len(prefix["trades"]) == 2
    first, second = _run(states)["trades"]
    assert second["entry_date"] == states.iloc[-2].trade_date
    assert second["entry_close"] == 100
    assert second["reentry_consecutive_down_days"] == 2
    assert first["reentry_watch_down_days"] == 2


@pytest.mark.parametrize("prices", [[95, 100, 99, 99, 98, 97], [95, 100, 99, 101, 99, 98]])
def test_flat_or_up_resets_consecutive_declines(prices):
    states = _states(prices)
    first, second = _run(states)["trades"]
    assert second["entry_date"] == states.iloc[-1].trade_date
    assert second["reentry_consecutive_down_days"] == 2
    assert second["reentry_elapsed_trade_days"] == 4


def test_pre_exit_declines_are_not_counted():
    trade, = _run(_states([96, 95, 94, 100, 99]))["trades"]
    assert trade["reentry_watch_down_days"] == 1


def test_gap_exit_waits_for_previous_entry_price_instead_of_exit_price():
    states = _states([95, 103, 102, 101, 100])
    prefix = build_short_trade_backtest(states, as_of_date=states.iloc[-2].trade_date)
    trade, = prefix["trades"]
    assert trade["reentry_watch_down_days"] == 2
    assert prefix["summary"]["reentry_waiting_price"] == 1
    first, second = _run(states)["trades"]
    assert first["exit_close"] == 103
    assert second["reentry_anchor_exit_close"] == 103
    assert second["reentry_anchor_entry_close"] == 100
    assert second["entry_close"] == 100
    assert second["reentry_consecutive_down_days"] == 3


def test_configurable_three_down_days_does_not_reenter_after_only_two():
    states = _states([95, 100, 99, 98, 97])
    config = ShortTradeBacktestConfig(reentry_down_days=3)
    prefix = build_short_trade_backtest(states, as_of_date=states.iloc[-2].trade_date, config=config)
    assert len(prefix["trades"]) == 1
    first, second = _run(states, config=config)["trades"]
    assert second["entry_close"] == 97
    assert second["reentry_consecutive_down_days"] == 3
    assert first["reentry_down_days_required"] == 3


def test_stop_uses_trigger_day_close_for_decline_and_previous_entry_for_anchor():
    states = _states([125, 123, 122, 119, 100])
    prefix = build_short_trade_backtest(states, as_of_date=states.iloc[-2].trade_date)
    assert len(prefix["trades"]) == 1
    assert prefix["summary"]["reentry_waiting_price"] == 1
    first, second = _run(states)["trades"]
    assert first["evaluation_actual_close"] == 125
    assert first["exit_close"] == pytest.approx(120)
    assert second["reentry_anchor_entry_close"] == 100
    assert second["entry_close"] == 100
    assert second["reentry_consecutive_down_days"] == 4


@pytest.mark.parametrize("reason", [PROFIT_GIVEBACK_EXIT, PROFIT_REBOUND_EXIT, MAX_DRAWDOWN_STOP])
def test_any_exit_still_in_candidate_cycle_can_start_watch(reason):
    prices = {
        PROFIT_GIVEBACK_EXIT: [95, 100, 99, 98],
        PROFIT_REBOUND_EXIT: [90, 91, 92, 93, 92, 91],
        MAX_DRAWDOWN_STOP: [125, 99, 98],
    }[reason]
    first, second = _run(_states(prices))["trades"]
    assert first["status_reason"] == reason
    assert first["reentry_watch_status"] == "CONSUMED"
    assert second["reentry_anchor_exit_reason"] == reason
    assert second["entry_source"] == EXIT_PRICE_REENTRY
    assert second["reentry_consecutive_down_days"] == 2


def test_profitable_exit_can_reenter_above_exit_but_below_previous_entry():
    first, second = _run(_states([90, 91, 92, 93, 99, 98, 97]))["trades"]
    assert first["status_reason"] == PROFIT_REBOUND_EXIT
    assert first["exit_close"] == 93
    assert second["reentry_anchor_entry_close"] == 100
    assert second["entry_close"] == 97
    assert second["reentry_consecutive_down_days"] == 2


@pytest.mark.parametrize("reason", [TROUGH_CONFIRM_EXIT, HIGH_INVALIDATION_EXIT, CYCLE_CHANGE_EXIT])
def test_exit_outside_original_candidate_phase_cannot_start_watch(reason):
    states = _states([95, 100, 99, 98])
    if reason == TROUGH_CONFIRM_EXIT:
        _observe(states, [6], StockShortState.TROUGH_REGION)
        states.loc[6, "trough_confirm_date"] = states.loc[6, "trade_date"]
        # No giveback on this exit, so the original trough exit takes priority.
        states.loc[6, "close"] = 96
        states.loc[6, "trough_ma_close"] = 95
    elif reason == HIGH_INVALIDATION_EXIT:
        _observe(states, [6], StockShortState.INVALID_HIGH_NEW_HIGH)
    else:
        states.loc[6:, "cycle_id"] = 2
    result = _run(states)
    trade, = result["trades"]
    assert trade["status_reason"] == reason
    assert trade["reentry_watch_status"] == ("INELIGIBLE_CYCLE" if reason == CYCLE_CHANGE_EXIT else "INELIGIBLE_STATE")
    assert result["summary"]["reentry_ineligible_exits"] == 1


def test_cycle_change_still_cancels_watch():
    states = _states([95, 100, 99, 98, 97])
    states.loc[7:, "cycle_id"] = 2
    result = _run(states)
    trade, = result["trades"]
    assert trade["reentry_watch_status"] == "CANCELLED_CYCLE"
    assert trade["reentry_watch_cancel_date"] == states.loc[7, "trade_date"]
    assert result["summary"]["reentry_cancelled_cycle"] == 1


def test_leaving_then_returning_to_candidate_phase_keeps_decline_count():
    states = _states([95, 100, 99, 98])
    _observe(states, [7], StockShortState.TROUGH_CANDIDATE)
    result = _run(states)
    first, second = result["trades"]
    assert first["reentry_watch_status"] == "CONSUMED"
    assert second["reentry_consecutive_down_days"] == 2
    assert result["summary"]["reentry_cancelled_state"] == 0


def test_new_cycle_can_use_a_new_normal_signal_without_inherited_qualification():
    states = _states([95, 100, 99, 98])
    states.loc[7:, "cycle_id"] = 2
    _signal(states, 7)
    first, second = _run(states)["trades"]
    assert first["reentry_watch_status"] == "CANCELLED_CYCLE"
    assert second["entry_source"] == "CANDIDATE_DRAWDOWN_THRESHOLD"
    assert second["chain_sequence"] == 1
    assert second["qualification_entry_date"] == second["entry_date"]


def test_backfilled_state_does_not_cancel_an_observable_candidate_watch():
    states = _states([95, 100, 99, 98])
    states.loc[6:, "state"] = int(StockShortState.TROUGH_REGION)
    states.loc[6:, "state_name"] = StockShortState.TROUGH_REGION.name
    assert len(_run(states)["trades"]) == 2


def test_normal_signal_cannot_bypass_watch_and_confirmed_entry_is_a_reentry():
    states = _states([95, 100, 99, 98])
    _signal(states, 7)
    _signal(states, 8)
    result = _run(states)
    first, second = result["trades"]
    assert second["entry_date"] == states.loc[8, "trade_date"]
    assert second["entry_source"] == EXIT_PRICE_REENTRY
    assert first["reentry_blocked_entries"] == [{"trade_date": states.loc[7, "trade_date"], "reason": "WAITING_DECLINE"}]
    assert result["summary"]["reentry_blocked_signals"] == 1


def test_normal_signal_cannot_bypass_price_requirement():
    states = _states([95, 103, 102, 101])
    _signal(states, 8)
    result = _run(states)
    trade, = result["trades"]
    assert trade["reentry_blocked_entries"][0]["reason"] == "WAITING_PRICE"


def test_new_exit_renews_watch_and_clears_previous_declines_and_profit():
    states = _states([95, 100, 99, 98, 94, 98, 97, 96])
    first, second, third = _run(states)["trades"]
    assert [t["chain_sequence"] for t in [first, second, third]] == [1, 2, 3]
    assert {t["chain_id"] for t in [first, second, third]} == {first["trade_id"]}
    assert second["status_reason"] == PROFIT_GIVEBACK_EXIT
    assert second["return_pct"] == pytest.approx(0)
    assert third["reentry_from_trade_id"] == second["trade_id"]
    assert third["reentry_anchor_exit_close"] == 98
    assert third["reentry_consecutive_down_days"] == 2
    assert third["qualification_entry_date"] == first["entry_date"]
    assert third["peak_profit_pct"] == 0
    assert third["profit_protection_armed"] is False
    assert third["reentry_watch_down_days"] == 0


def test_exit_day_signal_is_skipped_without_same_day_reentry():
    states = _states([95, 100, 99, 98])
    _signal(states, 6)
    result = _run(states)
    first, second = result["trades"]
    assert first["skipped_entries"][0]["reason"] == "EXIT_DAY"
    assert second["entry_date"] == states.loc[8, "trade_date"]
    assert result["summary"]["skipped_on_exit_day"] == 1


@pytest.mark.parametrize("days", [10, 11])
def test_watch_includes_tenth_market_day_but_not_eleventh(days):
    states = _states([95, 100, *([101] * (days - 2)), 99, 98])
    result = _run(states)
    assert len(result["trades"]) == (2 if days == 10 else 1)
    assert result["trades"][0]["reentry_watch_status"] == ("CONSUMED" if days == 10 else "EXPIRED")
    if days == 10:
        assert result["trades"][1]["reentry_elapsed_trade_days"] == 10


def test_normal_signal_resumes_after_expiry_and_is_not_replayed():
    states = _states([95, 100, *([101] * 10), 99])
    _signal(states, 7)
    _signal(states, 17)
    first, second = _run(states)["trades"]
    assert first["reentry_watch_status"] == "EXPIRED"
    assert second["entry_date"] == states.loc[17, "trade_date"]
    assert second["entry_source"] == "CANDIDATE_DRAWDOWN_THRESHOLD"
    assert second["chain_sequence"] == 1


def test_suspension_does_not_extend_window_but_declines_use_valid_stock_bars():
    full = _states([95, 100, *([101] * 9), 99, 98])
    states = full.loc[[*range(7), 16, 17]].copy()
    result = _run(states, trading_dates=full.trade_date)
    trade, = result["trades"]
    assert trade["reentry_watch_status"] == "EXPIRED"
    within = _states([95, 100, 99, 105, 104, 98])
    result = _run(within.loc[[*range(8), 10]], trading_dates=within.trade_date)
    assert result["trades"][1]["reentry_elapsed_trade_days"] == 4
    assert result["trades"][1]["reentry_consecutive_down_days"] == 2


def test_other_stocks_supply_market_calendar_when_no_snapshot_is_given():
    full = _states([95, 100, *([101] * 9), 99, 98])
    suspended = full.loc[[*range(7), 16, 17]].copy()
    other = full.assign(ts_code="000002.SZ", entry_signal=0, entry_signal_type="")
    result = _run(pd.concat([suspended, other], ignore_index=True))
    trade, = result["trades"]
    assert trade["reentry_watch_status"] == "EXPIRED"
    assert result["metadata"]["price_reentry_rule"]["calendar_source"] == "UNION_OF_STATE_DATES"


def test_reentry_keeps_current_day_industry_gate_after_decline_confirmation():
    from stock_selection.strategies.stock_short_high_state.industry_state import (
        IndustryConfig, attach_industry_states, calculate_industry_states,
    )
    states = _states([95, 100, 99, 98, 97])
    config = IndustryConfig(lookback_days=3)
    industry = calculate_industry_states(pd.DataFrame({
        "ts_code": "801011.SI", "trade_date": states.trade_date,
        "close": [100, 100, 100, 100, 90, 90, 100, 100, 110, 90],
    }), config=config)
    members = pd.DataFrame({"ts_code": ["000001.SZ"], "industry_code": ["801011.SI"], "industry_name": ["测试行业"]})
    states = attach_industry_states(states, industry, members, config=config)
    result = _run(states)
    first, second = result["trades"]
    blocked, = result["industry_blocked_entries"]
    assert blocked["trade_date"] == states.loc[8, "trade_date"]
    assert blocked["entry_signal_type"] == EXIT_PRICE_REENTRY
    assert second["entry_date"] == states.loc[9, "trade_date"]
    assert second["entry_industry_state"] == "DOWN"
    assert second["reentry_consecutive_down_days"] == 3
    assert first["reentry_watch_status"] == "CONSUMED"


def test_chart_subset_uses_saved_market_calendar(tmp_path):
    pytest.importorskip("plotly")
    from stock_selection.strategies.stock_short_high_state.visualization import generate_stock_short_state_charts
    full = _states([95, 100, *([101] * 9), 99, 98])
    states = full.loc[[*range(7), 16, 17]].copy()
    run = tmp_path / "run"
    run.mkdir()
    path = run / "states.csv"
    states.to_csv(path, index=False)
    full[["trade_date"]].to_csv(run / TRADING_DATES_FILENAME, index=False)
    cache = tmp_path / "cache"
    (cache / "daily").mkdir(parents=True)
    bars = full[["ts_code", "trade_date", "open", "high", "close", "vol"]].copy()
    bars["low"] = bars.close - 1
    bars.to_parquet(cache / "daily" / f"{bars.trade_date.max()}.parquet", index=False)
    result = generate_stock_short_state_charts(states_csv=path, stock_codes=["000001.SZ"], cache_root=cache)
    assert result["errors"] == []
    assert result["charts"][0]["short_intervals"] == 1


def test_switches_allow_comparing_existing_exit_rules():
    states = _states([95, 100, 99, 98])
    trade, = _run(states, config=ShortTradeBacktestConfig(reentry_enabled=False))["trades"]
    assert trade["status_reason"] == PROFIT_GIVEBACK_EXIT
    assert trade["reentry_watch_status"] == "DISABLED"
    trade, = _run(states, config=ShortTradeBacktestConfig(profit_protection_enabled=False))["trades"]
    assert trade["status"] == "OPEN"


def test_cutoffs_do_not_use_later_prices_or_backfilled_states():
    states = _states([95, 100, 99, 98, 94, 98, 97, 96])
    for position in range(4, len(states)):
        cutoff = states.loc[position, "trade_date"]
        full = build_short_trade_backtest(states, as_of_date=cutoff)
        prefix = build_short_trade_backtest(states.iloc[:position + 1], as_of_date=cutoff)
        assert full["trades"] == prefix["trades"]


def test_reports_and_chart_expose_confirmed_declines_and_previous_entry_price():
    pytest.importorskip("plotly")
    from stock_selection.strategies.stock_short_high_state.visualization import (
        build_stock_short_state_figure, merge_bars_and_states,
    )
    states = _states([95, 100, 99, 98, 94, 98, 97, 96])
    result = _run(states)
    reports = [result, *split_short_trade_backtest_by_average_market_cap(result).values(),
               filter_short_trade_backtest_by_stock_pool(result, ["000001.SZ"], pool_label="测试", pool_date="20260101", source_file="test.csv", selection_rule="ALL")]
    for report in reports:
        for rendered in (render_short_trade_backtest_markdown(report), render_short_trade_backtest_html(report)):
            assert "盈利回吐保护" in rendered
            assert "开仓价再开仓" in rendered
            assert "连续下跌" in rendered
            assert "等待下跌确认" in rendered
            assert "小于或等于上一笔实际开仓价" in rendered
            assert "后续观察和重开当天均不要求仍处于候选高点阶段" in rendered
            assert "必须持续处于同一周期的候选高点阶段" not in rendered
            assert "上涨激活" not in rendered
        json.dumps(report, ensure_ascii=False, allow_nan=False)
    assert result["summary"]["price_reentry_trades"] == 2
    assert result["summary"]["price_reentry_completed_trades"] == 1
    assert result["summary"]["price_reentry_average_return_pct"] == pytest.approx(0)
    bars = states[["ts_code", "trade_date", "open", "high", "close", "vol"]].copy()
    bars["low"] = bars.close - 1
    figure = build_stock_short_state_figure(merge_bars_and_states(bars, states), stock_code="000001.SZ", trade_backtest=result)
    entries = {trace.name: trace for trace in figure.data if "开仓" in trace.name}
    assert len(entries["做空开仓"].x) == 1
    assert len(entries["开仓价再开仓"].x) == 2
    assert all("连续下跌：2 天" in value for value in entries["开仓价再开仓"].text)
    assert all("收盘回到上一笔开仓价或以下" in value for value in entries["开仓价再开仓"].text)
    assert "上涨激活" not in figure.to_json()
    rule = result["metadata"]["price_reentry_rule"]
    assert rule["anchor"] == "LAST_EXECUTED_ENTRY_PRICE"
    assert rule["candidate_state_check"] == "EXIT_DAY_ONLY"
    assert rule["cancel_on_candidate_exit"] is False


@pytest.mark.parametrize(("field", "value"), [
    ("profit_protection_activation_pct", -.01), ("profit_protection_activation_pct", float("nan")),
    ("profit_protection_exit_pct", 1), ("profit_protection_exit_pct", float("inf")),
    ("reentry_window_trade_days", 0), ("reentry_window_trade_days", 1.5),
    ("reentry_window_trade_days", True), ("reentry_enabled", "True"),
    ("reentry_down_days", 1), ("reentry_down_days", 2.5),
    ("reentry_down_days", float("inf")), ("reentry_down_days", True),
])
def test_new_config_rejects_invalid_values(field, value):
    with pytest.raises(ValueError, match=field):
        replace(ShortTradeBacktestConfig(), **{field: value})
