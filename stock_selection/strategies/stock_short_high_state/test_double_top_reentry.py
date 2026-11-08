"""Double tops observe at 95% of a frozen high and reenter at a 10% pullback."""

import json

import pandas as pd
import pytest

from stock_selection.strategies.stock_short_high_state.backtest import (
    DOUBLE_TOP_REENTRY, MAX_DRAWDOWN_STOP, PROFIT_REBOUND_EXIT,
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
from stock_selection.strategies.stock_short_high_state.stock_pool import build_shortable_stock_list


def _states(after_entry, *, isolate=True, first_entry_close=90):
    closes = [70, 80, 90, 100, first_entry_close, *after_entry]
    states = calculate_stock_short_states(pd.DataFrame({
        "ts_code": "000001.SZ", "stock_name": "测试股票",
        "trade_date": pd.bdate_range("2026-01-05", periods=len(closes)).strftime("%Y%m%d"),
        "open": closes, "high": [c + 20 for c in closes], "close": closes, "vol": 1000,
    }), lookback_days=3, high_heat_days=2, min_breakout_gain_pct=0)
    if isolate:
        # Keep the genuine first entry and prices/states, isolate the extra
        # execution rule from subsequent independent candidate events.
        states.loc[5:, "entry_signal"] = 0
        states.loc[5:, "entry_signal_type"] = ""
    states["total_mv"] = 2_000_000
    return states


def _run(states, **kwargs):
    return build_short_trade_backtest(states, as_of_date=states.trade_date.max(), **kwargs)


def _reentries(result):
    return [t for t in result["trades"] if t["entry_source"] == DOUBLE_TOP_REENTRY]


@pytest.mark.parametrize(("close", "qualifies"), [(90.01, False), (90, True), (89.99, True), (88, True)])
def test_frozen_high_pullback_uses_actual_close_and_includes_equality(close, qualifies):
    states = _states([108, 99, close])
    saved = states.copy(deep=True)
    result = _run(states)
    assert result["trades"][0]["status_reason"] == MAX_DRAWDOWN_STOP
    assert len(_reentries(result)) == int(qualifies)
    if qualifies:
        trade, = _reentries(result)
        assert trade["entry_close"] == close
        assert trade["entry_state"] == DOUBLE_TOP_REENTRY
        assert trade["entry_reference_high_close"] == 100
        assert trade["entry_double_top_origin_entry_close"] == 90
        assert trade["entry_double_top_retest_threshold_close"] == 95
        assert trade["entry_double_top_reentry_threshold_close"] == 90
        assert trade["entry_double_top_origin_entry_date"] == states.loc[4, "trade_date"]
        assert trade["entry_double_top_previous_exit_date"] == states.loc[5, "trade_date"]
        assert trade["entry_double_top_retest_date"] == states.loc[5, "trade_date"]
        assert trade["entry_date"] > trade["entry_double_top_previous_exit_date"]
    pd.testing.assert_frame_equal(states, saved)


@pytest.mark.parametrize(("retest", "qualifies"), [(94.99, False), (95, True), (99.99, True), (100, True), (105, True)])
def test_retest_can_happen_after_exit_and_uses_close_not_intraday_high(retest, qualifies):
    states = _states([94, retest, 90])
    assert states.loc[6, "high"] > 100
    states.loc[6, "open"] = 110  # Neither the open nor intraday high can arm it.
    result = _run(states, config=ShortTradeBacktestConfig(max_drawdown_pct=.04))
    assert result["trades"][0]["exit_date"] == states.loc[5, "trade_date"]
    assert len(_reentries(result)) == int(qualifies)
    if qualifies:
        assert _reentries(result)[0]["entry_double_top_retest_date"] == states.loc[6, "trade_date"]


def test_exit_day_can_start_observation_at_exactly_95_percent():
    states = _states([95, 90.01, 90])
    trade, = _reentries(_run(states, config=ShortTradeBacktestConfig(max_drawdown_pct=.05)))
    assert trade["entry_double_top_retest_date"] == states.loc[5, "trade_date"]
    assert trade["entry_date"] == states.iloc[-1].trade_date
    assert trade["entry_double_top_retest_threshold_close"] == 95


def test_rising_second_top_does_not_raise_frozen_high_pullback_threshold():
    states = _states([108, 110, 99, 90.01, 90])
    trade, = _reentries(_run(states))
    assert trade["entry_close"] == 90
    assert trade["entry_date"] == states.iloc[-1].trade_date
    assert trade["entry_reference_high_close"] == 100
    assert states.loc[7, "reference_high_close"] == 110


def test_gap_in_original_entry_does_not_replace_fixed_high_percentage():
    states = _states([106, 95, 90], first_entry_close=88)
    result = _run(states)
    trade, = _reentries(result)
    assert result["trades"][0]["entry_close"] == 88
    assert trade["entry_double_top_origin_entry_close"] == 88
    assert trade["entry_reference_high_close"] == 100
    assert trade["entry_double_top_reentry_threshold_close"] == 90
    assert trade["entry_close"] == 90


def test_retest_below_original_high_can_reenter_when_cost_gate_blocks_normal_signal():
    states = _states([81, 82, 83, 84, 95, 90], isolate=False)
    assert states.loc[10, "entry_signal"] == 15
    states.loc[10, "entry_signal"] = 0
    states.loc[10, "entry_signal_type"] = ""
    states.loc[10, "entry_breakout_qualified"] = False
    result = _run(states)
    assert result["trades"][0]["status_reason"] == PROFIT_REBOUND_EXIT
    trade, = _reentries(result)
    assert states.loc[8:, "close"].max() == 95
    assert trade["entry_double_top_retest_date"] == states.loc[9, "trade_date"]
    assert trade["entry_close"] == 90


def test_held_trade_cannot_prepare_reentry_or_use_a_retest_before_its_exit():
    config = ShortTradeBacktestConfig(max_drawdown_pct=.5)
    assert not _reentries(_run(_states([100, 95, 90]), config=config))
    states = _states([100, 81, 82, 83, 84, 89, 90])
    result = _run(states, config=config)
    assert result["trades"][0]["status_reason"] == PROFIT_REBOUND_EXIT
    assert not _reentries(result)  # 100 was reached while the trade was still held.


def test_non_stop_exit_also_prepares_a_double_top():
    states = _states([81, 82, 83, 84, 100, 90])
    result = _run(states)
    assert result["trades"][0]["status_reason"] == PROFIT_REBOUND_EXIT
    assert len(_reentries(result)) == 1


@pytest.mark.parametrize("tail", [[108, 79, 100, 90], [108, 100, 85, 100, 90], [79, 101, 108, 90]])
def test_entering_trough_candidate_before_or_after_exit_cancels_setup(tail):
    states = _states(tail)
    assert states.observed_state.eq(int(StockShortState.TROUGH_CANDIDATE)).any()
    assert not _reentries(_run(states))


def test_cycle_change_cancels_pending_setup():
    states = _states([108, 100, 90])
    states.loc[6:, "cycle_id"] = 2
    assert not _reentries(_run(states))


def test_uses_observed_states_instead_of_future_historical_labels():
    states = _states([108, 100, 90])
    states.loc[5:, "state"] = int(StockShortState.TROUGH_REGION)
    assert len(_reentries(_run(states))) == 1


def test_reentry_must_close_and_retest_again_before_another_reentry():
    states = _states([108, 90, 108, 90])
    trades = _reentries(_run(states))
    assert len(trades) == 2
    assert [t["entry_date"] for t in trades] == states.loc[[6, 8], "trade_date"].tolist()
    assert [t["entry_double_top_origin_entry_close"] for t in trades] == [90, 90]
    assert trades[1]["entry_double_top_previous_exit_date"] == states.loc[7, "trade_date"]
    assert trades[1]["entry_double_top_retest_date"] == states.loc[7, "trade_date"]
    # A second exit below 95 must not reuse the first observation.
    states = _states([108, 90, 94, 90])
    assert len(_reentries(_run(states, config=ShortTradeBacktestConfig(max_drawdown_pct=.04)))) == 1
    states = _states([108, 90, 94, 95, 90])
    trades = _reentries(_run(states, config=ShortTradeBacktestConfig(max_drawdown_pct=.04)))
    assert len(trades) == 2
    assert trades[1]["entry_double_top_retest_date"] == states.loc[8, "trade_date"]
    assert [t["entry_double_top_reentry_threshold_close"] for t in trades] == [90, 90]


def test_industry_blocked_crossing_is_not_replayed():
    from stock_selection.strategies.stock_short_high_state.industry_state import (
        IndustryConfig, attach_industry_states, calculate_industry_states,
    )
    states = _states([108, 90, 89])
    config = IndustryConfig(lookback_days=3)
    industry = calculate_industry_states(pd.DataFrame({
        "ts_code": "801011.SI", "trade_date": states.trade_date,
        "close": [100, 100, 100, 100, 80, 80, 110, 80],
    }), config=config)
    members = pd.DataFrame({
        "ts_code": ["000001.SZ"], "industry_code": ["801011.SI"], "industry_name": ["测试行业"],
    })
    states = attach_industry_states(states, industry, members, config=config)
    result = _run(states)
    assert not _reentries(result)
    blocked, = result["industry_blocked_entries"]
    assert blocked["entry_signal_type"] == DOUBLE_TOP_REENTRY
    assert blocked["trade_date"] == states.loc[6, "trade_date"]


def test_candidate_signal_collision_opens_one_trade_and_does_not_recheck_cost_gate():
    states = _states([108, 90], isolate=False)
    assert states.loc[6, "entry_signal"] == 15
    assert len(_run(states)["trades"]) == 2
    assert len(_reentries(_run(states))) == 1
    # Original entry already qualified; the extra rule is independent of the
    # cost gate on today's date, even if the independent signal is absent.
    states.loc[6, "entry_signal"] = 0
    states.loc[6, "entry_signal_type"] = ""
    states.loc[6, "entry_breakout_qualified"] = False
    assert len(_reentries(_run(states))) == 1


def test_existing_candidate_can_open_earlier_and_end_pending_observation():
    states = _states([120, 108, 90], isolate=False)
    result = _run(states)
    assert result["trades"][1]["entry_close"] == 108
    assert result["trades"][1]["entry_source"] == "CANDIDATE_DRAWDOWN_THRESHOLD"
    assert not _reentries(result)


def test_no_future_data_and_setups_are_per_stock():
    states = _states([108, 105, 90, 85, 86])
    for position in range(4, len(states)):
        date = states.loc[position, "trade_date"]
        full = build_short_trade_backtest(states, as_of_date=date)
        prefix = _run(states.iloc[:position + 1])
        assert full["trades"] == prefix["trades"]
    other = states.assign(ts_code="000002.SZ", entry_signal=0, entry_signal_type="")
    result = _run(pd.concat([states, other], ignore_index=True))
    assert {t["ts_code"] for t in _reentries(result)} == {"000001.SZ"}


def test_config_can_disable_double_top_without_disabling_existing_signals():
    states = _states([108, 90], isolate=False)
    result = _run(states, config=ShortTradeBacktestConfig(double_top_reentry_enabled=False))
    assert not _reentries(result)
    assert len(result["trades"]) == 2
    with pytest.raises(ValueError, match="double_top_reentry_enabled"):
        ShortTradeBacktestConfig(double_top_reentry_enabled=1)


@pytest.mark.parametrize("overrides", [
    {"double_top_retest_high_ratio": True},
    {"double_top_retest_high_ratio": float("nan")},
    {"double_top_retest_high_ratio": 1.01},
    {"double_top_retest_high_ratio": .9},
    {"double_top_pullback_pct": 0},
    {"double_top_pullback_pct": 1},
    {"double_top_pullback_pct": float("inf")},
    {"double_top_pullback_pct": True},
])
def test_config_rejects_invalid_double_top_price_ratios(overrides):
    with pytest.raises(ValueError, match="double_top_"):
        ShortTradeBacktestConfig(**overrides)


def test_custom_ratios_use_frozen_high_and_match_reported_metadata():
    states = _states([81, 82, 83, 84, 96, 90, 85])
    config = ShortTradeBacktestConfig(double_top_retest_high_ratio=.96, double_top_pullback_pct=.15)
    result = _run(states, config=config)
    trade, = _reentries(result)
    assert trade["entry_double_top_retest_threshold_close"] == 96
    assert trade["entry_double_top_reentry_threshold_close"] == 85
    assert trade["entry_close"] == 85
    rule = result["metadata"]["entry_rule"]["double_top_reentry_rule"]
    assert rule["retest_high_ratio"] == .96
    assert rule["pullback_pct"] == .15


def test_reports_chart_and_current_stock_list_share_the_execution_source():
    states = _states([108, 90])
    result = _run(states)
    reports = [result, *split_short_trade_backtest_by_average_market_cap(result).values()]
    reports.append(filter_short_trade_backtest_by_stock_pool(
        result, ["000001.SZ"], pool_label="测试", pool_date="20260101",
        source_file="test.csv", selection_rule="ALL",
    ))
    for report in reports:
        json.dumps(report, ensure_ascii=False, allow_nan=False)
        for rendered in [render_short_trade_backtest_html(report), render_short_trade_backtest_markdown(report)]:
            assert "双峰再开仓" in rendered
            assert "第一波峰回落开仓" in rendered
            assert "双峰原开仓日 / 原开仓价 / 前次平仓日 / 观察启动日 / 观察门槛 / 固定开仓门槛" in rendered
            assert "原高点的95.00%" in rendered
            assert "原高点×（1－10.00%）" in rendered
    assert result["summary"]["double_top_reentry_trades"] == 1
    inventory = pd.DataFrame({
        "ts_code": ["000001.SZ"], "security_name": ["测试股票"],
        "available_quantity": [100], "lending_date": ["20260101"], "eligible": [True],
    })
    for supplied in [None, result]:
        stock_list = build_shortable_stock_list(
            states, inventory, as_of_date=states.trade_date.max(),
            source_file="test.csv", selection_rule="ALL", trade_backtest=supplied,
        )
        row, = stock_list["stocks"]
        assert row["has_entry_signal"]
        assert row["entry_signal_type"] == DOUBLE_TOP_REENTRY
    pytest.importorskip("plotly")
    from stock_selection.strategies.stock_short_high_state.visualization import (
        build_stock_short_state_figure, merge_bars_and_states,
    )
    bars = states[["trade_date", "open", "high", "close", "vol"]].assign(low=states.close - 1)
    figure = build_stock_short_state_figure(
        merge_bars_and_states(bars, states), stock_code="000001.SZ", trade_backtest=result,
    )
    entries, = [trace for trace in figure.data if trace.name == "做空开仓"]
    assert len(entries.x) == 2
    assert "双峰按原高点回落再开仓" in entries.text[-1]
    assert "观察启动日" in entries.text[-1]
    assert "观察门槛：95.00" in entries.text[-1]
    assert "固定开仓门槛：90.00" in entries.text[-1]
