"""Visualize saved strategy-five states on interactive stock K-line charts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from stock_selection.data.market_cap import (
    format_market_cap,
    load_cached_market_caps,
    merge_market_caps,
)
from stock_selection.data.tushare_cache import DEFAULT_CACHE_ROOT
from stock_selection.strategies.stock_short_high_state.backtest import (
    build_short_trade_backtest,
    sum_evaluated_returns_by_stock,
)
from stock_selection.strategies.stock_short_high_state.report import (
    DEFAULT_OUTPUT_ROOT,
)


STRATEGY_DIRECTORY = Path(__file__).resolve().parent
DEFAULT_CODES_FILE = STRATEGY_DIRECTORY / "strategy_five_chart_codes.txt"
RISE_COLOR = "#d84a3a"
FALL_COLOR = "#169c72"
SHORT_FILL_COLOR = "rgba(255, 215, 0, 0.22)"
SHORT_BORDER_COLOR = "rgba(205, 155, 0, 0.85)"
ALERT_COLOR = "#7b2cbf"


REQUIRED_STATE_COLUMNS = {
    "ts_code",
    "stock_name",
    "trade_date",
    "close",
    "vol",
    "lookback_days",
    "observed_state",
    "observed_state_name",
    "state",
    "state_name",
    "state_description",
    "cycle_id",
    "peak_number",
    "heat",
    "reference_high_date",
    "reference_high_close",
    "reference_low_date",
    "reference_low_close",
    "alert_code",
}


@dataclass(frozen=True)
class StateInterval:
    """One backtest holding interval, including its entry and end dates."""

    start_date: str
    end_date: str
    state: int
    state_name: str
    state_description: str
    cycle_id: int
    peak_number: int
    trading_days: int


def resolve_states_csv(
    *,
    states_csv: str | Path | None = None,
    as_of_date: str | None = None,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
) -> Path:
    """Resolve an explicit states CSV, a dated output, or the latest output."""
    if states_csv is not None:
        path = Path(states_csv)
        if not path.exists():
            raise FileNotFoundError(f"states CSV does not exist: {path}")
        return path

    root = Path(output_root)
    target = _normalise_date(as_of_date)
    if as_of_date is not None and not target:
        raise ValueError("as_of_date must be a valid date")
    if target:
        path = root / f"{target[:4]}-{target[4:6]}-{target[6:8]}" / "states.csv"
        if not path.exists():
            raise FileNotFoundError(f"states CSV does not exist: {path}")
        return path

    candidates = sorted(root.glob("????-??-??/states.csv"))
    if not candidates:
        raise FileNotFoundError(f"no states.csv found under {root}")
    return candidates[-1]


def read_chart_codes(
    path: str | Path = DEFAULT_CODES_FILE,
) -> tuple[list[str], list[str]]:
    """Read one stock code per line and return valid codes plus errors."""
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"chart code file does not exist: {source}")
    codes: list[str] = []
    errors: list[str] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(
        source.read_text(encoding="utf-8-sig").splitlines(),
        start=1,
    ):
        value = raw_line.split("#", 1)[0].strip()
        if not value:
            continue
        code = normalise_stock_code(value)
        if not code:
            errors.append(f"line {line_number}: invalid stock code {value!r}")
            continue
        if code not in seen:
            seen.add(code)
            codes.append(code)
    return codes, errors


def normalise_stock_code(value: str) -> str:
    """Normalize six-digit or Tushare stock codes."""
    text = str(value).strip().upper()
    if "." in text:
        symbol, suffix = text.rsplit(".", 1)
        if len(symbol) == 6 and symbol.isdigit() and suffix in {"SH", "SZ", "BJ"}:
            return f"{symbol}.{suffix}"
        return ""
    if len(text) != 6 or not text.isdigit():
        return ""
    if text.startswith(("5", "6")):
        return f"{text}.SH"
    if text.startswith(("0", "1", "2", "3")):
        return f"{text}.SZ"
    if text.startswith(("4", "8", "9")):
        return f"{text}.BJ"
    return ""


def read_saved_states(path: str | Path) -> pd.DataFrame:
    """Read and validate a strategy-five states CSV."""
    source = Path(path)
    data = pd.read_csv(
        source,
        dtype={
            "ts_code": str,
            "stock_name": str,
            "trade_date": str,
            "state_start_date": str,
            "reference_high_date": str,
            "reference_low_date": str,
            "trough_confirm_date": str,
            "observed_state_name": str,
            "state_name": str,
            "state_description": str,
            "alert_code": str,
        },
        low_memory=False,
    )
    missing = sorted(REQUIRED_STATE_COLUMNS.difference(data.columns))
    if missing:
        raise ValueError(
            f"states CSV is missing columns: {', '.join(missing)}. "
            "请先重新运行策略五 cli.py 生成新版 states.csv，再运行画图命令；"
            "旧文件缺少当日实际状态或放量参考所需数据。"
        )
    result = data.copy()
    result["ts_code"] = result["ts_code"].fillna("").astype(str).str.strip()
    result["trade_date"] = result["trade_date"].map(_normalise_date)
    result["state"] = pd.to_numeric(result["state"], errors="coerce")
    result["close"] = pd.to_numeric(result["close"], errors="coerce")
    invalid = result[
        result["ts_code"].eq("")
        | result["trade_date"].eq("")
        | result["state"].isna()
        | result["close"].isna()
    ]
    if not invalid.empty:
        raise ValueError("states CSV contains invalid code, date, state, or close values")
    duplicates = result.duplicated(["ts_code", "trade_date"], keep=False)
    if duplicates.any():
        raise ValueError("states CSV contains duplicate stock/date rows")
    result["state"] = result["state"].astype(int)
    return result.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)


def load_cached_stock_bars(
    stock_codes: Iterable[str],
    *,
    start_date: str,
    end_date: str,
    cache_root: str | Path | None = None,
) -> dict[str, pd.DataFrame]:
    """Load cached OHLCV bars for selected stocks and dates."""
    codes = {str(code).strip() for code in stock_codes}
    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    directory = root / "daily"
    paths = [
        path
        for path in sorted(directory.glob("*.parquet"))
        if start_date <= path.stem <= end_date
    ]
    if not paths:
        raise FileNotFoundError(
            f"no daily cache files found from {start_date} to {end_date} under {directory}"
        )
    frames: list[pd.DataFrame] = []
    columns = ["ts_code", "trade_date", "open", "high", "low", "close", "vol"]
    for path in paths:
        frame = pd.read_parquet(path, columns=columns)
        frame = frame[frame["ts_code"].isin(codes)]
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return {}
    data = pd.concat(frames, ignore_index=True)
    return {
        ts_code: group.sort_values("trade_date").reset_index(drop=True)
        for ts_code, group in data.groupby("ts_code", sort=True)
    }


def merge_bars_and_states(
    bars: pd.DataFrame,
    states: pd.DataFrame,
    *,
    close_tolerance: float = 1e-6,
) -> pd.DataFrame:
    """Join OHLCV bars to saved states and verify close-price consistency."""
    bar_columns = {"trade_date", "open", "high", "low", "close", "vol"}
    missing_bars = sorted(bar_columns.difference(bars.columns))
    if missing_bars:
        raise ValueError(f"stock bars are missing columns: {', '.join(missing_bars)}")
    normalized_bars = bars.copy()
    normalized_bars["trade_date"] = normalized_bars["trade_date"].map(_normalise_date)
    normalized_bars = normalized_bars.rename(columns={"close": "bar_close", "vol": "bar_vol"})
    # Preserve saved volume so figure-level backtests use the same inputs as
    # reports, even if the OHLC cache was refreshed after states.csv was saved.
    state_view = states.rename(columns={"close": "state_close"})
    merge_keys = ["trade_date"]
    if "ts_code" in state_view.columns and "ts_code" in normalized_bars.columns:
        merge_keys.insert(0, "ts_code")
    merged = state_view.merge(normalized_bars, on=merge_keys, how="left")
    if merged["bar_close"].isna().any():
        missing_dates = merged.loc[merged["bar_close"].isna(), "trade_date"].tolist()
        raise ValueError(
            f"OHLC cache is missing {len(missing_dates)} state dates, "
            f"first missing date: {missing_dates[0]}"
        )
    close_matches = np.isclose(
        merged["state_close"].astype(float),
        merged["bar_close"].astype(float),
        rtol=0.0,
        atol=close_tolerance,
    )
    if not bool(close_matches.all()):
        mismatch = merged.loc[~close_matches].iloc[0]
        raise ValueError(
            f"state close and OHLC close differ on {mismatch['trade_date']}: "
            f"{mismatch['state_close']} != {mismatch['bar_close']}"
        )
    merged["close"] = merged["bar_close"].astype(float)
    merged["date"] = pd.to_datetime(merged["trade_date"], format="%Y%m%d")
    return merged.sort_values("trade_date").reset_index(drop=True)


def build_short_state_intervals(
    states: pd.DataFrame,
    *,
    trades: Iterable[dict[str, Any]] | None = None,
) -> list[StateInterval]:
    """Derive holding intervals from backtest trades, without separate state rules."""
    if states.empty:
        return []
    if trades is None:
        trades = build_short_trade_backtest(
            states, as_of_date=str(states["trade_date"].max()),
        )["trades"]
    intervals: list[StateInterval] = []
    for trade in trades:
        entry_rows = states[
            states["ts_code"].eq(trade["ts_code"])
            & states["trade_date"].eq(trade["entry_date"])
        ]
        entry_row = entry_rows.iloc[0] if not entry_rows.empty else None
        intervals.append(StateInterval(
            start_date=str(trade["entry_date"]),
            end_date=str(trade.get("exit_date") or trade["evaluation_date"]),
            state=int(entry_row["state"]) if entry_row is not None else 0,
            state_name=str(trade["entry_state"]),
            state_description=(
                str(entry_row["state_description"])
                if entry_row is not None else str(trade["entry_state"])
            ),
            cycle_id=int(trade["cycle_id"]),
            peak_number=int(trade["peak_number"]),
            trading_days=int(trade["holding_trade_days"]) + 1,
        ))
    return intervals


def build_stock_short_state_figure(
    chart_data: pd.DataFrame,
    *,
    stock_code: str,
    stock_name: str = "",
    initial_bars: int | None = 250,
    trade_backtest: dict[str, Any] | None = None,
) -> Any:
    """Plot backtest holding regions, entry/exit markers, alerts, and hover details."""
    go, make_subplots = _import_plotly()
    data = chart_data.sort_values("trade_date").reset_index(drop=True)
    if data.empty:
        raise ValueError("chart_data must not be empty")
    if trade_backtest is None:
        trade_backtest = build_short_trade_backtest(
            data, as_of_date=str(data["trade_date"].max()),
        )
    stock_trades = [
        trade for trade in trade_backtest["trades"] if trade["ts_code"] == stock_code
    ]
    stock_backtest = {"metadata": trade_backtest["metadata"], "trades": stock_trades}
    dates = data["date"]
    volume_colors = [
        RISE_COLOR if close >= open_ else FALL_COLOR
        for open_, close in zip(data["open"], data["close"])
    ]
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.78, 0.22],
        vertical_spacing=0.03,
    )
    figure.add_trace(
        go.Candlestick(
            x=dates,
            open=data["open"],
            high=data["high"],
            low=data["low"],
            close=data["close"],
            increasing_line_color=RISE_COLOR,
            decreasing_line_color=FALL_COLOR,
            increasing_fillcolor=RISE_COLOR,
            decreasing_fillcolor=FALL_COLOR,
            name="K线",
            hoverinfo="skip",
        ),
        row=1,
        col=1,
    )

    latest = data.iloc[-1]
    latest_market_cap = (
        f"最新总市值 {format_market_cap(latest.get('total_mv'))} · "
        f"流通市值 {format_market_cap(latest.get('circ_mv'))}"
    )
    figure.add_trace(
        go.Bar(
            x=dates,
            y=data["bar_vol"] if "bar_vol" in data.columns else data["vol"],
            marker_color=volume_colors,
            marker_line_width=0,
            opacity=0.72,
            name="成交量",
            hovertemplate="%{x|%Y-%m-%d}<br>成交量 %{y:,.0f}<extra></extra>",
        ),
        row=2,
        col=1,
    )

    intervals = build_short_state_intervals(data, trades=stock_trades)
    for interval in intervals:
        start = pd.to_datetime(interval.start_date, format="%Y%m%d") - pd.Timedelta(
            hours=12
        )
        end = pd.to_datetime(interval.end_date, format="%Y%m%d") + pd.Timedelta(
            hours=12
        )
        figure.add_vrect(
            x0=start,
            x1=end,
            fillcolor=SHORT_FILL_COLOR,
            line_color=SHORT_BORDER_COLOR,
            line_width=1,
            layer="below",
            row="all",
            col=1,
        )

    if stock_trades:
        figure.add_trace(
            go.Scatter(
                x=pd.to_datetime(
                    [trade["entry_date"] for trade in stock_trades], format="%Y%m%d",
                ),
                y=[trade["entry_close"] for trade in stock_trades],
                mode="markers",
                marker={
                    "symbol": "triangle-down",
                    "size": 12,
                    "color": "#ffd700",
                    "line": {"color": "#8a6d00", "width": 1.5},
                },
                text=[_trade_hover(trade) for trade in stock_trades],
                hovertemplate="%{text}<extra></extra>",
                name="做空开仓",
            ),
            row=1,
            col=1,
        )

    closed_trades = [trade for trade in stock_trades if trade["status"] == "COMPLETED"]
    if closed_trades:
        figure.add_trace(
            go.Scatter(
                x=pd.to_datetime(
                    [trade["exit_date"] for trade in closed_trades], format="%Y%m%d",
                ),
                y=[trade["exit_close"] for trade in closed_trades],
                mode="markers",
                marker={
                    "symbol": "triangle-up",
                    "size": 12,
                    "color": "#2563eb",
                    "line": {"color": "#1e3a8a", "width": 1.5},
                },
                text=[f"买入平仓<br>{_trade_hover(trade)}" for trade in closed_trades],
                hovertemplate="%{text}<extra></extra>",
                name="做空平仓",
            ),
            row=1,
            col=1,
        )

    alert_rows = _strategy_alert_rows(data, stock_backtest)
    if not alert_rows.empty:
        figure.add_trace(
            go.Scatter(
                x=alert_rows["date"],
                y=alert_rows["high"] * 1.035,
                mode="markers",
                marker={"symbol": "x", "size": 12, "color": ALERT_COLOR},
                text=[
                    _alert_hover(row)
                    for row in alert_rows.to_dict("records")
                ],
                hovertemplate="%{text}<extra></extra>",
                name="策略异常",
            ),
            row=1,
            col=1,
        )

    trade_events: dict[str, list[str]] = {}
    for trade in stock_trades:
        trade_details = _trade_hover(trade)
        trade_events.setdefault(str(trade["entry_date"]), []).append(
            f"开仓记录<br>{trade_details}"
        )
        trade_events.setdefault(str(trade["evaluation_date"]), []).append(
            f"结束/估值记录<br>{trade_details}"
        )
    hover_details = [
        _state_hover(row)
        + "".join(
            f"<br><br>{details}"
            for details in trade_events.get(str(row["trade_date"]), [])
        )
        for row in data.to_dict("records")
    ]
    figure.add_trace(
        go.Scatter(
            x=dates,
            y=data["close"],
            mode="markers",
            marker={"size": 12, "color": "rgba(0,0,0,0)"},
            text=hover_details,
            hovertemplate="%{text}<extra></extra>",
            name="状态详情",
            showlegend=False,
        ),
        row=1,
        col=1,
    )

    for row in (1, 2):
        figure.update_xaxes(
            rangeslider_visible=False,
            showgrid=True,
            gridcolor="#eef1f4",
            rangebreaks=[{"bounds": ["sat", "mon"]}],
            row=row,
            col=1,
        )
    if initial_bars and len(data) > initial_bars:
        initial_range = [
            data["date"].iloc[-initial_bars],
            data["date"].iloc[-1] + pd.Timedelta(days=1),
        ]
        for row in (1, 2):
            figure.update_xaxes(range=initial_range, row=row, col=1)

    title_name = f"{stock_name} " if stock_name else ""
    figure.update_layout(
        title={
            "text": f"{title_name}{stock_code} 策略五状态检查"
            f"<br><sup>{latest_market_cap}</sup>",
            "x": 0.01,
            "xanchor": "left",
        },
        height=760,
        template="plotly_white",
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        margin={"l": 72, "r": 32, "t": 92, "b": 48},
        hovermode="x unified",
        dragmode="zoom",
        bargap=0.16,
        font={
            "family": "Arial, PingFang SC, Microsoft YaHei, sans-serif",
            "size": 12,
            "color": "#22262b",
        },
        legend={"orientation": "h", "x": 0.01, "y": 1.03},
        uirevision="stock-short-high-state",
    )
    figure.update_yaxes(title_text="价格", row=1, col=1)
    figure.update_yaxes(title_text="成交量", rangemode="tozero", row=2, col=1)
    return figure


def write_stock_short_state_html(
    chart_data: pd.DataFrame,
    output_path: str | Path,
    *,
    stock_code: str,
    stock_name: str = "",
    initial_bars: int | None = 250,
    trade_backtest: dict[str, Any] | None = None,
) -> Path:
    """Write one stock chart HTML using a shared local Plotly bundle."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    figure = build_stock_short_state_figure(
        chart_data,
        stock_code=stock_code,
        stock_name=stock_name,
        initial_bars=initial_bars,
        trade_backtest=trade_backtest,
    )
    figure.write_html(
        path,
        include_plotlyjs="directory",
        full_html=True,
        config={
            "displaylogo": False,
            "responsive": True,
            "scrollZoom": True,
            "modeBarButtonsToRemove": ["lasso2d", "select2d"],
        },
    )
    return path


def generate_stock_short_state_charts(
    *,
    states_csv: str | Path,
    codes_file: str | Path = DEFAULT_CODES_FILE,
    stock_codes: Iterable[str] | None = None,
    cache_root: str | Path | None = None,
    output_directory: str | Path | None = None,
    initial_bars: int | None = 250,
) -> dict[str, Any]:
    """Generate charts for the code list, or all saved stocks if the list is empty."""
    state_path = Path(states_csv)
    states = read_saved_states(state_path)
    codes, input_errors = _resolve_chart_codes(codes_file, stock_codes)
    if not codes:
        if input_errors or stock_codes is not None:
            raise ValueError("chart code file contains no valid stock codes")
        codes = sorted(states["ts_code"].unique().tolist())
        if not codes:
            raise ValueError("states CSV contains no stocks to chart")
    selected_states = states[states["ts_code"].isin(codes)]
    if selected_states.empty:
        raise ValueError("none of the requested stock codes exist in the states CSV")
    start_date = str(selected_states["trade_date"].min())
    end_date = str(selected_states["trade_date"].max())
    bars_by_code = load_cached_stock_bars(
        codes,
        start_date=start_date,
        end_date=end_date,
        cache_root=cache_root,
    )
    market_caps = load_cached_market_caps(
        start_date=start_date,
        end_date=end_date,
        stock_codes=codes,
        cache_root=cache_root,
    )
    output = (
        Path(output_directory)
        if output_directory is not None
        else state_path.parent / (
            f"charts_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        )
    )
    output.mkdir(parents=True, exist_ok=output_directory is not None)
    positive_output = output / "positive"
    negative_output = output / "negative"
    unclassified_output = output / "unclassified"
    positive_output.mkdir(parents=True, exist_ok=True)
    negative_output.mkdir(parents=True, exist_ok=True)
    unclassified_output.mkdir(parents=True, exist_ok=True)

    chart_rows: list[dict[str, Any]] = []
    category_rows = {"positive": [], "negative": [], "unclassified": []}
    errors = list(input_errors)
    backtest = build_short_trade_backtest(
        selected_states,
        as_of_date=end_date,
    )
    trades_by_code: dict[str, list[dict[str, Any]]] = {}
    for trade in backtest.get("trades", []):
        trades_by_code.setdefault(str(trade["ts_code"]), []).append(trade)
    returns_by_stock = sum_evaluated_returns_by_stock(backtest.get("trades", []))

    for code in codes:
        stock_states = states[states["ts_code"].eq(code)].copy()
        if stock_states.empty:
            errors.append(f"{code}: not found in states CSV")
            continue
        bars = bars_by_code.get(code)
        if bars is None or bars.empty:
            errors.append(f"{code}: no cached OHLC data")
            continue
        try:
            chart_data = merge_bars_and_states(bars, stock_states)
            chart_data = merge_market_caps(chart_data, market_caps)
            stock_name = _latest_nonempty(stock_states["stock_name"])
            stock_trades = trades_by_code.get(code, [])
            stock_backtest = {"metadata": backtest["metadata"], "trades": stock_trades}
            classified_return = returns_by_stock.get(code)
            category = "unclassified"
            if classified_return is not None:
                category = "positive" if classified_return > 0 else "negative"
            category_output = output / category
            filename = f"{code.replace('.', '_')}.html"
            write_stock_short_state_html(
                chart_data,
                category_output / filename,
                stock_code=code,
                stock_name=stock_name,
                initial_bars=initial_bars,
                trade_backtest=stock_backtest,
            )
            latest = chart_data.sort_values("trade_date").iloc[-1]
            chart_rows.append(
                {
                    "ts_code": code,
                    "stock_name": stock_name,
                    "latest_state": str(latest["state_description"]),
                    "short_intervals": len(stock_trades),
                    "peak_intervals": sum(
                        int(trade["peak_number"]) > 0 for trade in stock_trades
                    ),
                    "alerts": len(_strategy_alert_rows(stock_states, stock_backtest)),
                    "total_mv": latest.get("total_mv"),
                    "circ_mv": latest.get("circ_mv"),
                    "filename": f"{category}/{filename}",
                    "classified_return_pct": classified_return,
                    "return_category": category,
                }
            )
            category_row = dict(chart_rows[-1])
            category_row["filename"] = filename
            category_rows[category].append(category_row)
        except (ValueError, FileNotFoundError) as exc:
            errors.append(f"{code}: {exc}")

    positive_index = _write_chart_index(
        positive_output,
        category_rows["positive"],
        [],
        state_path,
    )
    negative_index = _write_chart_index(
        negative_output,
        category_rows["negative"],
        [],
        state_path,
    )
    unclassified_index = _write_chart_index(
        unclassified_output,
        category_rows["unclassified"],
        [],
        state_path,
    )
    index_path = _write_chart_index(output, chart_rows, errors, state_path)
    return {
        "index": str(index_path),
        "output_directory": str(output),
        "positive_directory": str(positive_output),
        "negative_directory": str(negative_output),
        "unclassified_directory": str(unclassified_output),
        "positive_index": str(positive_index),
        "negative_index": str(negative_index),
        "unclassified_index": str(unclassified_index),
        "charts": chart_rows,
        "errors": errors,
    }


def _write_chart_index(
    output_directory: Path,
    chart_rows: list[dict[str, Any]],
    errors: list[str],
    states_csv: Path,
) -> Path:
    rows = []
    for item in chart_rows:
        rows.append(
            "<tr>"
            f"<td>{escape(item['ts_code'])}</td>"
            f"<td>{escape(item['stock_name'])}</td>"
            f"<td>{escape(item['latest_state'])}</td>"
            f"<td>{item['short_intervals']}</td>"
            f"<td>{item['peak_intervals']}</td>"
            f"<td>{item['alerts']}</td>"
            f"<td>{escape(format_market_cap(item.get('total_mv')))}</td>"
            f"<td>{escape(format_market_cap(item.get('circ_mv')))}</td>"
            f"<td><a href=\"{escape(item['filename'])}\">查看图表</a></td>"
            "</tr>"
        )
    if not rows:
        rows.append("<tr><td colspan=\"9\">没有生成图表</td></tr>")
    error_items = "".join(f"<li>{escape(error)}</li>" for error in errors)
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>策略五图表入口</title>
<style>
body{{font-family:Arial,"Microsoft YaHei",sans-serif;margin:28px;color:#222}}
table{{border-collapse:collapse;width:100%;max-width:1100px}}
th,td{{border:1px solid #ddd;padding:9px;text-align:left}}
th{{background:#f5f5f5}}a{{color:#0969da}}code{{background:#f5f5f5;padding:2px 5px}}
.note{{background:#fff8cc;border-left:4px solid #d6a800;padding:10px 14px}}
</style>
</head>
<body>
<h1>策略五状态图表</h1>
<p class="note">黄框表示回测开仓至平仓的区间，包含止损；持仓中延伸至最后数据日，失效交易截止失效日。黄色倒三角标记开仓，蓝色正三角标记平仓（包含止损），紫色叉号标记策略异常，其余状态及平仓原因通过鼠标悬停查看。K线保持红涨绿跌。</p>
<p>状态来源：<code>{escape(str(states_csv))}</code></p>
<table>
<thead><tr><th>股票</th><th>名称</th><th>最新状态</th><th>回测交易</th><th>其中波峰交易</th><th>异常</th><th>总市值</th><th>流通市值</th><th>图表</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>
{f'<h2>未生成项目</h2><ul>{error_items}</ul>' if errors else ''}
</body>
</html>
"""
    path = output_directory / "index.html"
    path.write_text(html, encoding="utf-8")
    return path


def _strategy_alert_rows(
    states: pd.DataFrame, backtest: dict[str, Any],
) -> pd.DataFrame:
    invalidated_dates = [
        trade["evaluation_date"]
        for trade in backtest["trades"] if trade["status"] == "INVALIDATED"
    ]
    return states[
        states["state_name"].eq(backtest["metadata"]["invalidation_state"])
        | states["alert_code"].fillna("").astype(str).str.strip().ne("")
        | states["trade_date"].isin(invalidated_dates)
    ]


def _trade_hover(trade: dict[str, Any]) -> str:
    status = {
        "COMPLETED": "已平仓", "OPEN": "持仓中", "INVALIDATED": "已失效",
    }.get(trade["status"], trade["status"])
    endpoint = {"COMPLETED": "平仓", "OPEN": "估值", "INVALIDATED": "失效"}.get(
        trade["status"], "结束",
    )
    details = (
        f"开仓状态：{trade['entry_state']}"
        f"<br>开仓：{trade['entry_date']} · {_format_number(trade['entry_close'])}"
        f"<br>{endpoint}：{trade['evaluation_date']} · {_format_number(trade['evaluation_close'])}"
        f"<br>交易状态：{status}"
        f"<br>结束状态：{trade['exit_state'] if trade['status'] == 'COMPLETED' else '-'}"
        f"<br>原因：{trade['status_reason']}"
        f"<br>收益：{_format_percent(trade['mark_return_pct'])}"
        f"<br>最大回撤：{_format_percent(trade['max_drawdown_pct'])}"
        f"<br>持有交易日：{trade['holding_trade_days']}"
        f"<br>周期：{trade['cycle_id']} · 波峰序号：{trade['peak_number']}"
        f"<br>结束日实际状态：{trade.get('evaluation_observed_state', '-')}"
        f"<br>参考状态：{trade.get('price_origin_status', '-')}"
        f"<br>参考高点：{trade.get('origin_high_date') or '-'} · {_format_number(trade.get('origin_high_close'))}"
        f"<br>前{trade.get('origin_lookback_days') or '-'}日最低收盘：{trade.get('origin_window_low_date') or '-'} · {_format_number(trade.get('origin_window_low_close'))}"
        f"<br>三天放量：{trade.get('volume_start_date') or '-'} / {trade.get('volume_middle_date') or '-'} / {trade.get('volume_end_date') or '-'}"
        f"<br>成交量：{_format_number(trade.get('volume_first'), digits=0)} / {_format_number(trade.get('volume_second'), digits=0)} / {_format_number(trade.get('volume_third'), digits=0)}"
        f"<br>第三天/第一天量比：{_format_number(trade.get('volume_expansion_ratio'))}"
        f"<br>行情价格起点：{trade.get('price_origin_date') or '-'} · {_format_number(trade.get('price_origin_close'))}"
        f"<br>当日实际收盘：{_format_number(trade.get('evaluation_actual_close'))}"
        f"<br>收盘价与参考价绝对偏离：{_format_percent(trade.get('price_origin_gap_pct'))}"
    )
    if trade.get("stop_trigger_close") is not None:
        details += (
            f"<br>止损触发日收盘：{_format_number(trade['stop_trigger_close'])}"
            "<br>平仓价按回测止损阈值模拟成交"
        )
    return details


def _state_hover(row: dict[str, Any]) -> str:
    heat = _format_percent(row.get("heat"))
    return (
        f"{row.get('trade_date', '')}"
        f"<br>开盘：{_format_number(row.get('open'))}"
        f"<br>最高：{_format_number(row.get('high'))}"
        f"<br>最低：{_format_number(row.get('low'))}"
        f"<br>收盘：{_format_number(row.get('close'))}"
        f"<br>总市值：{format_market_cap(row.get('total_mv'))}"
        f"<br>流通市值：{format_market_cap(row.get('circ_mv'))}"
        f"<br>成交量：{_format_number(row.get('bar_vol', row.get('vol')), digits=0)}"
        f"<br>状态：{row.get('state_description', '')}"
        f"<br>枚举：{row.get('state_name', '')} ({row.get('state', '')})"
        f"<br>当日实际状态：{row.get('observed_state_name', '')} ({row.get('observed_state', '')})"
        f"<br>周期：{_number_or_zero(row.get('cycle_id'))}"
        f"<br>波峰序号：{_number_or_zero(row.get('peak_number'))}"
        f"<br>热度：{heat}"
        f"<br>参考高点：{row.get('reference_high_date', '')} "
        f"{_format_number(row.get('reference_high_close'))}"
        f"<br>参考低点：{row.get('reference_low_date', '')} "
        f"{_format_number(row.get('reference_low_close'))}"
        f"<br>告警：{row.get('alert_code', '') or '-'}"
    )


def _alert_hover(row: dict[str, Any]) -> str:
    return (
        "策略异常"
        f"<br>日期：{row.get('trade_date', '')}"
        f"<br>状态：{row.get('state_name', '')} · {row.get('state_description', '')}"
        f"<br>收盘：{_format_number(row.get('close'))}"
        f"<br>总市值：{format_market_cap(row.get('total_mv'))}"
        f"<br>流通市值：{format_market_cap(row.get('circ_mv'))}"
        f"<br>原参考高点：{row.get('reference_high_date', '')} "
        f"{_format_number(row.get('reference_high_close'))}"
        f"<br>周期：{_number_or_zero(row.get('cycle_id'))}"
        f"<br>告警：{row.get('alert_code', '') or '-'}"
    )


def _latest_nonempty(values: pd.Series) -> str:
    nonempty = values.fillna("").astype(str).str.strip()
    nonempty = nonempty[nonempty.ne("")]
    return str(nonempty.iloc[-1]) if not nonempty.empty else ""


def _resolve_chart_codes(
    codes_file: str | Path,
    stock_codes: Iterable[str] | None,
) -> tuple[list[str], list[str]]:
    if stock_codes is None:
        return read_chart_codes(codes_file)
    codes: list[str] = []
    errors: list[str] = []
    seen: set[str] = set()
    for raw_code in stock_codes:
        code = normalise_stock_code(str(raw_code))
        if not code:
            errors.append(f"invalid stock code {raw_code!r}")
            continue
        if code not in seen:
            seen.add(code)
            codes.append(code)
    return codes, errors


def _number_or_zero(value: Any) -> int:
    return 0 if value is None or pd.isna(value) else int(value)


def _format_number(value: Any, *, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):,.{digits}f}"


def _format_percent(value: Any) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.2%}"


def _normalise_date(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip().replace("-", "")
    return text[:8] if len(text) >= 8 and text[:8].isdigit() else ""


def _import_plotly() -> tuple[Any, Any]:
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError as exc:
        raise RuntimeError(
            'Plotly is required for strategy-five charts. Install `.[viz]`.'
        ) from exc
    return go, make_subplots
