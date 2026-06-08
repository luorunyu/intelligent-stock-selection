"""Plotly price charts."""

from __future__ import annotations

import pandas as pd

from stock_selection.core.schema import CLOSE_COL, DATE_COL, HIGH_COL, LOW_COL, OPEN_COL, SYMBOL_COL, VOLUME_COL
from stock_selection.core.validation import normalize_price_frame


def _import_plotly():
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError as exc:
        raise ImportError("Install visualization dependencies with: pip install .[viz]") from exc
    return go, make_subplots


def plot_single_stock(
    df: pd.DataFrame,
    symbol: str,
    start_date: str | pd.Timestamp | None = None,
    end_date: str | pd.Timestamp | None = None,
    trades: pd.DataFrame | None = None,
):
    """Create a candlestick + volume chart for a single stock."""

    go, make_subplots = _import_plotly()
    data = normalize_price_frame(df)
    sdf = data[data[SYMBOL_COL] == symbol]
    if start_date is not None:
        sdf = sdf[sdf[DATE_COL] >= pd.Timestamp(start_date)]
    if end_date is not None:
        sdf = sdf[sdf[DATE_COL] <= pd.Timestamp(end_date)]

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.7, 0.3])
    fig.add_trace(
        go.Candlestick(x=sdf[DATE_COL], open=sdf[OPEN_COL], high=sdf[HIGH_COL], low=sdf[LOW_COL], close=sdf[CLOSE_COL]),
        row=1,
        col=1,
    )
    fig.add_trace(go.Bar(x=sdf[DATE_COL], y=sdf[VOLUME_COL], name="Volume"), row=2, col=1)
    _add_trade_markers(fig, go, sdf, symbol, trades, row=1, col=1)
    fig.update_layout(title=symbol, xaxis_rangeslider_visible=False)
    return fig


def plot_stock_list(
    df: pd.DataFrame,
    symbols: list[str],
    start_date: str | pd.Timestamp | None = None,
    end_date: str | pd.Timestamp | None = None,
    include_volume: bool = True,
    trades: pd.DataFrame | None = None,
):
    """Create daily K-line charts for a list of stock codes."""

    go, make_subplots = _import_plotly()
    data = normalize_price_frame(df)
    data = data[data[SYMBOL_COL].astype(str).isin([str(symbol) for symbol in symbols])]
    if start_date is not None:
        data = data[data[DATE_COL] >= pd.Timestamp(start_date)]
    if end_date is not None:
        data = data[data[DATE_COL] <= pd.Timestamp(end_date)]

    rows_per_symbol = 2 if include_volume else 1
    total_rows = max(len(symbols) * rows_per_symbol, 1)
    row_heights = []
    subplot_titles = []
    for symbol in symbols:
        subplot_titles.append(str(symbol))
        row_heights.append(0.7 if include_volume else 1.0)
        if include_volume:
            subplot_titles.append(f"{symbol} volume")
            row_heights.append(0.3)

    fig = make_subplots(
        rows=total_rows,
        cols=1,
        shared_xaxes=False,
        vertical_spacing=0.02,
        row_heights=row_heights,
        subplot_titles=subplot_titles,
    )
    for index, symbol in enumerate(symbols):
        sdf = data[data[SYMBOL_COL].astype(str) == str(symbol)].sort_values(DATE_COL)
        price_row = index * rows_per_symbol + 1
        fig.add_trace(
            go.Candlestick(
                x=sdf[DATE_COL],
                open=sdf[OPEN_COL],
                high=sdf[HIGH_COL],
                low=sdf[LOW_COL],
                close=sdf[CLOSE_COL],
                name=str(symbol),
            ),
            row=price_row,
            col=1,
        )
        if include_volume:
            fig.add_trace(
                go.Bar(x=sdf[DATE_COL], y=sdf[VOLUME_COL], name=f"{symbol} volume"),
                row=price_row + 1,
                col=1,
            )
        _add_trade_markers(fig, go, sdf, str(symbol), trades, row=price_row, col=1)
    fig.update_layout(
        title="Daily K-Line Charts",
        xaxis_rangeslider_visible=False,
        height=max(320 * len(symbols), 360),
        showlegend=False,
    )
    return fig


def _add_trade_markers(
    fig,
    go,
    sdf: pd.DataFrame,
    symbol: str,
    trades: pd.DataFrame | None,
    row: int,
    col: int,
) -> None:
    if trades is None or trades.empty or sdf.empty:
        return
    stock_trades = trades[trades[SYMBOL_COL].astype(str) == str(symbol)].copy()
    if stock_trades.empty:
        return
    stock_trades["date"] = pd.to_datetime(stock_trades["date"])
    price_lookup = sdf.set_index(DATE_COL)[CLOSE_COL]

    buys = stock_trades[stock_trades["action"] == "buy"]
    sells = stock_trades[stock_trades["action"] == "sell"]
    if not buys.empty:
        fig.add_trace(
            go.Scatter(
                x=buys["date"],
                y=[_trade_price(row_, price_lookup) for _, row_ in buys.iterrows()],
                mode="markers",
                marker={"symbol": "triangle-up", "size": 11, "color": "#16a34a"},
                name=f"{symbol} buy",
                text=buys.get("reason"),
                hovertemplate="Buy %{x}<br>price=%{y:.2f}<br>%{text}<extra></extra>",
            ),
            row=row,
            col=col,
        )
    if not sells.empty:
        fig.add_trace(
            go.Scatter(
                x=sells["date"],
                y=[_trade_price(row_, price_lookup) for _, row_ in sells.iterrows()],
                mode="markers",
                marker={"symbol": "triangle-down", "size": 11, "color": "#dc2626"},
                name=f"{symbol} sell",
                text=sells.get("reason"),
                hovertemplate="Sell %{x}<br>price=%{y:.2f}<br>%{text}<extra></extra>",
            ),
            row=row,
            col=col,
        )


def _trade_price(trade_row: pd.Series, price_lookup: pd.Series) -> float:
    if "price" in trade_row and pd.notna(trade_row["price"]):
        return float(trade_row["price"])
    date = pd.Timestamp(trade_row["date"])
    return float(price_lookup.loc[date])


def plot_stock_comparison(
    df: pd.DataFrame,
    symbols: list[str],
    start_date: str | pd.Timestamp | None = None,
    end_date: str | pd.Timestamp | None = None,
):
    """Create normalized close-price comparison lines."""

    go, _ = _import_plotly()
    data = normalize_price_frame(df)
    data = data[data[SYMBOL_COL].isin(symbols)]
    if start_date is not None:
        data = data[data[DATE_COL] >= pd.Timestamp(start_date)]
    if end_date is not None:
        data = data[data[DATE_COL] <= pd.Timestamp(end_date)]
    fig = go.Figure()
    for symbol, sdf in data.groupby(SYMBOL_COL):
        series = sdf.sort_values(DATE_COL)[CLOSE_COL]
        normalized = series / series.iloc[0]
        fig.add_trace(go.Scatter(x=sdf.sort_values(DATE_COL)[DATE_COL], y=normalized, mode="lines", name=symbol))
    fig.update_layout(title="Normalized Performance")
    return fig
