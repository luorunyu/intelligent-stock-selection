"""Signal-driven stock backtesting engine."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from stock_selection.backtesting.metrics import compute_performance_metrics
from stock_selection.backtesting.report import build_backtest_reports
from stock_selection.core.schema import CLOSE_COL, DATE_COL, SYMBOL_COL
from stock_selection.core.types import BacktestResult
from stock_selection.core.validation import normalize_price_frame


@dataclass
class OpenPosition:
    symbol: str
    entry_date: pd.Timestamp
    entry_price: float
    shares: float


class SignalBacktestEngine:
    """Backtest strategies that emit buy/sell/hold signals."""

    def __init__(
        self,
        strategy,
        initial_cash: float = 1_000_000.0,
        max_positions: int = 5,
        transaction_cost: float = 0.001,
        stop_loss: float | None = 0.08,
        take_profit: float | None = None,
        max_holding_days: int | None = None,
    ) -> None:
        self.strategy = strategy
        self.initial_cash = initial_cash
        self.max_positions = max_positions
        self.transaction_cost = transaction_cost
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.max_holding_days = max_holding_days

    def run(
        self,
        price_df: pd.DataFrame,
        symbols: list[str],
        start_date: str | pd.Timestamp,
        end_date: str | pd.Timestamp | None = None,
        **strategy_kwargs,
    ) -> BacktestResult:
        """Run a backtest on a stock pool and date range.

        The strategy is called once per trading day with ``as_of_date=date``.
        It can only make decisions from data available up to that date.
        Trades are executed at that day's close.
        """

        data = normalize_price_frame(price_df)
        symbols = [str(symbol) for symbol in symbols]
        end = pd.Timestamp(end_date) if end_date is not None else data[DATE_COL].max()
        start = pd.Timestamp(start_date)

        trade_data = data[
            (data[SYMBOL_COL].isin(symbols))
            & (data[DATE_COL] >= start)
            & (data[DATE_COL] <= end)
        ]
        if trade_data.empty:
            return BacktestResult(equity_curve=pd.DataFrame(), metadata=self._metadata(symbols, start, end))

        close_matrix = trade_data.pivot(index=DATE_COL, columns=SYMBOL_COL, values=CLOSE_COL).sort_index()
        cash = float(self.initial_cash)
        positions: dict[str, OpenPosition] = {}
        equity_rows: list[dict[str, object]] = []
        position_rows: list[dict[str, object]] = []
        trade_rows: list[dict[str, object]] = []

        for date in close_matrix.index:
            prices = close_matrix.loc[date].dropna().astype(float).to_dict()

            signals = self.strategy.generate_signals(
                data,
                as_of_date=date,
                symbols=symbols,
                current_positions=set(positions),
                **strategy_kwargs,
            )
            signal_by_symbol = signals.set_index("symbol").to_dict("index") if not signals.empty else {}

            for symbol in list(positions):
                if symbol not in prices:
                    continue
                exit_reason = self._exit_reason(date, positions[symbol], prices[symbol])
                signal = signal_by_symbol.get(symbol, {})
                if signal.get("action") == "sell":
                    exit_reason = signal.get("reason", "sell_signal")
                if exit_reason:
                    cash += self._sell(date, positions.pop(symbol), prices[symbol], exit_reason, trade_rows)

            buy_signals = signals[signals["action"] == "buy"] if not signals.empty else pd.DataFrame()
            for signal in buy_signals.sort_values("periodicity_score", ascending=False).itertuples(index=False):
                if len(positions) >= self.max_positions:
                    break
                symbol = str(signal.symbol)
                if symbol in positions or symbol not in prices:
                    continue
                available_slots = max(self.max_positions - len(positions), 1)
                budget = cash / available_slots
                if budget <= 0:
                    break
                position, cash_used = self._buy(date, symbol, prices[symbol], budget, signal.reason, trade_rows)
                if position is not None:
                    positions[symbol] = position
                    cash -= cash_used

            equity_after_trades = self._portfolio_value(cash, positions, prices)
            equity_rows.append(
                {
                    "date": date,
                    "equity": equity_after_trades,
                    "cash": cash,
                    "market_value": equity_after_trades - cash,
                    "position_count": len(positions),
                }
            )
            for position in positions.values():
                price = prices.get(position.symbol)
                if price is None:
                    continue
                position_rows.append(
                    {
                        "date": date,
                        "symbol": position.symbol,
                        "entry_date": position.entry_date,
                        "entry_price": position.entry_price,
                        "close": price,
                        "shares": position.shares,
                        "unrealized_return": price / position.entry_price - 1.0,
                    }
                )

        equity_curve = pd.DataFrame(equity_rows)
        trades = pd.DataFrame(trade_rows)
        metrics = compute_performance_metrics(equity_curve)
        metrics.update(_trade_metrics(trades))
        reports = build_backtest_reports(trade_data, symbols, equity_curve, trades, metrics)
        return BacktestResult(
            equity_curve=equity_curve,
            positions=pd.DataFrame(position_rows),
            trades=trades,
            metrics=metrics,
            reports=reports,
            metadata=self._metadata(symbols, start, end),
        )

    def _buy(
        self,
        date: pd.Timestamp,
        symbol: str,
        price: float,
        budget: float,
        reason: str,
        trade_rows: list[dict[str, object]],
    ) -> tuple[OpenPosition | None, float]:
        total_cost_rate = 1.0 + self.transaction_cost
        shares = budget / (price * total_cost_rate)
        if shares <= 0:
            return None, 0.0
        trade_value = shares * price
        cost = trade_value * self.transaction_cost
        trade_rows.append(
            {
                "date": date,
                "symbol": symbol,
                "action": "buy",
                "price": price,
                "shares": shares,
                "trade_value": trade_value,
                "cost": cost,
                "reason": reason,
                "entry_date": date,
                "entry_price": price,
            }
        )
        return OpenPosition(symbol=symbol, entry_date=date, entry_price=price, shares=shares), trade_value + cost

    def _sell(
        self,
        date: pd.Timestamp,
        position: OpenPosition,
        price: float,
        reason: str,
        trade_rows: list[dict[str, object]],
    ) -> float:
        trade_value = position.shares * price
        cost = trade_value * self.transaction_cost
        realized_return = price / position.entry_price - 1.0
        trade_rows.append(
            {
                "date": date,
                "symbol": position.symbol,
                "action": "sell",
                "price": price,
                "shares": position.shares,
                "trade_value": trade_value,
                "cost": cost,
                "reason": reason,
                "entry_date": position.entry_date,
                "entry_price": position.entry_price,
                "exit_date": date,
                "exit_price": price,
                "holding_days": int((date - position.entry_date).days),
                "realized_return": realized_return,
            }
        )
        return trade_value - cost

    def _exit_reason(self, date: pd.Timestamp, position: OpenPosition, price: float) -> str | None:
        current_return = price / position.entry_price - 1.0
        if self.stop_loss is not None and current_return <= -self.stop_loss:
            return "stop_loss"
        if self.take_profit is not None and current_return >= self.take_profit:
            return "take_profit"
        if self.max_holding_days is not None and (date - position.entry_date).days >= self.max_holding_days:
            return "max_holding_days"
        return None

    def _portfolio_value(
        self,
        cash: float,
        positions: dict[str, OpenPosition],
        prices: dict[str, float],
    ) -> float:
        value = cash
        for symbol, position in positions.items():
            price = prices.get(symbol, position.entry_price)
            value += position.shares * price
        return float(value)

    def _metadata(self, symbols: list[str], start: pd.Timestamp, end: pd.Timestamp) -> dict[str, object]:
        return {
            "strategy_name": getattr(self.strategy, "name", self.strategy.__class__.__name__),
            "symbols": symbols,
            "start_date": str(start.date()),
            "end_date": str(end.date()),
            "initial_cash": self.initial_cash,
            "max_positions": self.max_positions,
            "transaction_cost": self.transaction_cost,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "max_holding_days": self.max_holding_days,
        }


def _trade_metrics(trades: pd.DataFrame) -> dict[str, float]:
    if trades.empty or "action" not in trades:
        return {"trade_count": 0.0, "closed_trade_count": 0.0, "win_rate": 0.0}
    sells = trades[trades["action"] == "sell"]
    if sells.empty or "realized_return" not in sells:
        return {"trade_count": float(len(trades)), "closed_trade_count": 0.0, "win_rate": 0.0}
    returns = sells["realized_return"].astype(float)
    return {
        "trade_count": float(len(trades)),
        "closed_trade_count": float(len(sells)),
        "win_rate": float((returns > 0).mean()),
        "avg_trade_return": float(returns.mean()),
        "best_trade_return": float(returns.max()),
        "worst_trade_return": float(returns.min()),
    }


EqualWeightBacktestEngine = SignalBacktestEngine
