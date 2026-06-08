"""Cycle-position stock selection strategy."""

from __future__ import annotations

import pandas as pd

from stock_selection.core.schema import CLOSE_COL, DATE_COL, SYMBOL_COL, VOLUME_COL
from stock_selection.core.types import SelectionResult
from stock_selection.core.validation import filter_as_of, normalize_price_frame
from stock_selection.methods.periodicity import analyze_all_cycles
from stock_selection.strategies.base import BaseStrategy


class CyclePositionStrategy(BaseStrategy):
    """Select cyclical stocks and report whether they are bottom/middle/top."""

    name = "cycle_position_strategy"

    def __init__(
        self,
        target_positions: tuple[str, ...] | None = None,
        min_period: int = 20,
        max_period: int = 120,
        window: int = 252,
        periodicity_threshold: float = 0.30,
        top_n: int | None = None,
        entry_confirmation: bool = True,
        entry_ma_window: int = 5,
        entry_low_window: int = 3,
        entry_volume_window: int = 5,
        entry_volume_ratio: float = 1.0,
    ) -> None:
        self.target_positions = target_positions
        self.min_period = min_period
        self.max_period = max_period
        self.window = window
        self.periodicity_threshold = periodicity_threshold
        self.top_n = top_n
        self.entry_confirmation = entry_confirmation
        self.entry_ma_window = entry_ma_window
        self.entry_low_window = entry_low_window
        self.entry_volume_window = entry_volume_window
        self.entry_volume_ratio = entry_volume_ratio

    def select(
        self,
        df: pd.DataFrame,
        as_of_date: str | pd.Timestamp | None = None,
        symbols: list[str] | None = None,
    ) -> SelectionResult:
        signals = analyze_all_cycles(
            df,
            symbols=symbols,
            as_of_date=as_of_date,
            min_period=self.min_period,
            max_period=self.max_period,
            window=self.window,
            periodicity_threshold=self.periodicity_threshold,
        )

        candidates = signals[signals["periodic"]].copy()
        if self.target_positions is not None:
            candidates = candidates[candidates["cycle_position"].isin(self.target_positions)]

        candidates = candidates.sort_values(
            ["periodicity_score", "price_position"],
            ascending=[False, True],
        ).reset_index(drop=True)
        if self.top_n is not None:
            candidates = candidates.head(self.top_n)
        candidates["rank"] = range(1, len(candidates) + 1)
        candidates["score"] = (candidates["periodicity_score"].fillna(0.0) * 100).round(2)

        selected_cols = [
            "symbol",
            "rank",
            "score",
            "cycle_position",
            "dominant_period",
            "price_position",
            "current_price",
        ]
        selected = candidates[selected_cols].reset_index(drop=True) if not candidates.empty else pd.DataFrame(columns=selected_cols)
        reasons = _build_reasons(candidates, self.name)
        return SelectionResult(
            selected=selected,
            scores=selected[["symbol", "rank", "score"]] if not selected.empty else pd.DataFrame(columns=["symbol", "rank", "score"]),
            signals=signals,
            reasons=reasons,
            metadata={
                "strategy_name": self.name,
                "as_of_date": str(as_of_date) if as_of_date is not None else None,
                "target_positions": self.target_positions,
                "window": self.window,
                "periodicity_threshold": self.periodicity_threshold,
                "entry_confirmation": self.entry_confirmation,
                "entry_ma_window": self.entry_ma_window,
                "entry_low_window": self.entry_low_window,
                "entry_volume_window": self.entry_volume_window,
                "entry_volume_ratio": self.entry_volume_ratio,
            },
        )

    def generate_signals(
        self,
        df: pd.DataFrame,
        as_of_date: str | pd.Timestamp,
        symbols: list[str] | None = None,
        current_positions: set[str] | None = None,
    ) -> pd.DataFrame:
        """Return buy/sell/hold signals as of one historical date.

        Buy rule: cyclical stock at bottom.
        Sell rule: held cyclical stock reaches top, or it is no longer periodic.
        Everything else is hold.
        """

        current_positions = current_positions or set()
        analysis = analyze_all_cycles(
            df,
            symbols=symbols,
            as_of_date=as_of_date,
            min_period=self.min_period,
            max_period=self.max_period,
            window=self.window,
            periodicity_threshold=self.periodicity_threshold,
        )
        rows = []
        for row in analysis.itertuples(index=False):
            symbol = str(row.symbol)
            if symbol in current_positions:
                action, reason = self._exit_signal(row)
            else:
                action, reason = self._entry_signal(row, df, symbol, as_of_date)
            rows.append(self._build_signal_row(row, symbol, action, reason))
        return pd.DataFrame(rows)

    def _entry_signal(
        self,
        row,
        df: pd.DataFrame,
        symbol: str,
        as_of_date: str | pd.Timestamp,
    ) -> tuple[str, str]:
        """Return the buy/hold decision for a stock that is not currently held."""

        if not bool(row.periodic):
            return "hold", "not_periodic"
        if row.cycle_position != "bottom":
            return "hold", "not_cycle_bottom"
        if self.entry_confirmation:
            confirmed, reason = self._entry_confirmed(df, symbol, as_of_date)
            if not confirmed:
                return "hold", reason
            return "buy", "cycle_bottom_price_volume_confirmed"
        return "buy", "cycle_bottom"

    def _exit_signal(self, row) -> tuple[str, str]:
        """Return the sell/hold decision for a currently held stock."""

        if row.cycle_position == "top":
            return "sell", "cycle_top"
        if not bool(row.periodic):
            return "sell", "no_longer_periodic"
        return "hold", "no_action"

    def _build_signal_row(self, row, symbol: str, action: str, reason: str) -> dict[str, object]:
        return {
            "date": pd.Timestamp(row.as_of_date),
            "symbol": symbol,
            "action": action,
            "reason": reason,
            "cycle_position": row.cycle_position,
            "periodic": bool(row.periodic),
            "dominant_period": row.dominant_period,
            "periodicity_score": row.periodicity_score,
            "price_position": row.price_position,
            "current_price": row.current_price,
        }

    def _entry_confirmed(
        self,
        df: pd.DataFrame,
        symbol: str,
        as_of_date: str | pd.Timestamp,
    ) -> tuple[bool, str]:
        data = filter_as_of(normalize_price_frame(df), as_of_date)
        history = data[data[SYMBOL_COL].astype(str) == str(symbol)].sort_values(DATE_COL)
        required_history = max(self.entry_ma_window + 1, self.entry_low_window + 1, self.entry_volume_window)
        if len(history) < required_history:
            return False, "insufficient_entry_history"

        close = history[CLOSE_COL].astype(float)
        volume = history[VOLUME_COL].astype(float)
        current_close = float(close.iloc[-1])
        current_volume = float(volume.iloc[-1])

        current_ma = float(close.tail(self.entry_ma_window).mean())
        previous_ma = float(close.iloc[-self.entry_ma_window - 1 : -1].mean())
        if current_close <= current_ma or current_ma < previous_ma:
            return False, "entry_price_not_confirmed"

        previous_low = float(close.iloc[-self.entry_low_window - 1 : -1].min())
        if current_close <= previous_low:
            return False, "entry_new_low"

        average_volume = float(volume.tail(self.entry_volume_window).mean())
        if average_volume <= 0 or current_volume < average_volume * self.entry_volume_ratio:
            return False, "entry_volume_not_confirmed"

        return True, "entry_confirmed"


def _build_reasons(candidates: pd.DataFrame, strategy_name: str) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame(columns=["symbol", "rank", "score", "strategy_name", "reason"])
    reasons = candidates[["symbol", "rank", "score", "cycle_position", "dominant_period", "price_position"]].copy()
    reasons["strategy_name"] = strategy_name
    reasons["reason"] = reasons.apply(
        lambda row: (
            f"periodic stock, dominant period {row['dominant_period']:.1f} trading days, "
            f"currently at cycle {row['cycle_position']}"
        ),
        axis=1,
    )
    return reasons.reset_index(drop=True)
