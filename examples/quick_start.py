"""Quick start example with synthetic cyclical stocks."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stock_selection.backtesting import SignalBacktestEngine
from stock_selection.strategies import CyclePositionStrategy
from stock_selection.visualization import plot_stock_list


def make_sample_data() -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-01", periods=260)
    rows = []
    period = 40
    last_index = len(dates) - 1
    phases = {
        "CY_BOTTOM": -np.pi / 2 - 2 * np.pi * last_index / period,
        "CY_TOP": np.pi / 2 - 2 * np.pi * last_index / period,
        "CY_MIDDLE": -2 * np.pi * last_index / period,
    }

    for symbol, phase in phases.items():
        t = np.arange(len(dates))
        close = 20 + 4 * np.sin(2 * np.pi * t / period + phase)
        for i, date in enumerate(dates):
            rows.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "open": close[i] * 0.998,
                    "high": close[i] * 1.01,
                    "low": close[i] * 0.99,
                    "close": close[i],
                    "volume": 100_000,
                }
            )
    return pd.DataFrame(rows)


if __name__ == "__main__":
    price_df = make_sample_data()

    screening_strategy = CyclePositionStrategy(target_positions=None, periodicity_threshold=0.25)
    screening = screening_strategy.select(price_df, as_of_date="2025-12-31")
    print("Cyclical stocks and current cycle position:")
    print(screening.selected)

    buy_bottom_strategy = CyclePositionStrategy(periodicity_threshold=0.25)
    backtest = SignalBacktestEngine(
        buy_bottom_strategy,
        max_positions=2,
        stop_loss=0.08,
        take_profit=None,
        max_holding_days=90,
    ).run(
        price_df,
        symbols=["CY_BOTTOM", "CY_TOP", "CY_MIDDLE"],
        start_date="2025-04-01",
        end_date="2025-12-31",
    )
    print("Backtest metrics:")
    print(backtest.metrics)
    print("Overall report:")
    print(backtest.reports["overall"])
    print("Per-symbol report:")
    print(backtest.reports["per_symbol"])
    print("Trade records:")
    print(backtest.trades)
