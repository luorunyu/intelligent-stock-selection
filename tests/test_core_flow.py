from __future__ import annotations

import numpy as np
import pandas as pd

from stock_selection.backtesting import run_parameter_grid, run_train_validation_test, run_walk_forward
from stock_selection.backtesting.engine import SignalBacktestEngine
from stock_selection.methods.periodicity import analyze_all_cycles
from stock_selection.strategies.cycle_position import CyclePositionStrategy


def sample_data() -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-01", periods=260)
    rows = []
    period = 40
    last_index = len(dates) - 1
    specs = {
        "CY_BOTTOM": -np.pi / 2 - 2 * np.pi * last_index / period,
        "CY_TOP": np.pi / 2 - 2 * np.pi * last_index / period,
        "TREND": 0.0,
    }

    for symbol, phase in specs.items():
        t = np.arange(len(dates))
        if symbol == "TREND":
            close = 20 + t * 0.03
        else:
            close = 20 + 4 * np.sin(2 * np.pi * t / period + phase)
        open_ = close * 0.998
        high = close * 1.01
        low = close * 0.99
        volume = np.full(len(dates), 100_000)
        for i, date in enumerate(dates):
            rows.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "open": open_[i],
                    "high": high[i],
                    "low": low[i],
                    "close": close[i],
                    "volume": volume[i],
                }
            )
    return pd.DataFrame(rows)


def confirmed_entry_data() -> pd.DataFrame:
    data = sample_data()
    last_date = data["date"].max()
    extra_dates = pd.bdate_range(last_date + pd.offsets.BDay(1), periods=4)
    closes = [16.05, 16.20, 16.50, 17.00]
    volumes = [110_000, 120_000, 130_000, 160_000]
    rows = []
    for date, close, volume in zip(extra_dates, closes, volumes):
        rows.append(
            {
                "date": date,
                "symbol": "CY_BOTTOM",
                "open": close * 0.998,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": volume,
            }
        )
    return pd.concat([data, pd.DataFrame(rows)], ignore_index=True)


def test_cycle_analysis_classifies_positions():
    result = analyze_all_cycles(sample_data(), periodicity_threshold=0.25)
    by_symbol = result.set_index("symbol")

    assert bool(by_symbol.loc["CY_BOTTOM", "periodic"])
    assert by_symbol.loc["CY_BOTTOM", "cycle_position"] == "bottom"
    assert bool(by_symbol.loc["CY_TOP", "periodic"])
    assert by_symbol.loc["CY_TOP", "cycle_position"] == "top"


def test_cycle_strategy_can_select_bottoms_only():
    strategy = CyclePositionStrategy(target_positions=("bottom",), periodicity_threshold=0.25)
    result = strategy.select(sample_data())

    assert result.selected["symbol"].tolist() == ["CY_BOTTOM"]
    assert result.selected.iloc[0]["cycle_position"] == "bottom"
    assert not result.signals.empty
    assert not result.reasons.empty


def test_cycle_strategy_generates_trade_signals():
    strategy = CyclePositionStrategy(periodicity_threshold=0.25)
    data = sample_data()

    buy_signals = strategy.generate_signals(data, as_of_date=data["date"].max(), symbols=["CY_BOTTOM"])
    assert buy_signals.iloc[0]["action"] == "hold"
    assert buy_signals.iloc[0]["reason"] == "entry_price_not_confirmed"

    confirmed_data = confirmed_entry_data()
    confirmed_buy_signals = strategy.generate_signals(
        confirmed_data,
        as_of_date=confirmed_data["date"].max(),
        symbols=["CY_BOTTOM"],
    )
    assert confirmed_buy_signals.iloc[0]["action"] == "buy"
    assert confirmed_buy_signals.iloc[0]["reason"] == "cycle_bottom_price_volume_confirmed"

    sell_signals = strategy.generate_signals(
        data,
        as_of_date=data["date"].max(),
        symbols=["CY_TOP"],
        current_positions={"CY_TOP"},
    )
    assert sell_signals.iloc[0]["action"] == "sell"


def test_backtest_runs_signal_strategy_through_history():
    strategy = CyclePositionStrategy(periodicity_threshold=0.25)
    result = SignalBacktestEngine(strategy, max_positions=2, stop_loss=0.08, max_holding_days=90).run(
        sample_data(),
        symbols=["CY_BOTTOM", "CY_TOP", "TREND"],
        start_date="2025-04-01",
    )

    assert not result.equity_curve.empty
    assert not result.trades.empty
    assert set(result.trades["action"]).issubset({"buy", "sell"})
    assert "total_return" in result.metrics
    assert "max_drawdown" in result.metrics
    assert "trade_count" in result.metrics
    assert "overall" in result.reports
    assert "per_symbol" in result.reports
    assert "max_forward_return" in result.reports["per_symbol"]
    assert "max_drawdown" in result.reports["per_symbol"]
    assert "avg_holding_days" in result.reports["per_symbol"]


def test_parameter_grid_runs_all_combinations():
    results, runs = run_parameter_grid(
        sample_data(),
        symbols=["CY_BOTTOM", "CY_TOP", "TREND"],
        strategy_cls=CyclePositionStrategy,
        param_grid={
            "periodicity_threshold": [0.20, 0.25],
            "top_n": [None, 2],
        },
        start_date="2025-04-01",
        engine_kwargs={"max_positions": 2, "stop_loss": 0.08, "max_holding_days": 90},
    )

    assert len(results) == 4
    assert len(runs) == 4
    assert "total_return" in results
    assert "max_drawdown" in results
    assert set(results["periodicity_threshold"]) == {0.20, 0.25}


def test_train_validation_test_selects_train_best_and_reuses_it():
    result = run_train_validation_test(
        sample_data(),
        symbols=["CY_BOTTOM", "CY_TOP", "TREND"],
        strategy_cls=CyclePositionStrategy,
        param_grid={"periodicity_threshold": [0.20, 0.25]},
        train_period=("2025-04-01", "2025-06-30"),
        validation_period=("2025-07-01", "2025-09-30"),
        test_period=("2025-10-01", "2025-12-31"),
        engine_kwargs={"max_positions": 2, "stop_loss": 0.08, "max_holding_days": 90},
    )

    assert result.best_params["periodicity_threshold"] in {0.20, 0.25}
    assert result.summary["split"].tolist() == ["train", "validation", "test"]
    assert not result.train_results.empty
    assert "total_return" in result.summary


def test_walk_forward_runs_rolling_train_test_folds():
    result = run_walk_forward(
        sample_data(),
        symbols=["CY_BOTTOM", "CY_TOP", "TREND"],
        strategy_cls=CyclePositionStrategy,
        param_grid={"periodicity_threshold": [0.20, 0.25]},
        train_periods=80,
        test_periods=40,
        start_date="2025-01-01",
        engine_kwargs={"max_positions": 2, "stop_loss": 0.08, "max_holding_days": 90},
    )

    assert len(result.summary) >= 1
    assert not result.train_results.empty
    assert "fold" in result.summary
    assert "test_start" in result.summary
    assert result.summary["periodicity_threshold"].isin([0.20, 0.25]).all()
    
    #一句测试
