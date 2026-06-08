"""Strategy base interfaces."""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from stock_selection.core.types import SelectionResult


class BaseStrategy(ABC):
    """Base class for all strategies."""

    name = "base_strategy"

    @abstractmethod
    def select(self, df: pd.DataFrame, as_of_date: str | pd.Timestamp | None = None) -> SelectionResult:
        """Select stocks as of a date."""

