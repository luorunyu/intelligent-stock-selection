"""Backtest report builders."""

from __future__ import annotations

import pandas as pd

from stock_selection.core.schema import CLOSE_COL, DATE_COL, SYMBOL_COL


def build_backtest_reports(
    price_df: pd.DataFrame,
    symbols: list[str],
    equity_curve: pd.DataFrame,
    trades: pd.DataFrame,
    metrics: dict[str, float],
) -> dict[str, pd.DataFrame]:
    """Build overall and per-symbol reports for one backtest."""

    per_symbol = build_symbol_report(price_df, symbols, trades)
    overall = build_overall_report(symbols, equity_curve, trades, metrics, per_symbol)
    return {"overall": overall, "per_symbol": per_symbol}


def build_symbol_report(
    price_df: pd.DataFrame,
    symbols: list[str],
    trades: pd.DataFrame,
) -> pd.DataFrame:
    """Summarize price path and trade result for each stock."""

    rows = []
    for symbol in symbols:
        sdf = price_df[price_df[SYMBOL_COL] == symbol].sort_values(DATE_COL)
        stock_trades = trades[trades["symbol"] == symbol] if not trades.empty else pd.DataFrame()
        closed = stock_trades[stock_trades["action"] == "sell"] if not stock_trades.empty else pd.DataFrame()
        close = sdf[CLOSE_COL].astype(float)

        rows.append(
            {
                "symbol": symbol,
                "start_date": sdf[DATE_COL].iloc[0] if not sdf.empty else pd.NaT,
                "end_date": sdf[DATE_COL].iloc[-1] if not sdf.empty else pd.NaT,
                "start_price": float(close.iloc[0]) if not close.empty else None,
                "end_price": float(close.iloc[-1]) if not close.empty else None,
                "buy_hold_return": _buy_hold_return(close),
                "max_price_return": _max_price_return(close),
                "max_forward_return": _max_forward_return(close),
                "max_drawdown": _max_drawdown(close),
                "trade_count": float(len(stock_trades)),
                "closed_trade_count": float(len(closed)),
                "win_rate": _win_rate(closed),
                "avg_trade_return": _mean(closed, "realized_return"),
                "best_trade_return": _max(closed, "realized_return"),
                "worst_trade_return": _min(closed, "realized_return"),
                "avg_holding_days": _mean(closed, "holding_days"),
                "max_holding_days": _max(closed, "holding_days"),
            }
        )
    return pd.DataFrame(rows)


def build_overall_report(
    symbols: list[str],
    equity_curve: pd.DataFrame,
    trades: pd.DataFrame,
    metrics: dict[str, float],
    per_symbol: pd.DataFrame,
) -> pd.DataFrame:
    """Build a one-row summary of stock pool and strategy results."""

    closed = trades[trades["action"] == "sell"] if not trades.empty else pd.DataFrame()
    return pd.DataFrame(
        [
            {
                "stock_count": len(symbols),
                "start_date": equity_curve["date"].iloc[0] if not equity_curve.empty else pd.NaT,
                "end_date": equity_curve["date"].iloc[-1] if not equity_curve.empty else pd.NaT,
                "total_return": metrics.get("total_return", 0.0),
                "max_drawdown": metrics.get("max_drawdown", 0.0),
                "trade_count": float(len(trades)),
                "closed_trade_count": float(len(closed)),
                "win_rate": metrics.get("win_rate", 0.0),
                "avg_trade_return": metrics.get("avg_trade_return", 0.0),
                "best_trade_return": metrics.get("best_trade_return", 0.0),
                "worst_trade_return": metrics.get("worst_trade_return", 0.0),
                "avg_holding_days": _mean(closed, "holding_days"),
                "best_stock_buy_hold_return": _max(per_symbol, "buy_hold_return"),
                "best_stock_max_forward_return": _max(per_symbol, "max_forward_return"),
                "worst_stock_max_drawdown": _min(per_symbol, "max_drawdown"),
            }
        ]
    )


def _buy_hold_return(close: pd.Series) -> float | None:
    if len(close) < 2:
        return None
    return float(close.iloc[-1] / close.iloc[0] - 1.0)


def _max_price_return(close: pd.Series) -> float | None:
    if close.empty:
        return None
    return float(close.max() / close.iloc[0] - 1.0)


def _max_forward_return(close: pd.Series) -> float | None:
    if close.empty:
        return None
    running_min = close.cummin()
    return float((close / running_min - 1.0).max())


def _max_drawdown(close: pd.Series) -> float | None:
    if close.empty:
        return None
    running_max = close.cummax()
    return float((close / running_max - 1.0).min())


def _win_rate(df: pd.DataFrame) -> float:
    if df.empty or "realized_return" not in df:
        return 0.0
    return float((df["realized_return"].astype(float) > 0).mean())


def _mean(df: pd.DataFrame, col: str) -> float | None:
    if df.empty or col not in df:
        return None
    return float(df[col].astype(float).mean())


def _max(df: pd.DataFrame, col: str) -> float | None:
    if df.empty or col not in df:
        return None
    return float(df[col].astype(float).max())


def _min(df: pd.DataFrame, col: str) -> float | None:
    if df.empty or col not in df:
        return None
    return float(df[col].astype(float).min())
