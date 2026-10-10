"""Visualize saved strategy-five states on interactive stock K-line charts."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from stock_selection.data.tushare_cache import DEFAULT_CACHE_ROOT
from stock_selection.strategies.stock_short_high_state.report import (
    DEFAULT_OUTPUT_ROOT,
)
from stock_selection.strategies.stock_short_high_state.screen import StockShortState


STRATEGY_DIRECTORY = Path(__file__).resolve().parent
DEFAULT_CODES_FILE = STRATEGY_DIRECTORY / "strategy_five_chart_codes.txt"
SHORT_SCOPE_STATES = {
    int(StockShortState.HIGH_CANDIDATE),
    int(StockShortState.HIGH_CONFIRMED),
    int(StockShortState.PEAK_1),
    int(StockShortState.PEAK_2),
    int(StockShortState.PEAK_3),
    int(StockShortState.PEAK_N),
}
ALERT_STATE = int(StockShortState.INVALID_HIGH_NEW_HIGH)
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
    """One continuous region with the same short-scope state."""

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
        dtype={"ts_code": str, "trade_date": str, "state_name": str},
    )
    missing = sorted(REQUIRED_STATE_COLUMNS.difference(data.columns))
    if missing:
        raise ValueError(f"states CSV is missing columns: {', '.join(missing)}")
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
    normalized_bars = normalized_bars.rename(columns={"close": "bar_close"})
    state_view = states.rename(columns={"close": "state_close"})
    merged = state_view.merge(normalized_bars, on="trade_date", how="left")
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


def build_short_state_intervals(states: pd.DataFrame) -> list[StateInterval]:
    """Merge consecutive rows with the same short-scope state and cycle."""
    intervals: list[StateInterval] = []
    active: dict[str, Any] | None = None
    ordered = states.sort_values("trade_date").reset_index(drop=True)
    for row in ordered.itertuples(index=False):
        state = int(row.state)
        is_short = state in SHORT_SCOPE_STATES
        identity = (
            state,
            int(_number_or_zero(row.cycle_id)),
            int(_number_or_zero(row.peak_number)),
        )
        if not is_short:
            if active is not None:
                intervals.append(_finish_interval(active))
                active = None
            continue
        if active is None or active["identity"] != identity:
            if active is not None:
                intervals.append(_finish_interval(active))
            active = {
                "identity": identity,
                "start_date": str(row.trade_date),
                "end_date": str(row.trade_date),
                "state": state,
                "state_name": str(row.state_name),
                "state_description": str(row.state_description),
                "cycle_id": identity[1],
                "peak_number": identity[2],
                "trading_days": 1,
            }
        else:
            active["end_date"] = str(row.trade_date)
            active["trading_days"] += 1
    if active is not None:
        intervals.append(_finish_interval(active))
    return intervals


def build_stock_short_state_figure(
    chart_data: pd.DataFrame,
    *,
    stock_code: str,
    stock_name: str = "",
    initial_bars: int | None = 250,
) -> Any:
    """Build one interactive K-line chart with yellow short-scope regions."""
    go, make_subplots = _import_plotly()
    data = chart_data.sort_values("trade_date").reset_index(drop=True)
    if data.empty:
        raise ValueError("chart_data must not be empty")
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
    figure.add_trace(
        go.Bar(
            x=dates,
            y=data["vol"],
            marker_color=volume_colors,
            marker_line_width=0,
            opacity=0.72,
            name="成交量",
            hovertemplate="%{x|%Y-%m-%d}<br>成交量 %{y:,.0f}<extra></extra>",
        ),
        row=2,
        col=1,
    )

    intervals = build_short_state_intervals(data)
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

    interval_starts = {interval.start_date: interval for interval in intervals}
    start_rows = data[data["trade_date"].isin(interval_starts)].copy()
    if not start_rows.empty:
        marker_text = []
        for row in start_rows.itertuples(index=False):
            interval = interval_starts[str(row.trade_date)]
            confirmation = (
                "<br>性质：事后确认"
                if interval.state == int(StockShortState.HIGH_CONFIRMED)
                else ""
            )
            marker_text.append(
                f"{interval.state_description}{confirmation}"
                f"<br>开始：{interval.start_date}"
                f"<br>结束：{interval.end_date}"
                f"<br>交易日：{interval.trading_days}"
                f"<br>周期：{interval.cycle_id}"
                f"<br>波峰序号：{interval.peak_number}"
            )
        figure.add_trace(
            go.Scatter(
                x=start_rows["date"],
                y=start_rows["high"] * 1.02,
                mode="markers",
                marker={
                    "symbol": "triangle-down",
                    "size": 12,
                    "color": "#ffd700",
                    "line": {"color": "#8a6d00", "width": 1.5},
                },
                text=marker_text,
                hovertemplate="%{text}<extra></extra>",
                name="做空区域开始",
            ),
            row=1,
            col=1,
        )

    alert_rows = data[data["state"].eq(ALERT_STATE)]
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

    figure.add_trace(
        go.Scatter(
            x=dates,
            y=data["close"],
            mode="markers",
            marker={"size": 12, "color": "rgba(0,0,0,0.001)"},
            text=[_state_hover(row) for row in data.to_dict("records")],
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
            "text": f"{title_name}{stock_code} 策略五状态检查",
            "x": 0.01,
            "xanchor": "left",
        },
        height=760,
        template="plotly_white",
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        margin={"l": 72, "r": 32, "t": 72, "b": 48},
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
) -> Path:
    """Write one self-contained stock chart HTML."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    figure = build_stock_short_state_figure(
        chart_data,
        stock_code=stock_code,
        stock_name=stock_name,
        initial_bars=initial_bars,
    )
    figure.write_html(
        path,
        include_plotlyjs=True,
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
    cache_root: str | Path | None = None,
    output_directory: str | Path | None = None,
    initial_bars: int | None = 250,
) -> dict[str, Any]:
    """Generate one chart per requested stock plus an HTML index."""
    state_path = Path(states_csv)
    states = read_saved_states(state_path)
    codes, input_errors = read_chart_codes(codes_file)
    if not codes:
        raise ValueError("chart code file contains no valid stock codes")
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
    output = (
        Path(output_directory)
        if output_directory is not None
        else state_path.parent / "charts"
    )
    output.mkdir(parents=True, exist_ok=True)

    chart_rows: list[dict[str, Any]] = []
    errors = list(input_errors)
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
            stock_name = _latest_nonempty(stock_states["stock_name"])
            filename = f"{code.replace('.', '_')}.html"
            write_stock_short_state_html(
                chart_data,
                output / filename,
                stock_code=code,
                stock_name=stock_name,
                initial_bars=initial_bars,
            )
            intervals = build_short_state_intervals(stock_states)
            latest = stock_states.sort_values("trade_date").iloc[-1]
            chart_rows.append(
                {
                    "ts_code": code,
                    "stock_name": stock_name,
                    "latest_state": str(latest["state_description"]),
                    "short_intervals": len(intervals),
                    "peak_intervals": sum(
                        interval.state
                        in {
                            int(StockShortState.PEAK_1),
                            int(StockShortState.PEAK_2),
                            int(StockShortState.PEAK_3),
                            int(StockShortState.PEAK_N),
                        }
                        for interval in intervals
                    ),
                    "alerts": int(stock_states["state"].eq(ALERT_STATE).sum()),
                    "filename": filename,
                }
            )
        except (ValueError, FileNotFoundError) as exc:
            errors.append(f"{code}: {exc}")

    index_path = _write_chart_index(output, chart_rows, errors, state_path)
    return {
        "index": str(index_path),
        "output_directory": str(output),
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
            f"<td><a href=\"{escape(item['filename'])}\">查看图表</a></td>"
            "</tr>"
        )
    if not rows:
        rows.append("<tr><td colspan=\"7\">没有生成图表</td></tr>")
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
<p class="note">黄色背景表示可做空状态区域；K线继续保持红涨绿跌。</p>
<p>状态来源：<code>{escape(str(states_csv))}</code></p>
<table>
<thead><tr><th>股票</th><th>名称</th><th>最新状态</th><th>做空区间</th><th>波峰区间</th><th>异常</th><th>图表</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>
{f'<h2>未生成项目</h2><ul>{error_items}</ul>' if errors else ''}
</body>
</html>
"""
    path = output_directory / "index.html"
    path.write_text(html, encoding="utf-8")
    return path


def _finish_interval(active: dict[str, Any]) -> StateInterval:
    return StateInterval(
        start_date=active["start_date"],
        end_date=active["end_date"],
        state=active["state"],
        state_name=active["state_name"],
        state_description=active["state_description"],
        cycle_id=active["cycle_id"],
        peak_number=active["peak_number"],
        trading_days=active["trading_days"],
    )


def _state_hover(row: dict[str, Any]) -> str:
    heat = _format_percent(row.get("heat"))
    return (
        f"{row.get('trade_date', '')}"
        f"<br>开盘：{_format_number(row.get('open'))}"
        f"<br>最高：{_format_number(row.get('high'))}"
        f"<br>最低：{_format_number(row.get('low'))}"
        f"<br>收盘：{_format_number(row.get('close'))}"
        f"<br>成交量：{_format_number(row.get('vol'), digits=0)}"
        f"<br>状态：{row.get('state_description', '')}"
        f"<br>枚举：{row.get('state_name', '')} ({row.get('state', '')})"
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
        "最高点判断失效"
        f"<br>日期：{row.get('trade_date', '')}"
        f"<br>收盘：{_format_number(row.get('close'))}"
        f"<br>原参考高点：{row.get('reference_high_date', '')} "
        f"{_format_number(row.get('reference_high_close'))}"
        f"<br>周期：{_number_or_zero(row.get('cycle_id'))}"
        f"<br>告警：{row.get('alert_code', '') or '-'}"
    )


def _latest_nonempty(values: pd.Series) -> str:
    nonempty = values.fillna("").astype(str).str.strip()
    nonempty = nonempty[nonempty.ne("")]
    return str(nonempty.iloc[-1]) if not nonempty.empty else ""


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
