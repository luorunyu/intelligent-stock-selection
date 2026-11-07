"""Industry gates must use same-day closes and never replay blocked signals."""

import json

import pandas as pd
import pytest

from stock_selection.strategies.stock_short_high_state import cli
from stock_selection.strategies.stock_short_high_state.backtest import build_short_trade_backtest, filter_short_trade_backtest_by_stock_pool
from stock_selection.strategies.stock_short_high_state.industry_state import (
    INDUSTRY_COLUMNS, IndustryConfig, attach_industry_states, calculate_industry_states,
    read_industry_inputs, validate_industry_fields,
)
from stock_selection.strategies.stock_short_high_state.screen import calculate_stock_short_states
from stock_selection.strategies.stock_short_high_state.visualization import (
    build_stock_short_state_figure, generate_stock_short_state_charts, merge_bars_and_states,
    read_saved_states,
)


def _bars(closes, code="801011.SI", dates=None):
    return pd.DataFrame({
        "ts_code": code, "trade_date": dates if dates is not None else pd.bdate_range("2026-01-05", periods=len(closes)).strftime("%Y%m%d"),
        "close": closes, "name": "测试行业", "high": 10000, "low": 1,
    })


def _stock(closes=None):
    closes = closes or [100, 80, 90, 150, 135, 130, 132, 133]
    daily = _bars(closes, code="000001.SZ")
    daily["stock_name"] = "测试股票"
    daily["open"] = closes
    daily["vol"] = 1000
    return daily, calculate_stock_short_states(daily, lookback_days=3, min_breakout_gain_pct=0)


def _attach(closes, stock=None, *, enabled=True):
    if stock is None:
        _, stock = _stock()
    prior = pd.bdate_range(end=pd.to_datetime(stock.trade_date.min()) - pd.Timedelta(days=1), periods=6).strftime("%Y%m%d")
    dates = [*prior, *stock.trade_date.tolist()]
    industry = calculate_industry_states(_bars([100] * 6 + closes, dates=dates))
    members = pd.DataFrame({"ts_code": ["000001.SZ"], "industry_code": ["801011.SI"], "industry_name": ["测试行业"]})
    return attach_industry_states(stock, industry, members, enabled=enabled)


@pytest.mark.parametrize(("closes", "expected"), [
    ([100] * 6 + [94], "STABLE_OR_RISING"),
    ([100] * 6 + [93.99], "DOWN"),
    ([100] * 6 + [106], "UP"),
    ([100] * 6 + [105.99], "STABLE_OR_RISING"),
    ([100, 95, 90, 85, 86, 87, 91], "UP"),
])
def test_close_only_threshold_boundaries_and_up_priority(closes, expected):
    result = calculate_industry_states(_bars(closes))
    assert result.iloc[-1].industry_state == expected
    assert result.iloc[-1].industry_observations == 7
    assert result.iloc[-1].industry_high_close == max(closes)
    assert result.iloc[-1].industry_low_close == min(closes)


def test_rolling_window_forgets_old_highs_and_does_not_keep_decline_forever():
    result = calculate_industry_states(_bars([100, 90, 90, 90, 90, 90, 90, 90]))
    assert result.iloc[6].industry_state == "DOWN"
    assert result.iloc[7].industry_state == "STABLE_OR_RISING"


@pytest.mark.parametrize("price", [0, float("nan"), float("inf"), -1])
def test_invalid_and_incomplete_industry_bars_cannot_be_classified(price):
    result = calculate_industry_states(_bars([100] * 6 + [price]))
    assert result.iloc[-1].industry_state == ""
    assert result.iloc[-1].industry_data_status == "INVALID_PRICE"
    assert result.iloc[5].industry_data_status == "INSUFFICIENT_HISTORY"


def test_future_bars_never_change_past_industry_states():
    bars = _bars([100, 95, 90, 85, 86, 87, 91, 70, 120])
    full = calculate_industry_states(bars)
    prefix = calculate_industry_states(bars.iloc[:7])
    pd.testing.assert_frame_equal(full.iloc[:7].reset_index(drop=True), prefix)


def test_industries_have_independent_windows():
    bars = pd.concat([_bars([100] * 6 + [90]), _bars([100] * 6 + [110], code="801012.SI")])
    latest = calculate_industry_states(bars).groupby("industry_code").tail(1).set_index("industry_code")
    assert latest.loc["801011.SI", "industry_state"] == "DOWN"
    assert latest.loc["801012.SI", "industry_state"] == "UP"


def test_declining_industry_allows_entry_and_later_rise_does_not_change_exit():
    _, raw = _stock()
    filtered = _attach([100, 100, 100, 100, 90, 91, 97, 98])
    baseline = build_short_trade_backtest(raw, as_of_date=raw.trade_date.max())
    result = build_short_trade_backtest(filtered, as_of_date=filtered.trade_date.max())
    trade, = result["trades"]
    assert trade["entry_industry_state"] == "DOWN"
    for column in ["entry_date", "evaluation_date", "status_reason", "mark_return_pct"]:
        assert trade[column] == baseline["trades"][0][column]
    assert filtered.iloc[-1].industry_state == "UP"


def test_blocked_signal_is_preserved_and_not_replayed_after_industry_declines():
    filtered = _attach([100, 100, 100, 100, 107, 90, 89, 88])
    original_signal = filtered[filtered.entry_signal.eq(15)].iloc[0]
    assert original_signal.industry_state == "UP"
    result = build_short_trade_backtest(filtered, as_of_date=filtered.trade_date.max())
    assert not result["trades"]
    blocked, = result["industry_blocked_entries"]
    assert blocked["entry_blocked_reason"] == "INDUSTRY_UP"
    assert result["summary"]["industry_blocked_signals"] == 1
    assert filtered.iloc[5].industry_state == "DOWN"


def test_a_new_stock_signal_can_open_after_an_earlier_signal_was_blocked():
    _, raw = _stock([100, 80, 90, 150, 135, 145, 130, 129])
    filtered = _attach([100, 100, 100, 100, 107, 100, 90, 89], stock=raw)
    result = build_short_trade_backtest(filtered, as_of_date=filtered.trade_date.max())
    trade, = result["trades"]
    assert trade["entry_date"] == filtered.iloc[6].trade_date
    assert len(result["industry_blocked_entries"]) == 1


@pytest.mark.parametrize(("problem", "reason"), [
    ("mapping", "MISSING_INDUSTRY"), ("ambiguous", "AMBIGUOUS_INDUSTRY"),
    ("date", "MISSING_INDUSTRY_DATE"), ("window", "INSUFFICIENT_HISTORY"),
])
def test_missing_or_ambiguous_industry_evidence_blocks_entry(problem, reason):
    _, stock = _stock()
    bars = _bars([100, 100, 100, 100, 90, 89, 88, 87], dates=stock.trade_date)
    industry = calculate_industry_states(bars)
    members = pd.DataFrame({"ts_code": ["000001.SZ"], "industry_code": ["801011.SI"], "industry_name": ["测试"]})
    if problem == "mapping":
        members = members.iloc[:0]
    elif problem == "ambiguous":
        members = pd.concat([members, members.assign(industry_code="801012.SI")])
    elif problem == "date":
        industry = industry[industry.industry_trade_date.ne(stock.iloc[4].trade_date)]
    filtered = attach_industry_states(stock, industry, members)
    result = build_short_trade_backtest(filtered, as_of_date=stock.trade_date.max())
    assert not result["trades"]
    assert result["industry_blocked_entries"][0]["entry_blocked_reason"] == reason


def test_saved_states_preserve_gate_and_chart_shows_industry_and_blocked_signal(tmp_path):
    daily, _ = _stock()
    filtered = _attach([100, 100, 100, 100, 107, 90, 89, 88])
    path = tmp_path / "states.csv"
    filtered.to_csv(path, index=False)
    loaded = read_saved_states(path)
    result = build_short_trade_backtest(loaded, as_of_date=loaded.trade_date.max())
    assert result["summary"]["industry_blocked_signals"] == 1
    chart = merge_bars_and_states(daily, loaded)
    figure = build_stock_short_state_figure(chart, stock_code="000001.SZ", trade_backtest=result)
    names = {trace.name for trace in figure.data}
    assert {"二级行业收盘价", "行业拦截开仓"} <= names
    assert "做空开仓" not in names
    assert figure.layout.yaxis3.title.text == "二级行业指数"
    loaded.drop(columns=INDUSTRY_COLUMNS).to_csv(path, index=False)
    with pytest.raises(ValueError, match="重新运行"):
        read_saved_states(path)


@pytest.mark.parametrize("change", ["qualified", "state", "date", "threshold", "rule"])
def test_saved_gate_tampering_cannot_bypass_filter(change):
    filtered = _attach([100, 100, 100, 100, 107, 90, 89, 88])
    index = filtered.index[4]
    column, value = {"qualified": ("entry_industry_qualified", True), "state": ("industry_state", "DOWN"),
                     "date": ("industry_trade_date", "20260101"), "threshold": ("industry_rise_threshold", 0.1),
                     "rule": ("industry_rule", "old")}[change]
    filtered.loc[index, column] = value
    with pytest.raises(ValueError):
        validate_industry_fields(filtered)


@pytest.mark.parametrize("kwargs", [{"lookback_days": 1}, {"lookback_days": 7.5}, {"rise_pct": 0}, {"drawdown_pct": float("nan")}])
def test_invalid_parameters_are_rejected(kwargs):
    with pytest.raises(ValueError):
        IndustryConfig(**kwargs)


def test_cli_defaults_disable_industry_filter(monkeypatch):
    monkeypatch.setattr("sys.argv", ["cli"])
    args = cli.parse_args()
    assert not args.industry_filter and args.industry_lookback_days == 7
    assert args.industry_drawdown_pct == args.industry_rise_pct == 0.06


def test_observation_only_keeps_industry_states_and_charts_without_blocking_entries(tmp_path):
    daily, raw = _stock()
    observed = _attach([100, 100, 100, 100, 107, 90, 89, 88], enabled=False)
    assert observed.iloc[4].industry_state == "UP"
    assert observed.entry_industry_qualified.all()
    assert observed.entry_blocked_reason.eq("").all()
    path = tmp_path / "states.csv"
    observed.to_csv(path, index=False)
    saved = read_saved_states(path)
    result = build_short_trade_backtest(saved, as_of_date=saved.trade_date.max())
    baseline = build_short_trade_backtest(raw, as_of_date=raw.trade_date.max())
    assert result["summary"] == baseline["summary"]
    assert result["industry_blocked_entries"] == []
    assert result["trades"][0]["entry_date"] == baseline["trades"][0]["entry_date"]
    assert result["trades"][0]["entry_industry_state"] == "UP"
    chart = merge_bars_and_states(daily, saved)
    figure = build_stock_short_state_figure(chart, stock_code="000001.SZ", trade_backtest=result)
    names = {trace.name for trace in figure.data}
    assert "二级行业收盘价" in names
    assert "行业拦截开仓" not in names
