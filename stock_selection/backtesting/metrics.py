"""Backtest performance metrics."""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_performance_metrics(equity_curve: pd.DataFrame, value_col: str = "equity") -> dict[str, float]:
    """Compute basic performance metrics from an equity curve."""

    if equity_curve.empty or value_col not in equity_curve:
        return {}
    equity = equity_curve[value_col].astype(float)
    returns = equity.pct_change().dropna()
    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1.0) if len(equity) > 1 else 0.0
    annual_return = float((1 + total_return) ** (252 / max(len(returns), 1)) - 1)
    annual_volatility = float(returns.std() * np.sqrt(252)) if not returns.empty else 0.0
    sharpe = annual_return / annual_volatility if annual_volatility else 0.0
    running_max = equity.cummax()
    max_drawdown = float((equity / running_max - 1.0).min())
    calmar = annual_return / abs(max_drawdown) if max_drawdown else 0.0
    return {
        "total_return": total_return,
        "annual_return": annual_return,
        "annual_volatility": annual_volatility,
        "sharpe": float(sharpe),
        "max_drawdown": max_drawdown,
        "calmar": float(calmar),
    }

