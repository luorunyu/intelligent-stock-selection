"""Interactive K-line charts for strategy-four daily states."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any

import pandas as pd

from stock_selection.strategies.stock_peak_pullback_rebound.report import (
    DEFAULT_OUTPUT_ROOT,
)
from stock_selection.strategies.stock_peak_pullback_rebound.screen import (
    PeakPullbackReboundState,
)
from stock_selection.strategies.stock_short_high_state.visualization import (
    load_cached_stock_bars,
    merge_bars_and_states,
    read_chart_codes,
)


STRATEGY_DIRECTORY = Path(__file__).resolve().parent
DEFAULT_CODES_FILE = STRATEGY_DIRECTORY / "strategy_four_chart_codes.txt"
ACTIVE_FILL_COLOR = "rgba(255, 215, 0, 0.24)"
ENDED_FILL_COLOR = "rgba(70, 130, 180, 0.18)"
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
    "alert_code",
    "alert_message",
}


@dataclass(frozen=True)
class StateInterval:
    start_date: str
    end_date: str
    state: int
    state_name: str
    state_description: str
    cycle_id: int
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


def read_saved_states(path: str | Path) -> pd.DataFrame:
    """Read and validate a strategy-four states CSV."""
    data = pd.read_csv(
        Path(path),
        dtype={
            "ts_code": str,
            "trade_date": str,
            "state_name": str,
            "state_description": str,
            "alert_code": str,
            "alert_message": str,
        },
    )
    missing = sorted(REQUIRED_STATE_COLUMNS.difference(data.columns))
    if missing:
        raise ValueError(f"states CSV is missing columns: {', '.join(missing)}")
    result = data.copy()
    result["ts_code"] = result["ts_code"].fillna("").astype(str).str.strip()
    result["stock_name"] = result["stock_name"].fillna("").astype(str)
    result["trade_date"] = result["trade_date"].map(_normalise_date)
    result["close"] = pd.to_numeric(result["close"], errors="coerce")
    result["state"] = pd.to_numeric(result["state"], errors="coerce")
    result["cycle_id"] = (
        pd.to_numeric(result["cycle_id"], errors="coerce").fillna(0).astype(int)
    )
    for column in (
        "state_name",
        "state_description",
        "alert_code",
        "alert_message",
    ):
        result[column] = result[column].fillna("").astype(str)
    invalid = result[
        result["ts_code"].eq("")
        | result["trade_date"].eq("")
        | result["close"].isna()
    ]
    if not invalid.empty:
        raise ValueError("states CSV contains invalid code, date, or close values")
    if result.duplicated(["ts_code", "trade_date"], keep=False).any():
        raise ValueError("states CSV contains duplicate stock/date rows")
    return result.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)


def build_state_intervals(states: pd.DataFrame) -> list[StateInterval]:
    """Merge consecutive rows with the same strategy state and cycle."""
    intervals: list[StateInterval] = []
    active: dict[str, Any] | None = None
    for row in states.sort_values("trade_date").itertuples(index=False):
        if pd.isna(row.state):
            if active is not None:
                intervals.append(_finish_interval(active))
                active = None
            continue
        state = int(row.state)
        identity = (state, int(row.cycle_id))
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
                "cycle_id": int(row.cycle_id),
                "trading_days": 1,
            }
        else:
            active["end_date"] = str(row.trade_date)
            active["trading_days"] += 1
    if active is not None:
        intervals.append(_finish_interval(active))
    return intervals


def build_peak_pullback_rebound_figure(
    chart_data: pd.DataFrame,
    *,
    stock_code: str,
    stock_name: str = "",
    initial_bars: int | None = 250,
) -> Any:
    """Build an interactive K-line chart with strategy-four state regions."""
    go, make_subplots = _import_plotly()
    data = chart_data.sort_values("trade_date").reset_index(drop=True)
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.76, 0.24],
    )
    figure.add_trace(
        go.Candlestick(
            x=data["date"],
            open=data["open"],
            high=data["high"],
            low=data["low"],
            close=data["close"],
            increasing={
                "line": {"color": "#d84a3a"},
                "fillcolor": "#d84a3a",
            },
            decreasing={
                "line": {"color": "#169c72"},
                "fillcolor": "#169c72",
            },
            name="K线",
        ),
        row=1,
        col=1,
    )
    volume_colors = [
        "#d84a3a" if close >= open_price else "#169c72"
        for open_price, close in zip(data["open"], data["close"])
    ]
    figure.add_trace(
        go.Bar(
            x=data["date"],
            y=data["vol"],
            marker={"color": volume_colors},
            name="成交量",
        ),
        row=2,
        col=1,
    )

    intervals = build_state_intervals(data)
    for interval in intervals:
        fill_color = (
            ACTIVE_FILL_COLOR
            if interval.state == int(PeakPullbackReboundState.REBOUND_ACTIVE)
            else ENDED_FILL_COLOR
        )
        start = pd.to_datetime(interval.start_date, format="%Y%m%d")
        end = pd.to_datetime(interval.end_date, format="%Y%m%d") + pd.Timedelta(days=1)
        figure.add_vrect(
            x0=start,
            x1=end,
            fillcolor=fill_color,
            opacity=1,
            line_width=0,
            layer="below",
            row=1,
            col=1,
        )

    if intervals:
        starts = pd.DataFrame(
            [
                {
                    "trade_date": item.start_date,
                    "description": item.state_description,
                    "trading_days": item.trading_days,
                    "cycle_id": item.cycle_id,
                }
                for item in intervals
            ]
        )
        starts = starts.merge(
            data[["trade_date", "date", "high"]],
            on="trade_date",
            how="left",
        )
        figure.add_trace(
            go.Scatter(
                x=starts["date"],
                y=starts["high"] * 1.02,
                mode="markers",
                marker={
                    "symbol": "triangle-down",
                    "size": 12,
                    "color": [
                        "#d4a900"
                        if description == "高点回落后反弹中"
                        else "#4682b4"
                        for description in starts["description"]
                    ],
                },
                text=[
                    f"{row.description}<br>周期：{row.cycle_id}"
                    f"<br>持续：{row.trading_days}个交易日"
                    for row in starts.itertuples(index=False)
                ],
                hovertemplate="%{text}<extra></extra>",
                name="状态开始",
            ),
            row=1,
            col=1,
        )

    alert_rows = data[data["alert_code"].fillna("").ne("")]
    if not alert_rows.empty:
        figure.add_trace(
            go.Scatter(
                x=alert_rows["date"],
                y=alert_rows["high"] * 1.04,
                mode="markers",
                marker={"symbol": "x", "size": 13, "color": ALERT_COLOR},
                text=[
                    f"{row.trade_date}<br>{row.alert_message}"
                    for row in alert_rows.itertuples(index=False)
                ],
                hovertemplate="%{text}<extra></extra>",
                name="突破前高告警",
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
            "text": f"{title_name}{stock_code} 策略四状态检查",
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
        uirevision="stock-peak-pullback-rebound-state",
    )
    figure.update_yaxes(title_text="价格", row=1, col=1)
    figure.update_yaxes(title_text="成交量", rangemode="tozero", row=2, col=1)
    return figure


def write_peak_pullback_rebound_html(
    chart_data: pd.DataFrame,
    output_path: str | Path,
    *,
    stock_code: str,
    stock_name: str = "",
    initial_bars: int | None = 250,
) -> Path:
    """Write one self-contained strategy-four chart."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    figure = build_peak_pullback_rebound_figure(
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


def generate_peak_pullback_rebound_charts(
    *,
    states_csv: str | Path,
    codes_file: str | Path = DEFAULT_CODES_FILE,
    cache_root: str | Path | None = None,
    output_directory: str | Path | None = None,
    initial_bars: int | None = 250,
) -> dict[str, Any]:
    """Generate one strategy-four chart per requested stock."""
    state_path = Path(states_csv)
    states = read_saved_states(state_path)
    codes, input_errors = read_chart_codes(codes_file)
    if not codes:
        raise ValueError("chart code file contains no valid stock codes")
    selected_states = states[states["ts_code"].isin(codes)]
    if selected_states.empty:
        raise ValueError("none of the requested stock codes exist in the states CSV")
    bars_by_code = load_cached_stock_bars(
        codes,
        start_date=str(selected_states["trade_date"].min()),
        end_date=str(selected_states["trade_date"].max()),
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
            write_peak_pullback_rebound_html(
                chart_data,
                output / filename,
                stock_code=code,
                stock_name=stock_name,
                initial_bars=initial_bars,
            )
            intervals = build_state_intervals(stock_states)
            latest = stock_states.sort_values("trade_date").iloc[-1]
            chart_rows.append(
                {
                    "ts_code": code,
                    "stock_name": stock_name,
                    "latest_state": str(latest["state_description"] or "-"),
                    "active_intervals": sum(
                        item.state
                        == int(PeakPullbackReboundState.REBOUND_ACTIVE)
                        for item in intervals
                    ),
                    "ended_intervals": sum(
                        item.state
                        == int(PeakPullbackReboundState.REBOUND_ENDED)
                        for item in intervals
                    ),
                    "alerts": int(stock_states["alert_code"].ne("").sum()),
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
    rows = [
        "<tr>"
        f"<td>{escape(item['ts_code'])}</td>"
        f"<td>{escape(item['stock_name'])}</td>"
        f"<td>{escape(item['latest_state'])}</td>"
        f"<td>{item['active_intervals']}</td>"
        f"<td>{item['ended_intervals']}</td>"
        f"<td>{item['alerts']}</td>"
        f"<td><a href=\"{escape(item['filename'])}\">查看图表</a></td>"
        "</tr>"
        for item in chart_rows
    ]
    if not rows:
        rows.append("<tr><td colspan=\"7\">没有生成图表</td></tr>")
    error_items = "".join(f"<li>{escape(error)}</li>" for error in errors)
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>策略四图表入口</title>
<style>
body{{font-family:Arial,"Microsoft YaHei",sans-serif;margin:28px;color:#222}}
table{{border-collapse:collapse;width:100%;max-width:1100px}}
th,td{{border:1px solid #ddd;padding:9px;text-align:left}}
th{{background:#f4f6f8}}
a{{color:#1565c0}}
</style>
</head>
<body>
<h1>策略四：高点回落反弹状态</h1>
<p>状态文件：{escape(str(states_csv))}</p>
<p>黄色表示反弹中，蓝色表示反弹结束，紫色叉号表示突破前高告警。</p>
<table>
<thead><tr><th>股票</th><th>名称</th><th>最新状态</th><th>反弹区间</th><th>结束区间</th><th>告警</th><th>图表</th></tr></thead>
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
        trading_days=active["trading_days"],
    )


def _latest_nonempty(values: pd.Series) -> str:
    nonempty = values.fillna("").astype(str).str.strip()
    nonempty = nonempty[nonempty.ne("")]
    return str(nonempty.iloc[-1]) if not nonempty.empty else ""


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
            "plotly is required for strategy-four charts; install the viz extra"
        ) from exc
    return go, make_subplots
