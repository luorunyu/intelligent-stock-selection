"""K-line visualization for industry-index stabilization states."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from stock_selection.strategies.industry_index_stabilization.report import (
    DEFAULT_OUTPUT_ROOT,
)
from stock_selection.strategies.industry_index_stabilization.screen import (
    IndustryStabilizationState,
)


STRATEGY_DIRECTORY = Path(__file__).resolve().parent
DEFAULT_CODES_FILE = STRATEGY_DIRECTORY / "strategy_three_chart_codes.txt"
RISE_COLOR = "#d84a3a"
FALL_COLOR = "#169c72"
CANDIDATE_FILL_COLOR = "rgba(80, 145, 220, 0.14)"
STABILIZED_FILL_COLOR = "rgba(255, 193, 7, 0.22)"
STABILIZED_BORDER_COLOR = "rgba(190, 130, 0, 0.85)"

REQUIRED_STATE_COLUMNS = {
    "index_code",
    "name",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "vol",
    "state",
    "state_description",
    "episode_id",
    "state_days",
}


@dataclass(frozen=True)
class StabilizationInterval:
    """One continuous candidate or confirmed stabilization interval."""

    start_date: str
    end_date: str
    state: int
    state_description: str
    episode_id: int
    trading_days: int


def resolve_state_files(
    *,
    states_csv: str | Path | None = None,
    episodes_csv: str | Path | None = None,
    as_of_date: str | None = None,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
) -> tuple[Path, Path]:
    """Resolve explicit files, a dated output directory, or the latest output."""
    if states_csv is not None:
        state_path = Path(states_csv)
        if not state_path.exists():
            raise FileNotFoundError(f"states CSV does not exist: {state_path}")
    else:
        root = Path(output_root)
        target = _normalise_date(as_of_date)
        if as_of_date is not None and not target:
            raise ValueError("as_of_date must be a valid date")
        if target:
            state_path = (
                root / f"{target[:4]}-{target[4:6]}-{target[6:8]}" / "states.csv"
            )
        else:
            candidates = sorted(root.glob("????-??-??/states.csv"))
            if not candidates:
                raise FileNotFoundError(f"no states.csv found under {root}")
            state_path = candidates[-1]
        if not state_path.exists():
            raise FileNotFoundError(f"states CSV does not exist: {state_path}")

    episode_path = (
        Path(episodes_csv) if episodes_csv is not None else state_path.parent / "episodes.csv"
    )
    if not episode_path.exists():
        raise FileNotFoundError(f"episodes CSV does not exist: {episode_path}")
    return state_path, episode_path


def read_chart_codes(
    path: str | Path = DEFAULT_CODES_FILE,
) -> tuple[list[str], list[str]]:
    """Read one SW industry-index code per line."""
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
        code = normalise_industry_code(value)
        if not code:
            errors.append(f"line {line_number}: invalid industry code {value!r}")
            continue
        if code not in seen:
            seen.add(code)
            codes.append(code)
    return codes, errors


def normalise_industry_code(value: str) -> str:
    """Normalize six-digit or Tushare SW industry-index codes."""
    text = str(value).strip().upper()
    if text.endswith(".SI") and len(text) == 9 and text[:6].isdigit():
        return text
    if len(text) == 6 and text.isdigit():
        return f"{text}.SI"
    return ""


def read_saved_states(path: str | Path) -> pd.DataFrame:
    """Read and validate a strategy-three states CSV."""
    data = pd.read_csv(
        Path(path),
        dtype={"index_code": str, "trade_date": str, "state_name": str},
    )
    missing = sorted(REQUIRED_STATE_COLUMNS.difference(data.columns))
    if missing:
        raise ValueError(f"states CSV is missing columns: {', '.join(missing)}")
    result = data.copy()
    result["index_code"] = result["index_code"].fillna("").astype(str).str.strip()
    result["trade_date"] = result["trade_date"].map(_normalise_date)
    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "vol",
        "state",
        "episode_id",
        "state_days",
    ]
    for column in numeric_columns:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    invalid = result[
        result["index_code"].eq("")
        | result["trade_date"].eq("")
        | result[["open", "high", "low", "close", "state"]].isna().any(axis=1)
    ]
    if not invalid.empty:
        raise ValueError("states CSV contains invalid code, date, OHLC, or state values")
    if result.duplicated(["index_code", "trade_date"], keep=False).any():
        raise ValueError("states CSV contains duplicate industry/date rows")
    result["state"] = result["state"].astype(int)
    result["episode_id"] = result["episode_id"].fillna(0).astype(int)
    result["state_days"] = result["state_days"].fillna(0).astype(int)
    return result.sort_values(["index_code", "trade_date"]).reset_index(drop=True)


def read_saved_episodes(path: str | Path) -> pd.DataFrame:
    """Read saved stabilization episodes."""
    source = Path(path)
    if source.stat().st_size == 0:
        return pd.DataFrame()
    data = pd.read_csv(
        source,
        dtype={
            "index_code": str,
            "signal_date": str,
            "stable_start_date": str,
            "stable_end_date": str,
            "target_hit_date": str,
        },
    )
    for column in (
        "signal_date",
        "stable_start_date",
        "stable_end_date",
        "target_hit_date",
    ):
        if column in data.columns:
            data[column] = data[column].map(_normalise_date)
    return data


def build_stabilization_state_intervals(
    states: pd.DataFrame,
) -> list[StabilizationInterval]:
    """Merge consecutive rows with the same visible state and episode."""
    visible_states = {
        int(IndustryStabilizationState.STABILIZING_CANDIDATE),
        int(IndustryStabilizationState.STABILIZED),
    }
    intervals: list[StabilizationInterval] = []
    active: dict[str, Any] | None = None
    for row in states.sort_values("trade_date").itertuples(index=False):
        state = int(row.state)
        identity = (state, int(row.episode_id))
        if state not in visible_states:
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
                "state_description": str(row.state_description),
                "episode_id": identity[1],
                "trading_days": 1,
            }
        else:
            active["end_date"] = str(row.trade_date)
            active["trading_days"] += 1
    if active is not None:
        intervals.append(_finish_interval(active))
    return intervals


def build_industry_stabilization_figure(
    chart_data: pd.DataFrame,
    episodes: pd.DataFrame | None = None,
    *,
    index_code: str,
    index_name: str = "",
    initial_bars: int | None = 250,
) -> Any:
    """Build an interactive K-line chart with stabilization-state regions."""
    go, make_subplots = _import_plotly()
    data = chart_data.sort_values("trade_date").reset_index(drop=True).copy()
    if data.empty:
        raise ValueError("chart_data must not be empty")
    data["date"] = pd.to_datetime(data["trade_date"], format="%Y%m%d")
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
        ),
        row=2,
        col=1,
    )

    for interval in build_stabilization_state_intervals(data):
        confirmed = interval.state == int(IndustryStabilizationState.STABILIZED)
        figure.add_vrect(
            x0=pd.to_datetime(interval.start_date) - pd.Timedelta(hours=12),
            x1=pd.to_datetime(interval.end_date) + pd.Timedelta(hours=12),
            fillcolor=(
                STABILIZED_FILL_COLOR if confirmed else CANDIDATE_FILL_COLOR
            ),
            line_color=STABILIZED_BORDER_COLOR if confirmed else CANDIDATE_FILL_COLOR,
            line_width=1,
            layer="below",
            row="all",
            col=1,
        )

    selected_episodes = episodes if episodes is not None else pd.DataFrame()
    if not selected_episodes.empty:
        for episode in selected_episodes.to_dict("records"):
            start = _date_value(episode.get("stable_start_date"))
            end = _date_value(episode.get("stable_end_date"))
            if start is not None and end is not None:
                for value in (
                    episode.get("stable_price_min"),
                    episode.get("stable_price_max"),
                ):
                    if value is not None and not pd.isna(value):
                        figure.add_shape(
                            type="line",
                            x0=start,
                            x1=end,
                            y0=float(value),
                            y1=float(value),
                            xref="x",
                            yref="y",
                            line={"color": STABILIZED_BORDER_COLOR, "dash": "dot"},
                        )

        signal_rows = _marker_rows(data, selected_episodes, "signal_date")
        if not signal_rows.empty:
            figure.add_trace(
                go.Scatter(
                    x=signal_rows["date"],
                    y=signal_rows["high"] * 1.02,
                    mode="markers",
                    marker={
                        "symbol": "triangle-down",
                        "size": 12,
                        "color": "#f0a000",
                    },
                    text=signal_rows["marker_text"],
                    hovertemplate="%{text}<extra></extra>",
                    name="企稳确认",
                ),
                row=1,
                col=1,
            )
        hit_rows = _marker_rows(data, selected_episodes, "target_hit_date")
        if not hit_rows.empty:
            figure.add_trace(
                go.Scatter(
                    x=hit_rows["date"],
                    y=hit_rows["high"] * 1.035,
                    mode="markers",
                    marker={"symbol": "star", "size": 12, "color": "#1f9d55"},
                    text=hit_rows["marker_text"],
                    hovertemplate="%{text}<extra></extra>",
                    name="目标达成",
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
        ),
        row=1,
        col=1,
    )
    title = f"{index_code} {index_name}".strip() + " - 策略三企稳状态"
    figure.update_layout(
        title=title,
        template="plotly_white",
        hovermode="x unified",
        xaxis_rangeslider_visible=False,
        legend={"orientation": "h", "y": 1.03, "x": 0},
        margin={"l": 55, "r": 25, "t": 75, "b": 45},
    )
    figure.update_xaxes(rangebreaks=[{"bounds": ["sat", "mon"]}])
    if initial_bars and len(data) > initial_bars:
        figure.update_xaxes(range=[dates.iloc[-initial_bars], dates.iloc[-1]])
    return figure


def generate_industry_stabilization_charts(
    *,
    states_csv: str | Path,
    episodes_csv: str | Path,
    codes_file: str | Path = DEFAULT_CODES_FILE,
    output_directory: str | Path | None = None,
    initial_bars: int | None = 250,
) -> dict[str, Any]:
    """Generate one HTML state chart per requested industry code."""
    state_path = Path(states_csv)
    states = read_saved_states(state_path)
    episodes = read_saved_episodes(episodes_csv)
    codes, errors = read_chart_codes(codes_file)
    if not codes:
        raise ValueError("chart code file contains no valid industry codes")
    output = (
        Path(output_directory)
        if output_directory is not None
        else state_path.parent / "charts"
    )
    output.mkdir(parents=True, exist_ok=True)

    charts: list[dict[str, Any]] = []
    for code in codes:
        industry_states = states[states["index_code"].eq(code)].copy()
        if industry_states.empty:
            errors.append(f"{code}: not found in states CSV")
            continue
        industry_episodes = (
            episodes[episodes["index_code"].eq(code)].copy()
            if not episodes.empty and "index_code" in episodes.columns
            else pd.DataFrame()
        )
        index_name = _latest_nonempty(industry_states["name"])
        filename = f"{code.replace('.', '_')}.html"
        figure = build_industry_stabilization_figure(
            industry_states,
            industry_episodes,
            index_code=code,
            index_name=index_name,
            initial_bars=initial_bars,
        )
        figure.write_html(output / filename, include_plotlyjs=True)
        intervals = build_stabilization_state_intervals(industry_states)
        latest = industry_states.sort_values("trade_date").iloc[-1]
        charts.append(
            {
                "index_code": code,
                "name": index_name,
                "latest_state": str(latest["state_description"]),
                "intervals": len(intervals),
                "confirmed_episodes": int(len(industry_episodes)),
                "filename": filename,
            }
        )
    return {
        "states_csv": str(state_path),
        "episodes_csv": str(episodes_csv),
        "output_directory": str(output),
        "charts": charts,
        "errors": errors,
    }


def _finish_interval(active: dict[str, Any]) -> StabilizationInterval:
    return StabilizationInterval(
        start_date=active["start_date"],
        end_date=active["end_date"],
        state=active["state"],
        state_description=active["state_description"],
        episode_id=active["episode_id"],
        trading_days=active["trading_days"],
    )


def _marker_rows(
    data: pd.DataFrame,
    episodes: pd.DataFrame,
    date_column: str,
) -> pd.DataFrame:
    if date_column not in episodes.columns:
        return pd.DataFrame()
    episode_rows = episodes[episodes[date_column].fillna("").astype(str).ne("")].copy()
    if episode_rows.empty:
        return pd.DataFrame()
    episode_rows["trade_date"] = episode_rows[date_column].map(_normalise_date)
    merged = episode_rows.merge(
        data[["trade_date", "date", "high"]],
        on="trade_date",
        how="inner",
    )
    if merged.empty:
        return merged
    merged["marker_text"] = merged.apply(
        lambda row: (
            f"区间：{row.get('stable_start_date', '-')} 至 "
            f"{row.get('stable_end_date', '-')}"
            f"<br>信号：{row.get('signal_date', '-')}"
            f"<br>验证：{row.get('validation_status', '-')}"
        ),
        axis=1,
    )
    return merged


def _state_hover(row: dict[str, Any]) -> str:
    range_value = row.get("stable_range_points")
    range_text = (
        "-"
        if range_value is None or pd.isna(range_value)
        else f"{float(range_value):.2f}"
    )
    return (
        f"{row['trade_date']}<br>状态：{row['state_description']}"
        f"<br>状态值：{int(row['state'])}"
        f"<br>区间编号：{int(row.get('episode_id') or 0)}"
        f"<br>状态天数：{int(row.get('state_days') or 0)}"
        f"<br>价格带宽度：{range_text}"
    )


def _date_value(value: Any) -> pd.Timestamp | None:
    date_text = _normalise_date(value)
    return pd.to_datetime(date_text, format="%Y%m%d") if date_text else None


def _latest_nonempty(values: pd.Series) -> str:
    nonempty = values[values.fillna("").astype(str).str.strip().ne("")]
    return str(nonempty.iloc[-1]).strip() if not nonempty.empty else ""


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
            "Plotly is required for charts; install the project viz dependencies"
        ) from exc
    return go, make_subplots
