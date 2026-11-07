"""Cost-region entries must use only peaks observable before their breakout."""

import json

import pandas as pd
import pytest

from stock_selection.strategies.stock_short_high_state.backtest import build_short_trade_backtest
from stock_selection.strategies.stock_short_high_state.cost_zone import COST_ZONE_COLUMNS
from stock_selection.strategies.stock_short_high_state.report import (
    build_stock_short_state_report, render_stock_short_state_markdown, render_short_trade_backtest_html,
)
from stock_selection.strategies.stock_short_high_state.screen import (
    BREAKOUT_BASELINE_RULE, StockShortState, calculate_stock_short_states,
)
from stock_selection.strategies.stock_short_high_state.visualization import (
    _state_hover, _trade_hover, build_stock_short_state_figure, read_saved_states,
)


def _history():
    # Three separated 16-bar peaks; all are confirmed before the breakout.
    return sum(([80] * 8 + [peak] + [80] * 7 for peak in (100, 110, 120)), [])


def _states(closes, **kwargs):
    daily = pd.DataFrame({
        "ts_code": "000001.SZ", "stock_name": "测试股票",
        "trade_date": pd.bdate_range("2026-01-05", periods=len(closes)).strftime("%Y%m%d"),
        "open": closes, "high": [close + 100 for close in closes],
        "close": closes, "vol": 1000,
    })
    kwargs.setdefault("lookback_days", 48)
    return calculate_stock_short_states(daily, **kwargs)


def _backtest(states):
    return build_short_trade_backtest(states, as_of_date=states.iloc[-1]["trade_date"])


@pytest.mark.parametrize(("high", "expected"), [(164.99, 0), (165, 15), (165.01, 15)])
def test_fifty_percent_over_peak_mean_includes_equality(high, expected):
    states = _states(_history() + [high, high * 0.9])
    row = states.iloc[-1]
    assert row["breakout_base_close"] == 110
    assert row["cost_peak_count"] == 3
    assert row["observed_state"] == StockShortState.HIGH_CANDIDATE_PULLBACK
    assert row["entry_signal"] == expected
    assert len(_backtest(states)["trades"]) == bool(expected)


def test_spike_is_classified_but_filtered_even_with_very_high_intraday_prices():
    states = _states(_history() + [140, 126])
    row = states.iloc[-1]
    assert row["observed_state"] == StockShortState.HIGH_CANDIDATE_PULLBACK
    assert row["entry_breakout_reason"] == "GAIN_BELOW_THRESHOLD"
    assert row["breakout_gain_pct"] == pytest.approx(140 / 110 - 1)
    assert not _backtest(states)["trades"]


def test_default_window_is_exactly_sixteen_and_waits_for_seven_following_bars():
    closes = [80] * 8 + [100] + [80] * 7 + [101]
    states = _states(closes, lookback_days=16)
    assert states.iloc[15]["cost_peak_count"] == 0
    row = states.iloc[16]
    assert row["cost_peak_window_days"] == 16
    point, = json.loads(row["cost_peak_points"])
    assert point == {"trade_date": states.iloc[8]["trade_date"], "close": 100.0, "confirm_date": states.iloc[15]["trade_date"]}
    assert row["cost_zone_freeze_date"] == row["trade_date"]
    assert row["cost_zone_frozen"]


def test_plateau_has_one_peak_at_its_last_day_and_one_peak_is_a_valid_baseline():
    states = _states([80] * 8 + [100] * 3 + [80] * 7 + [165, 148.5], lookback_days=18)
    row = states.iloc[-1]
    points = json.loads(row["cost_peak_points"])
    assert len(points) == 1
    assert points[0]["trade_date"] == states.iloc[10]["trade_date"]
    assert row["breakout_base_close"] == 100
    assert row["entry_signal"] == 15


def test_frozen_peaks_survive_meaningful_pullbacks_new_highs_and_new_cycles():
    states = _states(_history() + [170, 136, 150, 181, 162.9])
    frozen = states.iloc[48]
    assert states.iloc[51]["observed_state"] == StockShortState.INVALID_HIGH_NEW_HIGH
    assert states.iloc[-1]["cycle_id"] > states.iloc[48]["cycle_id"]
    for column in ("cost_peak_points", "breakout_base_close", "cost_zone_freeze_date", "cost_zone_low", "cost_zone_high"):
        assert states.loc[48:, column].eq(frozen[column]).all()
    assert states.iloc[-1]["cost_zone_high_close"] == 181
    assert states.iloc[-1]["entry_signal"] == 15
    _backtest(states)


def test_frozen_peaks_do_not_age_out_or_absorb_post_breakout_peaks():
    states = _states(_history() + [170, 150] + [160] * 60 + [180, 162])
    row = states.iloc[-1]
    assert row["cost_peak_points"] == states.iloc[48]["cost_peak_points"]
    assert row["breakout_base_close"] == 110
    assert row["cost_zone_freeze_date"] == states.iloc[48]["trade_date"]
    assert row["cost_peak_count"] == 3
    assert all(point["close"] <= 120 for point in json.loads(row["cost_peak_points"]))


@pytest.mark.parametrize(("close", "status"), [(120.01, "FROZEN"), (120, "RESET"), (119.99, "RESET")])
def test_return_to_upper_boundary_unfreezes_including_equality(close, status):
    states = _states(_history() + [170, close])
    row = states.iloc[-1]
    assert row["cost_zone_status"] == status
    assert bool(row["cost_zone_frozen"]) == (status == "FROZEN")
    assert bool(row["entry_breakout_qualified"]) == (status == "FROZEN")
    assert bool(row["entry_signal"]) == (status == "FROZEN")
    _backtest(states)


def test_rebuild_begins_next_day_and_can_include_newly_confirmed_highs():
    states = _states(_history() + [170] + [150] * 7 + [120, 140])
    reset, rebuilt = states.iloc[-2], states.iloc[-1]
    assert reset["cost_zone_status"] == "RESET"
    assert reset["breakout_base_close"] == 110
    assert reset["entry_breakout_reason"] == "RETURNED_TO_ZONE"
    assert reset["entry_signal"] == 0
    assert rebuilt["cost_zone_status"] == "TRACKING"
    assert not rebuilt["cost_zone_freeze_date"]
    assert rebuilt["cost_zone_high"] == 170
    assert 170 in [point["close"] for point in json.loads(rebuilt["cost_peak_points"])]
    assert rebuilt["breakout_base_close"] != 110


def test_old_peak_outside_lookback_is_not_in_a_new_snapshot():
    states = _states([800] * 8 + [1000] + [800] * 7 + _history() + [170, 153])
    row = states.iloc[-1]
    points = json.loads(row["cost_peak_points"])
    assert points
    assert all(point["close"] <= 120 for point in points)
    assert row["cost_zone_high"] == 120


def test_breakout_requires_strictly_exceeding_the_upper_boundary():
    states = _states(_history() + [120, 120.01])
    assert states.iloc[-2]["cost_zone_status"] == "TRACKING"
    assert states.iloc[-2]["entry_breakout_reason"] == "ZONE_NOT_FROZEN"
    assert states.iloc[-1]["cost_zone_status"] == "FROZEN"


def test_missing_peaks_block_entry_but_zero_threshold_disables_the_filter():
    closes = [50, 80, 100, 150, 135]
    states = _states(closes, lookback_days=3)
    assert states.iloc[-1]["entry_breakout_reason"] == "BASELINE_MISSING"
    assert states.iloc[-1]["entry_signal"] == 0
    disabled = _states(closes, lookback_days=3, min_breakout_gain_pct=0)
    assert disabled.iloc[-1]["entry_breakout_reason"] == "FILTER_DISABLED"
    assert disabled.iloc[-1]["entry_signal"] == 15
    assert len(_backtest(disabled)["trades"]) == 1


def test_future_bars_do_not_modify_cost_snapshots_or_observable_signals():
    closes = _history() + [130, 125, 170, 150, 180, 144] + [150] * 8 + [120, 140, 190, 171]
    full = _states(closes)
    columns = [*COST_ZONE_COLUMNS, "breakout_base_close", "breakout_base_date", "breakout_gain_pct", "entry_breakout_qualified", "entry_signal", "observed_state"]
    for cut in (16, 48, 49, 52, 58, len(closes) - 1):
        prefix = _states(closes[:cut])
        pd.testing.assert_frame_equal(full.iloc[:cut][columns], prefix[columns])


def test_later_qualification_does_not_backfill_an_earlier_rejected_pullback():
    states = _states(_history() + [140, 126, 170, 153, 152])
    assert states.loc[[49, 51, 52], "entry_signal"].tolist() == [0, 15, 0]
    trade, = _backtest(states)["trades"]
    assert trade["entry_date"] == states.iloc[51]["trade_date"]


def test_signal_survives_high_confirmation_on_the_same_day():
    states = _states(_history() + [170, 136])
    assert states.iloc[-1]["observed_state"] == StockShortState.TROUGH_CANDIDATE
    assert states.iloc[-1]["entry_signal"] == 15
    assert len(_backtest(states)["trades"]) == 1


@pytest.mark.parametrize("window", [True, 1, 2, 16.5, float("nan")])
def test_invalid_peak_window_is_rejected(window):
    with pytest.raises(ValueError, match="cost_peak_window_days"):
        _states(_history(), cost_peak_window_days=window)


@pytest.mark.parametrize("minimum", [-1, float("nan"), float("inf")])
def test_invalid_minimum_is_rejected(minimum):
    with pytest.raises(ValueError, match="min_breakout_gain_pct"):
        _states(_history(), min_breakout_gain_pct=minimum)


def test_configurable_window_threshold_csv_reports_and_chart_share_the_same_snapshot(tmp_path):
    states = _states(_history() + [144, 129.6], min_breakout_gain_pct=0.2, cost_peak_window_days=5)
    result = _backtest(states)
    trade, = result["trades"]
    assert trade["entry_cost_peak_window_days"] == 5
    assert trade["entry_cost_peak_points"] == states.iloc[-1]["cost_peak_points"]
    assert trade["entry_breakout_base_close"] == 110
    assert result["metadata"]["entry_rule"]["cost_peak_window_days"] == 5
    assert result["metadata"]["entry_rule"]["breakout_baseline"] == BREAKOUT_BASELINE_RULE
    html = render_short_trade_backtest_html(result)
    assert "20.00%" in html and "极值窗口共5日" in html
    report = build_stock_short_state_report(
        states, as_of_date=str(states.trade_date.max()), lookback_days=48,
        min_breakout_gain_pct=0.2, cost_peak_window_days=5,
        confirm_drawdown_pct=0.2, rebound_pct=0.1, high_heat_days=10, trough_ma_days=8,
    )
    markdown = render_stock_short_state_markdown(report)
    assert "峰值均值" in markdown and "110.00" in markdown and "极值窗口共5日" in markdown
    assert "历史峰值均值" in _trade_hover(trade)
    assert "冻结日期" in _trade_hover(trade)
    assert "成本区状态" in _state_hover(states.iloc[-1].to_dict())
    path = tmp_path / "states.csv"
    states.to_csv(path, index=False)
    saved = read_saved_states(path)
    assert _backtest(saved)["trades"][0]["entry_cost_peak_points"] == trade["entry_cost_peak_points"]
    chart = saved.copy()
    chart["date"] = pd.to_datetime(chart["trade_date"], format="%Y%m%d")
    chart["low"] = chart["close"] - 1
    figure = build_stock_short_state_figure(chart, stock_code="000001.SZ")
    assert {"成本区下沿", "成本区上沿", "历史峰值均值", "开仓涨幅门槛线", "成本区历史峰值"}.issubset({trace.name for trace in figure.data})
    threshold = next(trace for trace in figure.data if trace.name == "开仓涨幅门槛线")
    assert threshold.y[-1] == pytest.approx(132)


@pytest.mark.parametrize("column", ["cost_peak_points", "cost_zone_freeze_date", "cost_peak_window_days", "breakout_base_close"])
def test_old_states_cannot_silently_skip_the_cost_filter(tmp_path, column):
    states = _states(_history() + [170, 153]).drop(columns=column)
    with pytest.raises(ValueError, match="突破涨幅过滤"):
        _backtest(states)
    path = tmp_path / "old.csv"
    states.to_csv(path, index=False)
    with pytest.raises(ValueError, match="突破涨幅过滤"):
        read_saved_states(path)


def test_previous_rule_version_is_rejected(tmp_path):
    states = _states(_history() + [170, 153])
    states["breakout_baseline_rule"] = "LOOKBACK_INITIAL_HIGH_THEN_PREVIOUS_RISE_MEAN"
    with pytest.raises(ValueError, match="历史峰值成本区"):
        _backtest(states)
    path = tmp_path / "old.csv"
    states.to_csv(path, index=False)
    with pytest.raises(ValueError, match="历史峰值成本区"):
        read_saved_states(path)


@pytest.mark.parametrize("tamper", ["future_peak", "mean", "unfrozen", "gain", "window"])
def test_invalid_persisted_entry_evidence_is_rejected(tmp_path, tamper):
    states = _states(_history() + [170, 153])
    index = states.index[-1]
    if tamper == "future_peak":
        points = json.loads(states.loc[index, "cost_peak_points"])
        points[0]["confirm_date"] = states.loc[index, "trade_date"]
        states.loc[index, "cost_peak_points"] = json.dumps(points)
    elif tamper == "mean":
        states.loc[index, "breakout_base_close"] = 1
    elif tamper == "unfrozen":
        states.loc[index, "cost_zone_frozen"] = False
    elif tamper == "gain":
        states.loc[index, "breakout_gain_pct"] = 1
    else:
        states.loc[index, "cost_peak_window_days"] = 15
    with pytest.raises(ValueError, match="成本区"):
        _backtest(states)
    path = tmp_path / "invalid.csv"
    states.to_csv(path, index=False)
    with pytest.raises(ValueError, match="成本区"):
        read_saved_states(path)
