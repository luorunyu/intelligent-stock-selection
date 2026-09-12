"""跨模块传递的结果容器，统一约束选股和回测的输出结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class SelectionResult:
    """选股策略的统一输出：入选结果、评分、信号、理由与元数据。"""

    selected: pd.DataFrame
    scores: pd.DataFrame = field(default_factory=pd.DataFrame)
    signals: pd.DataFrame = field(default_factory=pd.DataFrame)
    reasons: pd.DataFrame = field(default_factory=pd.DataFrame)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BacktestResult:
    """回测引擎的统一输出：净值、持仓、交易、指标和附加报告。"""

    equity_curve: pd.DataFrame
    positions: pd.DataFrame = field(default_factory=pd.DataFrame)
    trades: pd.DataFrame = field(default_factory=pd.DataFrame)
    metrics: dict[str, float] = field(default_factory=dict)
    reports: dict[str, pd.DataFrame] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
