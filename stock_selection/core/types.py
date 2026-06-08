"""Shared result containers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class SelectionResult:
    """Unified output returned by strategies."""

    selected: pd.DataFrame
    scores: pd.DataFrame = field(default_factory=pd.DataFrame)
    signals: pd.DataFrame = field(default_factory=pd.DataFrame)
    reasons: pd.DataFrame = field(default_factory=pd.DataFrame)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BacktestResult:
    """Unified output returned by the backtesting engine."""

    equity_curve: pd.DataFrame
    positions: pd.DataFrame = field(default_factory=pd.DataFrame)
    trades: pd.DataFrame = field(default_factory=pd.DataFrame)
    metrics: dict[str, float] = field(default_factory=dict)
    reports: dict[str, pd.DataFrame] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
