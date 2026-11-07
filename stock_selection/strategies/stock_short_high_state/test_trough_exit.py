"""Regression checks for exits on observable trough confirmation signals."""

import json

import pandas as pd
import pytest

from stock_selection.strategies.stock_short_high_state.backtest import (
    HIGH_INVALIDATION_EXIT,
    MAX_DRAWDOWN_STOP,
    PROFIT_REBOUND_EXIT,
    TROUGH_CONFIRM_EXIT,
    ShortTradeBacktestConfig,
    build_short_trade_backtest,
    split_short_trade_backtest_by_average_market_cap,
)
from stock_selection.strategies.stock_short_high_state.report import (
    render_short_trade_backtest_html,
    render_short_trade_backtest_markdown,
)
from stock_selection.strategies.stock_short_high_state.screen import (
    StockShortState,
    calculate_stock_short_states,
)


def _states(closes=None, *, lookback_days=3, volumes=1000):
    if closes is None:
        # Cross MA5 only on the final day; the intervening decline prevents
        # the independent three-consecutive-up-days exit.
        closes = [70, 80, 90, 100, 79, 79.5, 80, 79.8, 80.2]
    return calculate_stock_short_states(
        pd.DataFrame({
            "ts_code": "000001.SZ",
            "stock_name": "测试股票",
            "trade_date": pd.bdate_range("2026-01-05", periods=len(closes)).strftime("%Y%m%d"),
            "open": closes,
            "high": [close + 1 for close in closes],
            "close": closes,
            "vol": volumes,
        }),
        lookback_days=lookback_days,
        # Exercise exits even when this short fixture has no 50% breakout.
        min_breakout_gain_pct=0,
        high_heat_days=2,
        trough_ma_days=5,
    )


def _backtest(states, **kwargs):
    return build_short_trade_backtest(
        states, as_of_date=states["trade_date"].iloc[-1] if not states.empty else "20260115", **kwargs,
    )


@pytest.mark.parametrize("history", ["flat_volume", "missing_volume", "short_history", "no_volume_column"])
def test_confirmed_trough_exits_without_volume_reference(history):
    states = _states(volumes=float("nan") if history == "missing_volume" else 1000)
    if history == "short_history":
        states = states.iloc[3:].copy()
        states["lookback_days"] = 120
    elif history == "no_volume_column":
        states = states.drop(columns=["vol", "lookback_days", "reference_high_date"])

    result = _backtest(states)
    trade, = result["trades"]

    assert StockShortState.PEAK_1 not in states["observed_state"].values
    assert trade["status"] == "COMPLETED"
    assert trade["status_reason"] == TROUGH_CONFIRM_EXIT
    assert trade["exit_state"] == "TROUGH_REGION"
    assert trade["exit_date"] == states["trade_date"].iloc[-1]
    assert trade["exit_close"] == 80.2
    assert trade["return_pct"] == pytest.approx(1 - 80.2 / 79)
    assert result["summary"]["trough_exit_trades"] == 1


def test_large_distance_from_former_volume_price_does_not_block_exit():
    closes = [20, 22, 23, 24, 25, 30, 50, 100, 79, 79.5, 80, 79.8, 80.2]
    volumes = [1000] * len(closes)
    # The former last qualifying volume triple ended at close 50, so the
    # MA confirmation close 79.5 would previously fail the 30% distance filter.
    volumes[4:7] = [100, 200, 300]
    states = _states(closes, lookback_days=5, volumes=volumes)

    trade, = _backtest(states)["trades"]

    assert abs(79.5 / 50 - 1) > 0.30
    assert trade["status_reason"] == TROUGH_CONFIRM_EXIT
    assert trade["exit_date"] == states.iloc[9]["trade_date"]
    assert trade["exit_close"] == 79.5


def test_backfilled_trough_does_not_exit_before_actual_confirmation():
    states = _states()
    assert states.iloc[4]["state"] == StockShortState.TROUGH_REGION
    assert states.iloc[4]["observed_state"] == StockShortState.TROUGH_CANDIDATE

    result = build_short_trade_backtest(states, as_of_date=states.iloc[-2]["trade_date"])
    trade, = result["trades"]

    assert trade["status"] == "OPEN"
    assert trade["exit_date"] is None
    assert trade["evaluation_trough_confirm_date"] is None
    assert result["summary"]["trough_exit_trades"] == 0


def test_return_to_old_trough_region_does_not_trigger_another_exit():
    states = _states([70, 80, 90, 100, 79, 79.5, 80, 79.8, 80.2, 91, 89, 80.1, 80, 80.2])
    # Start the input after its original confirmation and enable peak entries
    # for this scenario, so the position reaches an old, confirmed region.
    states = states.iloc[9:].copy()
    result = _backtest(states, config=ShortTradeBacktestConfig(
        peak_entry_states=frozenset({StockShortState.PEAK_1}),
    ))
    trade, = result["trades"]

    assert states.iloc[-1]["observed_state"] == StockShortState.TROUGH_REGION
    assert states.iloc[-1]["trough_confirm_date"] < states.iloc[0]["trade_date"]
    assert trade["status"] == "OPEN"
    assert trade["exit_date"] is None


@pytest.mark.parametrize(
    ("max_drawdown", "reason"),
    [(0.20, MAX_DRAWDOWN_STOP), (0.50, HIGH_INVALIDATION_EXIT)],
)
def test_stop_and_high_invalidation_exits_still_apply(max_drawdown, reason):
    states = _states([70, 80, 90, 100, 89, 79, 101])
    trade, = _backtest(states, config=ShortTradeBacktestConfig(
        max_drawdown_pct=max_drawdown,
    ))["trades"]

    assert trade["status_reason"] == reason
    assert trade["exit_date"] == states.iloc[-1]["trade_date"]


def test_profit_rebound_keeps_priority_when_trough_confirms_on_same_day():
    states = _states([70, 80, 90, 100, 89, 70, 70, 71, 72, 73])
    trade, = _backtest(states)["trades"]

    assert states.iloc[-1]["observed_state"] == StockShortState.TROUGH_REGION
    assert states.iloc[-1]["trough_confirm_date"] == states.iloc[-1]["trade_date"]
    assert trade["status_reason"] == PROFIT_REBOUND_EXIT
    assert trade["exit_close"] == 73
    assert trade["evaluation_consecutive_up_days"] == 3


@pytest.mark.parametrize("empty", [False, True])
def test_reports_remove_price_reference_and_keep_exit_diagnostics(empty):
    states = _states()
    result = _backtest(states.iloc[:0] if empty else states)
    reports = [result, *split_short_trade_backtest_by_average_market_cap(result).values()]

    for backtest in reports:
        payload = json.dumps(backtest, ensure_ascii=False)
        for old_field in ("price_origin", "origin_high", "origin_window", "volume_expansion", "volume_first", "first_peak"):
            assert old_field not in payload
        for text in (
            render_short_trade_backtest_markdown(backtest),
            render_short_trade_backtest_html(backtest),
        ):
            for old_label in ("参考价", "行情价格起点", "放量", "绝对偏离", "平仓参考核对"):
                assert old_label not in text
            assert "开平仓核对" in text
            assert "波谷确认日" in text
            assert f"MA{backtest['metadata']['normal_exit_rule']['trough_ma_days']}" in text
            assert "波谷观察上涨天数" not in text
    if not empty:
        assert "TROUGH_CONFIRM_EXIT" in render_short_trade_backtest_html(result)
        assert result["trades"][0]["entry_reference_high_close"] == 100


def test_chart_uses_same_confirmation_exit_without_price_reference():
    pytest.importorskip("plotly")
    from stock_selection.strategies.stock_short_high_state.visualization import (
        build_stock_short_state_figure,
        merge_bars_and_states,
    )

    states = _states()
    bars = states[["ts_code", "trade_date", "open", "high", "close", "vol"]].copy()
    bars["low"] = bars["close"] - 1
    chart_data = merge_bars_and_states(bars, states)
    figure = build_stock_short_state_figure(chart_data, stock_code="000001.SZ")
    payload = figure.to_json()

    assert "TROUGH_CONFIRM_EXIT" in payload
    ma_line, = [trace for trace in figure.data if trace.name == "MA5"]
    assert ma_line.y[-1] == pytest.approx(79.7)
    for old_label in ("参考价", "行情价格起点", "三天放量", "参考状态"):
        assert old_label not in payload
    price_intervals = [shape for shape in figure.layout.shapes if shape.xref == "x"]
    assert len(price_intervals) == 1
    assert pd.Timestamp(price_intervals[0].x1).strftime("%Y%m%d") == states.iloc[-1]["trade_date"]
