"""First rebounds enter only after a high retest followed by a closing pullback."""

import json

import pandas as pd
import pytest

from stock_selection.strategies.stock_short_high_state.backtest import (
    HIGH_INVALIDATION_EXIT, PROFIT_REBOUND_EXIT, ShortTradeBacktestConfig,
    build_short_trade_backtest, split_short_trade_backtest_by_average_market_cap,
)
from stock_selection.strategies.stock_short_high_state.report import (
    render_short_trade_backtest_html, render_short_trade_backtest_markdown,
)
from stock_selection.strategies.stock_short_high_state.screen import (
    PEAK_1_PULLBACK_SIGNAL, StockShortState, apply_peak_1_entry_signals,
    calculate_stock_short_states,
)
from stock_selection.strategies.stock_short_high_state.stock_pool import build_shortable_stock_list


def _states(rebound):
    # At 65 the MA confirms a 60..65 trough; the first peak starts at 66.
    closes = [70, 80, 90, 100, 79, 70, 65, 60, 61, 62, 63, 64, 65, *rebound]
    return calculate_stock_short_states(pd.DataFrame({
        "ts_code": "000001.SZ", "stock_name": "测试股票",
        "trade_date": pd.bdate_range("2026-01-05", periods=len(closes)).strftime("%Y%m%d"),
        "open": closes, "high": [value + 20 for value in closes],
        "close": closes, "vol": 1000,
    }), lookback_days=3, high_heat_days=2, min_breakout_gain_pct=0)


def _run(states, **kwargs):
    return build_short_trade_backtest(states, as_of_date=states.trade_date.max(), **kwargs)


def _peaks(result):
    return [trade for trade in result["trades"] if trade["peak_number"] > 0]


@pytest.mark.parametrize("peak", [89.99, 90, 91, 100, 101])
def test_retest_height_uses_close_and_needs_a_subsequent_pullback(peak):
    states = _states([66, 85, peak, round(peak * .9, 3)])
    prefix = build_short_trade_backtest(states, as_of_date=states.iloc[-2].trade_date)
    assert not _peaks(prefix)
    entries = _peaks(_run(states))
    qualifies = 90 <= peak <= 100
    assert len(entries) == int(qualifies)
    assert (int(states.iloc[-1].entry_signal) == 51) is qualifies
    if qualifies:
        trade, = entries
        assert trade["entry_close"] == pytest.approx(peak * .9)
        assert trade["entry_date"] == states.iloc[-1].trade_date
        assert trade["entry_state"] == "PEAK_1"
        assert trade["entry_source"] == PEAK_1_PULLBACK_SIGNAL
        assert trade["entry_reference_high_close"] == 100
        assert trade["entry_peak_1_high_close"] == peak
        assert trade["entry_peak_1_high_date"] == states.iloc[-2].trade_date
        assert trade["entry_peak_1_drawdown_pct"] == pytest.approx(.1)
    else:
        assert states.iloc[-1].high > 90  # Intraday highs never arm the setup.


@pytest.mark.parametrize(("close", "qualifies"), [(85.51, False), (85.5, True), (85.49, True)])
def test_pullback_from_own_peak_includes_exact_ten_percent(close, qualifies):
    states = _states([66, 90, 95, close])
    entries = _peaks(_run(states))
    assert len(entries) == int(qualifies)
    if qualifies:
        assert entries[0]["entry_close"] == close
        assert entries[0]["entry_peak_1_high_close"] == 95
    assert states.iloc[-1].peak_1_drawdown_pct == pytest.approx(1 - close / 95)


def test_continuing_rise_only_arms_and_never_opens_a_position():
    states = _states([90, 95, 99])
    assert states.entry_signal.eq(51).sum() == 0
    assert not _peaks(_run(states))


def test_running_peak_updates_before_entry_and_is_not_the_original_high():
    states = _states([66, 90, 95, 99, 89.11, 89.10])
    prefix = build_short_trade_backtest(states, as_of_date=states.iloc[-2].trade_date)
    assert not _peaks(prefix)
    trade, = _peaks(_run(states))
    assert trade["entry_close"] == 89.10
    assert trade["entry_peak_1_high_close"] == 99
    assert trade["entry_peak_1_high_date"] == states.loc[16, "trade_date"]


@pytest.mark.parametrize(("close", "state"), [
    (60, StockShortState.TROUGH_REGION), (59, StockShortState.TROUGH_CANDIDATE),
])
def test_pullback_can_trigger_on_leaving_peak_one_for_a_trough(close, state):
    states = _states([66, 90, close])
    assert states.iloc[-1].observed_state == state
    trade, = _peaks(_run(states))
    assert trade["entry_close"] == close
    assert trade["entry_peak_1_high_close"] == 90
    assert trade["status"] == "OPEN"  # No same-day entry and exit.


def test_first_pullback_is_a_single_event_even_after_an_exit_or_recrossing():
    states = _states([66, 95, 85.5, 70, 71, 72, 73, 90, 81])
    trade, = _peaks(_run(states))
    assert trade["status_reason"] == PROFIT_REBOUND_EXIT
    assert states.entry_signal.eq(51).sum() == 1
    assert states.iloc[-1].observed_state == StockShortState.PEAK_1
    assert states.iloc[-1].entry_signal == 0


def test_second_and_later_peaks_do_not_enter_if_first_peak_never_retested():
    states = _states([66, 85, 60, 90, 60, 95, 60, 99])
    assert states.peak_number.max() == 4
    assert states.entry_signal.eq(51).sum() == 0
    assert not _peaks(_run(states))


def test_starting_second_peak_cancels_an_unconsumed_first_peak_setup():
    states = _states([66, 90, 86, 95, 81])
    # A shallow first-peak ending and a second rebound must not inherit the
    # first peak's setup, even if the second rebound later falls far enough.
    states.loc[15, "observed_state"] = int(StockShortState.TROUGH_REGION)
    states.loc[16:, "observed_state"] = int(StockShortState.PEAK_2)
    states.loc[16:, "peak_number"] = 2
    assert not _peaks(_run(states))


def test_breaking_original_high_before_pullback_cancels_the_setup():
    states = _states([66, 90, 101, 90])
    assert not _peaks(_run(states))


def test_high_invalidation_still_exits_a_position_after_pullback_entry():
    states = _states([66, 95, 85.5, 101])
    trade, = _peaks(_run(states))
    assert trade["status_reason"] == HIGH_INVALIDATION_EXIT
    assert trade["exit_close"] == 101


@pytest.mark.parametrize("reason", ["POSITION_OPEN", "EXIT_DAY"])
def test_pullback_while_holding_or_on_exit_day_is_skipped_without_replay(reason):
    states = _states([66, 90, 81, 85])
    states.loc[:, "entry_signal"] = 0
    states.loc[:, "entry_signal_type"] = ""
    # An independent candidate event opens on the preceding 90 close.
    states.loc[14, "entry_signal"] = 15
    states.loc[14, "entry_signal_type"] = "CANDIDATE_DRAWDOWN_THRESHOLD"
    if reason == "EXIT_DAY":
        states.loc[15, "observed_state"] = int(StockShortState.TROUGH_REGION)
        states.loc[15, "trough_confirm_date"] = states.loc[15, "trade_date"]
        states.loc[15, "trough_ma_close"] = 80
    result = _run(states)
    trade, = result["trades"]
    skipped, = trade["skipped_entries"]
    assert skipped["reason"] == reason
    assert skipped["entry_signal_type"] == PEAK_1_PULLBACK_SIGNAL
    assert not _peaks(result)


def test_industry_blocked_pullback_is_not_replayed_on_later_days():
    from stock_selection.strategies.stock_short_high_state.industry_state import (
        IndustryConfig, attach_industry_states, calculate_industry_states,
    )
    states = _states([66, 90, 81, 82])
    config = IndustryConfig(lookback_days=3)
    industry = calculate_industry_states(pd.DataFrame({
        "ts_code": "801011.SI", "trade_date": states.trade_date,
        "close": [100] * 4 + [80] * 11 + [110, 80],
    }), config=config)
    members = pd.DataFrame({
        "ts_code": ["000001.SZ"], "industry_code": ["801011.SI"], "industry_name": ["测试行业"],
    })
    states = attach_industry_states(states, industry, members, config=config)
    result = _run(states)
    assert not _peaks(result)
    blocked, = result["industry_blocked_entries"]
    assert blocked["trade_date"] == states.loc[15, "trade_date"]
    assert blocked["entry_signal_type"] == PEAK_1_PULLBACK_SIGNAL


@pytest.mark.parametrize(("position", "source"), [
    (13, "OBSERVED_PEAK_START"), (14, "PEAK_1_CONFIRMED_HIGH_THRESHOLD"),
])
def test_old_peak_start_or_height_events_are_rebuilt_as_pullbacks(position, source):
    states = _states([66, 90, 81, 85])
    legacy = states.copy()
    legacy.loc[legacy.entry_signal.eq(51), "entry_signal"] = 0
    legacy.loc[position, "entry_signal"] = 51
    legacy.loc[position, "entry_signal_type"] = source
    legacy.loc[position, "entry_reference_high_close"] = 100
    assert _run(legacy)["trades"] == _run(states)["trades"]
    assert legacy.loc[position, "entry_signal"] == 51  # Caller stays intact.


def test_setups_are_independent_for_each_stock_and_cycle():
    first = _states([66, 90, 81, 85])
    second = first.assign(ts_code="000002.SZ")
    next_cycle = first.copy()
    next_cycle["cycle_id"] += 2
    next_cycle["trade_date"] = pd.bdate_range("2026-03-02", periods=len(first)).strftime("%Y%m%d")
    combined = pd.concat([first, second, next_cycle], ignore_index=True)
    result = apply_peak_1_entry_signals(combined.sample(frac=1, random_state=2))
    assert result.entry_signal.eq(51).sum() == 3
    assert len(_peaks(_run(combined))) == 3


def test_uses_observed_states_and_no_future_final_peak():
    rebound = [66, 90, 81, 99, 85]
    states = _states(rebound)
    for position in range(12, len(states)):
        full = build_short_trade_backtest(states, as_of_date=states.loc[position, "trade_date"])
        prefix = _states(rebound[:max(0, position - 12)])
        assert full["trades"] == _run(prefix)["trades"]
    fake_peak = states.copy()
    fake_peak.loc[13:, "observed_state"] = int(StockShortState.TROUGH_REGION)
    assert not _peaks(_run(fake_peak))


def test_config_can_disable_or_change_height_and_pullback_thresholds():
    states = _states([66, 90, 81, 95, 85.5, 76])
    assert not _peaks(_run(states, config=ShortTradeBacktestConfig(peak_entry_states=frozenset())))
    trade, = _peaks(_run(states, config=ShortTradeBacktestConfig(peak_1_min_high_ratio=.95)))
    assert trade["entry_close"] == 85.5
    trade, = _peaks(_run(states, config=ShortTradeBacktestConfig(peak_1_pullback_pct=.2)))
    assert trade["entry_close"] == 76
    assert trade["entry_peak_1_high_close"] == 95


@pytest.mark.parametrize("ratio", [0, -.1, 1.01, float("nan"), float("inf"), True, "0.9"])
def test_invalid_high_ratio_is_rejected(ratio):
    with pytest.raises(ValueError, match="peak_1_min_high_ratio"):
        ShortTradeBacktestConfig(peak_1_min_high_ratio=ratio)


@pytest.mark.parametrize("ratio", [0, -.1, 1, float("nan"), float("inf"), True, "0.1"])
def test_invalid_pullback_is_rejected(ratio):
    with pytest.raises(ValueError, match="peak_1_pullback_pct"):
        ShortTradeBacktestConfig(peak_1_pullback_pct=ratio)


def test_current_stock_list_only_marks_the_first_pullback_signal():
    states = _states([66, 90, 81, 85])
    inventory = pd.DataFrame({
        "ts_code": ["000001.SZ"], "security_name": ["测试股票"],
        "available_quantity": [1000], "lending_date": ["20260101"], "eligible": [True],
    })
    for position, expected in ((14, False), (15, True), (16, False)):
        report = build_shortable_stock_list(
            states, inventory, as_of_date=states.loc[position, "trade_date"],
            source_file="test.csv", selection_rule="ALL",
        )
        row, = report["stocks"]
        assert row["has_entry_signal"] is expected


def test_reports_and_saved_chart_use_the_same_pullback_entry(tmp_path):
    pytest.importorskip("plotly")
    from stock_selection.strategies.stock_short_high_state.visualization import (
        build_stock_short_state_figure, merge_bars_and_states, read_saved_states,
    )
    states = _states([66, 90, 81, 85])
    result = _run(states)
    for report in [result, *split_short_trade_backtest_by_average_market_cap(result).values()]:
        json.dumps(report, ensure_ascii=False, allow_nan=False)
        for rendered in [render_short_trade_backtest_html(report), render_short_trade_backtest_markdown(report)]:
            assert "双峰开仓" in rendered
            assert "90.00%" in rendered
            assert "回落至少10.00%" in rendered
            assert "第一波峰高点日期 / 收盘价 / 回落比例" in rendered
    legacy = states.copy()
    legacy.loc[legacy.entry_signal.eq(51), "entry_signal"] = 0
    legacy.loc[14, "entry_signal"] = 51
    legacy.loc[14, "entry_signal_type"] = "PEAK_1_CONFIRMED_HIGH_THRESHOLD"
    path = tmp_path / "states.csv"
    legacy.to_csv(path, index=False)
    restored = read_saved_states(path)
    assert restored.loc[14, "entry_signal"] == 0
    assert restored.loc[15, "entry_signal_type"] == PEAK_1_PULLBACK_SIGNAL
    bars = states[["trade_date", "open", "high", "close", "vol"]].assign(low=states.close - 1)
    figure = build_stock_short_state_figure(
        merge_bars_and_states(bars, restored), stock_code="000001.SZ", trade_backtest=result,
    )
    entries, = [trace for trace in figure.data if trace.name == "做空开仓"]
    assert len(entries.x) == 2
    assert entries.y[-1] == 81
    assert "开仓时距第一波峰高点回落：10.00%" in entries.text[-1]
    assert result["metadata"]["entry_rule"]["peak_1_rule"]["pullback_pct"] == .1
