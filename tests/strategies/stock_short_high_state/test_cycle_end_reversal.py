"""Cycle boundaries and reversal signals use bars available on the signal day."""

import pandas as pd
import pytest
from dataclasses import replace

from stock_selection.strategies.stock_short_high_state.backtest import (
    build_short_trade_backtest,
    BACKTEST_CONFIG,
    split_short_trade_backtest_by_average_market_cap,
    filter_short_trade_backtest_by_stock_pool,
)
from stock_selection.strategies.stock_short_high_state.report import (
    build_stock_short_state_report,
    render_stock_short_state_markdown,
    save_short_trade_backtest_report,
)
from stock_selection.strategies.stock_short_high_state.screen import (
    StockShortState as S,
    calculate_stock_short_states,
)
from stock_selection.strategies.stock_short_high_state.visualization import (
    build_stock_short_state_figure,
    merge_bars_and_states,
    read_saved_states,
)


def _bars(prices):
    return pd.DataFrame({
        "ts_code": "000068.SZ",
        "name": "测试股票",
        "trade_date": pd.bdate_range("20260105", periods=len(prices)).strftime("%Y%m%d"),
        "close": prices,
    })


def _states(prices, **parameters):
    return calculate_stock_short_states(
        _bars([20.0] * 30 + prices), lookback_days=30,
        trough_confirm_days=3, **parameters,
    )


@pytest.mark.parametrize("price,ended", [(65.1, False), (65.0, True), (64.9, True)])
def test_end_threshold_is_inclusive(price, ended):
    states = _states([100, 80, price])
    assert (states.iloc[-1]["state"] == int(S.CYCLE_ENDED)) == ended
    assert states.iloc[30]["state"] == int(S.HIGH_CONFIRMED)


def test_gap_to_end_skips_rebound_trough_and_old_cycle_never_resumes():
    states = _states([100, 80, 60, 62, 75, 80])
    assert states.iloc[32:]["state_name"].tolist() == ["CYCLE_ENDED"] * 4
    assert states.iloc[32:]["peak_number"].eq(0).all()
    assert states.iloc[-1]["cycle_end_date"] == states.iloc[32]["trade_date"]
    assert states.iloc[-1]["cycle_end_threshold_close"] == pytest.approx(65)


def test_tenth_bar_confirms_reversal_without_backdating_signal():
    prices = [100, 80, 65, 60] + [61] * 9
    before = _states(prices[:-1])
    after = _states(prices)
    assert before.iloc[-1]["state"] == int(S.CYCLE_ENDED)
    assert after.iloc[-1]["state"] == int(S.REVERSAL_CONFIRMED)
    signal = after.iloc[-1]
    assert signal["reversal_low_date"] == after.iloc[33]["trade_date"]
    assert signal["reversal_low_close"] == 60
    assert signal["reversal_confirm_date"] == signal["trade_date"]
    assert after.iloc[33]["state"] == int(S.CYCLE_ENDED)
    pd.testing.assert_frame_equal(before, after.iloc[:-1].reset_index(drop=True))


def test_new_low_resets_clock_and_equal_low_does_not_reset_it():
    prices = [100, 65, 64, 64.1, 64.2, 63] + [63] * 8
    before = _states(prices)
    after = _states(prices + [63])
    assert before.iloc[-1]["state_name"] == "CYCLE_ENDED"
    assert after.iloc[-1]["state_name"] == "REVERSAL_CONFIRMED"
    assert after.iloc[-1]["reversal_low_date"] == after.iloc[35]["trade_date"]


def test_breaking_confirmed_low_waits_for_another_ten_bars():
    prices = [100, 65, 60] + [61] * 9
    first = _states(prices)
    reset = _states(prices + [59])
    again = _states(prices + [59] + [60] * 9)
    assert first.iloc[-1]["state_name"] == "REVERSAL_CONFIRMED"
    assert reset.iloc[-1]["state_name"] == "CYCLE_ENDED"
    assert reset.iloc[-1]["reversal_confirm_date"] == ""
    assert again.iloc[-1]["state_name"] == "REVERSAL_CONFIRMED"
    assert again.iloc[-1]["reversal_low_close"] == 59
    assert again.iloc[-1]["cycle_end_date"] == first.iloc[-1]["cycle_end_date"]


def test_new_window_high_starts_separate_cycle_and_resets_peaks():
    states = _states([100, 65, 60] + [61] * 9 + [101])
    assert states.iloc[-2]["state_name"] == "REVERSAL_CONFIRMED"
    assert states.iloc[-1]["state_name"] == "HIGH_CANDIDATE"
    assert states.iloc[-1]["cycle_id"] == 2
    assert states.iloc[-1]["peak_number"] == 0
    assert states.iloc[-1]["cycle_end_date"] == ""


def test_end_from_peak_closes_trade_on_end_date_and_preserves_earlier_exit(tmp_path):
    states = _states([100, 84, 70, 70.1, 70, 78, 65, 60] + [61] * 9)
    states["total_mv"] = 2_000_000
    report = build_short_trade_backtest(
        states, as_of_date=states.iloc[-1]["trade_date"],
        config=replace(BACKTEST_CONFIG, include_peak_entries=True),
    )
    assert report["summary"]["completed_trades"] == 2
    candidate, peak = report["trades"]
    assert candidate["exit_date"] == states.iloc[32]["trade_date"]
    assert peak["entry_state"] == "PEAK_1"
    assert peak["exit_state"] == "CYCLE_ENDED"
    assert peak["status_reason"] == "CYCLE_END_DRAWDOWN"
    assert peak["exit_close"] == 65
    assert peak["exit_date"] == states.iloc[36]["trade_date"]
    assert peak["return_pct"] == pytest.approx(1 - 65 / 78)
    assert not states.iloc[36:]["state_name"].str.startswith("PEAK_").any()
    assert split_short_trade_backtest_by_average_market_cap(report)["below"]["summary"]["completed_trades"] == 2
    assert filter_short_trade_backtest_by_stock_pool(
        report, ["000068.SZ"], pool_label="测试池", pool_date="20260105",
        source_file="test.xls", selection_rule="测试",
    )["summary"]["completed_trades"] == 2
    path = save_short_trade_backtest_report(report, records_root=tmp_path)
    assert "CYCLE_ENDED" in path.read_text(encoding="utf-8")
    assert "CYCLE_ENDED" in path.with_suffix(".html").read_text(encoding="utf-8")


def test_direct_end_closes_pending_candidate_trade():
    states = _states([100, 84, 60])
    trade = build_short_trade_backtest(states, as_of_date="20261016")["trades"][0]
    assert trade["exit_state"] == "CYCLE_ENDED"
    assert trade["status"] == "COMPLETED"
    assert trade["exit_close"] == 60
    assert trade["return_pct"] == pytest.approx(1 - 60 / 84)


def test_reversal_days_and_cycle_threshold_are_configurable():
    states = _states([100, 65, 60, 59] + [59.5] * 11,
                     cycle_end_drawdown_pct=0.40, reversal_confirm_days=12)
    assert states.iloc[31]["state_name"] == "TROUGH_CANDIDATE"
    assert states.iloc[32]["state_name"] == "CYCLE_ENDED"
    assert states.iloc[-1]["state_name"] == "REVERSAL_CONFIRMED"


@pytest.mark.parametrize("parameters", [
    {"cycle_end_drawdown_pct": 0.30},
    {"cycle_end_drawdown_pct": 1.0},
    {"reversal_confirm_days": 3},
    {"reversal_confirm_days": 0},
])
def test_rejects_thresholds_that_cannot_form_stricter_reversal(parameters):
    with pytest.raises(ValueError):
        _states([100, 65], **parameters)


def test_report_and_chart_distinguish_low_date_from_confirmation_date(tmp_path):
    pytest.importorskip("plotly")
    states = _states([100, 84, 65, 60] + [61] * 9)
    state_path = tmp_path / "states.csv"
    states.to_csv(state_path, index=False)
    loaded = read_saved_states(state_path)
    bars = loaded[["ts_code", "trade_date", "close"]].copy()
    bars["open"] = bars["close"]
    bars["high"] = bars["close"] * 1.01
    bars["low"] = bars["close"] * 0.99
    bars["vol"] = 100
    chart = merge_bars_and_states(bars, loaded)
    figure = build_stock_short_state_figure(chart, stock_code="000068.SZ")
    traces = {trace.name: trace for trace in figure.data}
    low_date = pd.to_datetime(states.iloc[-1]["reversal_low_date"])
    confirm_date = pd.to_datetime(states.iloc[-1]["reversal_confirm_date"])
    assert pd.Timestamp(traces["反转低点（事后确认）"].x[0]) == low_date
    assert pd.Timestamp(traces["反转确认日"].x[0]) == confirm_date
    assert len(traces["本轮行情结束"].x) == 1
    assert any(shape.y0 == 65 for shape in figure.layout.shapes)
    report = build_stock_short_state_report(
        states, as_of_date="20261016", lookback_days=30,
        candidate_pullback_pct=0.15, confirm_drawdown_pct=0.30,
        rebound_pct=0.10, high_heat_days=10, trough_confirm_days=3,
    )
    markdown = render_stock_short_state_markdown(report)
    assert "35.0%" in markdown
    assert "10个交易日（含低点当天）" in markdown


def test_original_new_high_invalidation_remains_before_cycle_end():
    states = _states([100, 84, 70, 70.1, 70, 78, 101, 102])
    assert states.iloc[-2]["state"] == int(S.INVALID_HIGH_NEW_HIGH)
    assert states.iloc[-1]["cycle_id"] == 2
    assert states.iloc[-1]["state"] == int(S.HIGH_CANDIDATE)


@pytest.mark.parametrize("prices,expected_state", [
    ([100, 65, 60], S.CYCLE_ENDED),
    ([100, 65, 60] + [61] * 9, S.REVERSAL_CONFIRMED),
])
def test_plan_two_classifies_ended_and_reversal_as_non_short_scope(prices, expected_state):
    from stock_selection.screening_schemes.plan_two.scheme import (
        PlanTwoClassification, run_plan_two,
    )
    from tests.screening_schemes.plan_two.test_scheme import _lending

    daily = _bars([20.0] * 30 + prices)
    as_of_date = daily.iloc[-1]["trade_date"]
    _, latest, result = run_plan_two(
        _lending(["000068"], as_of_date), daily, as_of_date=as_of_date,
        lookback_days=30, trough_confirm_days=3,
    )
    assert latest.iloc[0]["state"] == int(expected_state)
    assert result.iloc[0]["classification"] == int(PlanTwoClassification.NON_SHORT_SCOPE)
    assert result.iloc[0]["cycle_end_date"]
    assert result.iloc[0]["cycle_end_threshold_close"] == pytest.approx(65)
