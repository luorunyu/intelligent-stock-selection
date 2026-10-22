import pandas as pd

from stock_selection.strategies.stock_short_high_state.backtest import (
    HIGH_INVALIDATION_EXIT,
    MAX_DRAWDOWN_STOP,
    PROFIT_REBOUND_EXIT,
    build_short_trade_backtest,
)
from stock_selection.strategies.stock_short_high_state.screen import (
    calculate_stock_short_states,
)


def _backtest(closes):
    dates = pd.bdate_range("2026-01-05", periods=len(closes))
    daily = pd.DataFrame({
        "ts_code": "000001.SZ",
        "stock_name": "测试股票",
        "trade_date": dates.strftime("%Y%m%d"),
        "open": closes,
        "high": [close + 1 for close in closes],
        "close": closes,
        "vol": 1000,
    })
    states = calculate_stock_short_states(
        daily, lookback_days=3, high_heat_days=2,
    )
    result = build_short_trade_backtest(
        states, as_of_date=dates[-1].strftime("%Y%m%d"),
    )
    return states, result["trades"]


def test_profit_rebound_exit_requires_later_strict_new_high_before_reentry():
    states, trades = _backtest(
        [70, 80, 90, 100, 89, 81, 82, 83, 91, 89, 100, 89, 101, 90]
    )

    assert states.loc[[9, 11, 13], "entry_signal"].tolist() == [15, 15, 15]
    assert len(trades) == 2
    assert trades[0]["status_reason"] == PROFIT_REBOUND_EXIT
    assert trades[0]["exit_date"] == states.loc[7, "trade_date"]
    assert trades[1]["entry_date"] == states.loc[13, "trade_date"]
    assert trades[1]["status"] == "OPEN"


def test_drawdown_stop_requires_new_high_after_stop_day():
    states, trades = _backtest(
        [70, 80, 90, 100, 89, 116, 104, 116, 104, 117, 105]
    )

    assert states.loc[[6, 8, 10], "entry_signal"].tolist() == [15, 15, 15]
    assert len(trades) == 2
    assert trades[0]["status_reason"] == MAX_DRAWDOWN_STOP
    assert trades[0]["exit_date"] == states.loc[5, "trade_date"]
    assert trades[1]["entry_date"] == states.loc[10, "trade_date"]
    assert trades[1]["status"] == "OPEN"


def test_high_invalidation_exit_does_not_lock_next_candidate_pullback():
    states, trades = _backtest([70, 80, 90, 100, 89, 79, 101, 90])

    assert len(trades) == 2
    assert trades[0]["status_reason"] == HIGH_INVALIDATION_EXIT
    assert trades[1]["entry_date"] == states.loc[7, "trade_date"]
