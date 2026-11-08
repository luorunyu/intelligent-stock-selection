"""Visualize saved strategy-five states on interactive stock K-line charts."""

from __future__ import annotations

from dataclasses import dataclass
import json
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from stock_selection.strategies.stock_short_high_state.cost_zone import (
    COST_ZONE_COLUMNS, BREAKOUT_REASON_LABELS, COST_ZONE_STATUS_LABELS,
    validate_cost_zone_fields,
)
from stock_selection.strategies.stock_short_high_state.industry_state import (
    BLOCK_NAMES, INDUSTRY_COLUMNS, validate_industry_fields,
)

from stock_selection.data.market_cap import (
    format_market_cap,
    load_cached_market_caps,
    merge_market_caps,
)
from stock_selection.data.tushare_cache import DEFAULT_CACHE_ROOT
from stock_selection.data.tushare_chips import load_chip_net_payload
from stock_selection.strategies.stock_short_high_state.chip_detail import chip_detail_script
from stock_selection.strategies.stock_short_high_state.backtest import (
    PROFIT_REBOUND_EXIT,
    PROFIT_REBOUND_REENTRY,
    build_short_trade_backtest,
    sample_stock_trade_codes,
    sum_evaluated_returns_by_stock,
)
from stock_selection.strategies.stock_short_high_state.report import (
    DEFAULT_OUTPUT_ROOT,
)
from stock_selection.strategies.stock_short_high_state.screen import (
    BREAKOUT_BASELINE_RULE, PEAK_1_PULLBACK_SIGNAL,
    STATE_DESCRIPTIONS, StockShortState, apply_peak_1_entry_signals,
)


STRATEGY_DIRECTORY = Path(__file__).resolve().parent
DEFAULT_CODES_FILE = STRATEGY_DIRECTORY / "strategy_five_chart_codes.txt"
RUN_CODES_FILENAME = "chart_codes.txt"
RISE_COLOR = "#d84a3a"
FALL_COLOR = "#169c72"
SHORT_FILL_COLOR = "rgba(255, 215, 0, 0.22)"
SHORT_BORDER_COLOR = "rgba(205, 155, 0, 0.85)"
ALERT_COLOR = "#7b2cbf"
EXIT_REASON_LABELS = {
    "TROUGH_CONFIRM_EXIT": "波谷确认平仓",
    PROFIT_REBOUND_EXIT: "盈利后连续上涨平仓",
    "MAX_DRAWDOWN_STOP": "达到回撤止损阈值",
    "HIGH_INVALIDATION_EXIT": "突破原高点，原判断失效",
    "CYCLE_CHANGE_EXIT": "行情周期切换",
}


REQUIRED_STATE_COLUMNS = {
    *INDUSTRY_COLUMNS, *COST_ZONE_COLUMNS,
    "ts_code",
    "stock_name",
    "trade_date",
    "open",
    "high",
    "close",
    "vol",
    "lookback_days",
    "observed_state",
    "observed_state_name",
    "entry_signal",
    "entry_signal_type",
    "entry_pattern_start_date",
    "entry_reference_high_close",
    "candidate_pullback_pct",
    "min_breakout_gain_pct",
    "breakout_baseline_rule",
    "breakout_base_date",
    "breakout_base_close",
    "breakout_base_kind",
    "breakout_base_start_date",
    "breakout_base_days",
    "breakout_gain_pct",
    "entry_breakout_qualified",
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
    "trough_confirm_date",
    "trough_ma_days",
    "trough_ma_close",
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
    """Resolve saved run states, including outputs from the legacy layout."""
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
        date_directory = root / f"{target[:4]}-{target[4:6]}-{target[6:8]}"
        candidates = list(date_directory.glob("charts_*/states.csv"))
        legacy_path = date_directory / "states.csv"
        if legacy_path.is_file():
            candidates.append(legacy_path)
    else:
        candidates = list(root.glob("????-??-??/charts_*/states.csv"))
        candidates.extend(root.glob("????-??-??/states.csv"))
    candidates = [path for path in candidates if path.is_file()]
    if not candidates:
        search_directory = date_directory if target else root
        raise FileNotFoundError(f"no states.csv found under {search_directory}")
    return max(candidates, key=lambda path: (
        path.relative_to(root).parts[0], path.stat().st_mtime_ns, str(path),
    ))


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
            "entry_signal_type": str,
            "entry_pattern_start_date": str,
            "breakout_base_date": str,
            "breakout_base_start_date": str,
            "cost_zone_freeze_date": str,
            "cost_zone_high_date": str,
            "industry_trade_date": str,
            "industry_code": str,
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
            "旧文件缺少成本区突破涨幅过滤、均线确认波谷的字段或当日实际状态。"
        )
    result = data.copy()
    if not result["breakout_baseline_rule"].eq(BREAKOUT_BASELINE_RULE).all():
        raise ValueError("states.csv 未采用历史峰值成本区冻结的突破涨幅过滤规则，请重新运行策略五 CLI")
    result["ts_code"] = result["ts_code"].fillna("").astype(str).str.strip()
    result["trade_date"] = result["trade_date"].map(_normalise_date)
    for column in ("entry_signal_type", "entry_pattern_start_date", "breakout_base_date", "breakout_base_start_date", "breakout_base_kind"):
        result[column] = result[column].fillna("").astype(str)
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
    result = validate_cost_zone_fields(validate_industry_fields(result))
    return apply_peak_1_entry_signals(
        result.sort_values(["ts_code", "trade_date"]).reset_index(drop=True),
    )


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
    normalized_bars = normalized_bars.rename(columns={
        "open": "bar_open", "high": "bar_high", "close": "bar_close", "vol": "bar_vol",
    })
    # Preserve saved state fields alongside the refreshed OHLCV chart bars.
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
    for saved_column, bar_column in (("state_close", "bar_close"), ("open", "bar_open"), ("high", "bar_high")):
        matches = np.isclose(
            merged[saved_column].astype(float), merged[bar_column].astype(float),
            rtol=0.0, atol=close_tolerance,
        )
        if not bool(matches.all()):
            mismatch = merged.loc[~matches].iloc[0]
            raise ValueError(
                f"state {saved_column} and OHLC {bar_column} differ on {mismatch['trade_date']}: "
                f"{mismatch[saved_column]} != {mismatch[bar_column]}"
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
    industry_blocked = [event for event in trade_backtest.get("industry_blocked_entries", []) if event["ts_code"] == stock_code]
    has_industry = "industry_filter_enabled" in data and (
        bool(data["industry_filter_enabled"].iloc[0])
        or pd.to_numeric(data["industry_close"], errors="coerce").notna().any()
    )
    subplot_rows = 3 if has_industry else 2
    dates = data["date"]
    volume_colors = [
        RISE_COLOR if close >= open_ else FALL_COLOR
        for open_, close in zip(data["open"], data["close"])
    ]
    figure = make_subplots(
        rows=subplot_rows,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.56, 0.16, 0.28] if has_industry else [0.78, 0.22],
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
            hoverinfo="none",
        ),
        row=1,
        col=1,
    )
    ma_days = int(data["trough_ma_days"].iloc[0])
    figure.add_trace(
        go.Scatter(
            x=dates,
            y=data["trough_ma_close"],
            mode="lines",
            name=f"MA{ma_days}",
            line={"color": "#c77d16", "width": 1.5},
            connectgaps=False,
            hovertemplate=f"MA{ma_days}：%{{y:.2f}}<extra></extra>",
        ),
        row=1,
        col=1,
    )

    _add_cost_zone_traces(figure, data)

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

    if has_industry:
        figure.add_trace(go.Scatter(
            x=dates, y=data["industry_close"], mode="lines+markers", name="二级行业收盘价",
            line={"color": "#3b82f6", "width": 1.5}, connectgaps=False,
            marker={"size": 5, "color": data["industry_state"].map({"UP": RISE_COLOR, "DOWN": FALL_COLOR}).fillna("#94a3b8")},
            text=[_industry_hover(row) for row in data.to_dict("records")],
            hovertemplate="%{text}<extra></extra>",
        ), row=3, col=1)
        declining = data["industry_state"].eq("DOWN") & data["industry_data_status"].eq("OK")
        for _, segment in data.groupby(declining.ne(declining.shift()).cumsum()):
            if not declining.loc[segment.index[0]]:
                continue
            figure.add_vrect(x0=segment["date"].iloc[0] - pd.Timedelta(hours=12),
                             x1=segment["date"].iloc[-1] + pd.Timedelta(hours=12),
                             fillcolor="rgba(22,156,114,0.14)", line_width=0, layer="below", row=3, col=1)
        if industry_blocked:
            blocked_rows = data[data["trade_date"].isin([event["trade_date"] for event in industry_blocked])]
            figure.add_trace(go.Scatter(
                x=blocked_rows["date"], y=blocked_rows["close"], mode="markers", name="行业拦截开仓",
                marker={"symbol": "x", "size": 11, "color": "#64748b"},
                text=[_industry_hover(row) for row in blocked_rows.to_dict("records")],
                hovertemplate="被行业过滤拦截，不延后补开<br>%{text}<extra></extra>",
            ), row=1, col=1)

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

    skipped_events: dict[str, list[str]] = {}
    for trade in stock_trades:
        for skipped in trade.get("skipped_entries", []):
            reason = (
                "平仓当日不重新开仓"
                if skipped["reason"] == "EXIT_DAY" else
                f"已有持仓，开仓于 {skipped['blocking_entry_date']}"
            )
            skipped_events.setdefault(str(skipped["trade_date"]), []).append(
                f"回调信号已跳过：{reason}"
            )
    # Add daily details before event markers so opening/closing markers remain
    # hoverable when they share the same date and price with the daily close.
    figure.add_trace(
        go.Scatter(
            x=dates,
            y=data["close"],
            mode="markers",
            marker={"size": 12, "color": "rgba(0,0,0,0)"},
            text=[
                _state_hover(row)
                + "".join(
                    f"<br>{event}"
                    for event in skipped_events.get(str(row["trade_date"]), [])
                )
                for row in data.to_dict("records")
            ],
            hovertemplate="%{text}<extra></extra>",
            name="状态详情",
            showlegend=False,
        ),
        row=1,
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
                text=[_trade_hover(trade) for trade in closed_trades],
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

    for row in range(1, subplot_rows + 1):
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
        for row in range(1, subplot_rows + 1):
            figure.update_xaxes(range=initial_range, row=row, col=1)

    title_name = f"{stock_name} " if stock_name else ""
    figure.update_layout(
        title={
            "text": f"{title_name}{stock_code} 策略五状态检查"
            f"<br><sup>{latest_market_cap}</sup>",
            "x": 0.01,
            "xanchor": "left",
        },
        height=980 if has_industry else 760,
        template="plotly_white",
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        margin={"l": 72, "r": 32, "t": 92, "b": 48},
        hovermode="closest",
        hoverlabel={"align": "left", "namelength": 0},
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
    if has_industry:
        figure.update_yaxes(title_text="二级行业指数", row=3, col=1)
    return figure


def write_stock_short_state_html(
    chart_data: pd.DataFrame,
    output_path: str | Path,
    *,
    stock_code: str,
    stock_name: str = "",
    initial_bars: int | None = 250,
    trade_backtest: dict[str, Any] | None = None,
    cache_root: str | Path | None = None,
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
    dates = chart_data.sort_values("trade_date")["trade_date"].astype(str).tolist()
    chips = load_chip_net_payload(stock_code, dates, cache_root=cache_root)
    bars = [{"date": str(row["trade_date"]), "close": float(row["close"])}
            for row in chart_data.sort_values("trade_date").to_dict("records")]
    figure.write_html(
        path,
        include_plotlyjs="directory",
        full_html=True,
        post_script=chip_detail_script(chips, bars) if chips else None,
        config={
            "displaylogo": False,
            "responsive": True,
            "scrollZoom": True,
            "modeBarButtonsToRemove": ["lasso2d", "select2d"],
        },
    )
    return path


def _add_cost_zone_traces(figure: Any, data: pd.DataFrame) -> None:
    import plotly.graph_objects as go

    # A gap at every changed snapshot avoids joining different cost regions.
    x: list[Any] = []
    lows: list[Any] = []
    highs: list[Any] = []
    means: list[Any] = []
    thresholds: list[Any] = []
    points: dict[str, dict[str, Any]] = {}
    previous = None
    for row in data.to_dict("records"):
        key = (row.get("cost_peak_points"), row.get("cost_zone_freeze_date"))
        if previous is not None and key != previous:
            x.append(None)
            for values in (lows, highs, means, thresholds):
                values.append(None)
        previous = key
        x.append(row["date"])
        lows.append(row.get("cost_zone_low"))
        highs.append(row.get("cost_zone_high"))
        baseline = row.get("breakout_base_close")
        means.append(baseline)
        thresholds.append(baseline * (1 + row["min_breakout_gain_pct"]))
        for point in json.loads(row.get("cost_peak_points") or "[]"):
            points[point["trade_date"]] = point
    for name, values, color, dash, fill in (
        ("成本区下沿", lows, "rgba(100,116,139,0.5)", "dot", None),
        ("成本区上沿", highs, "rgba(100,116,139,0.5)", "dot", "tonexty"),
        ("历史峰值均值", means, "#6366f1", "dash", None),
        ("开仓涨幅门槛线", thresholds, "#be185d", "dash", None),
    ):
        figure.add_trace(go.Scatter(
            x=x, y=values, mode="lines", name=name,
            line={"color": color, "width": 1, "dash": dash}, connectgaps=False,
            fill=fill, fillcolor="rgba(100,116,139,0.07)",
            hovertemplate=name + "：%{y:.2f}<extra></extra>",
        ), row=1, col=1)
    if points:
        ordered = sorted(points.values(), key=lambda point: point["trade_date"])
        figure.add_trace(go.Scatter(
            x=pd.to_datetime([point["trade_date"] for point in ordered], format="%Y%m%d"),
            y=[point["close"] for point in ordered], mode="markers", name="成本区历史峰值",
            marker={"symbol": "diamond-open", "size": 8, "color": "#6366f1"},
            text=[f"峰值 {point['trade_date']} · {point['close']:.2f}<br>确认日 {point['confirm_date']}，下一交易日起可用于基准" for point in ordered],
            hovertemplate="%{text}<extra></extra>",
        ), row=1, col=1)


def generate_stock_short_state_charts(
    *,
    states_csv: str | Path,
    codes_file: str | Path | None = None,
    stock_codes: Iterable[str] | None = None,
    cache_root: str | Path | None = None,
    output_directory: str | Path | None = None,
    initial_bars: int | None = 250,
) -> dict[str, Any]:
    """Use this run's code list, or sample its backtest; honor explicit lists."""
    state_path = Path(states_csv)
    states = read_saved_states(state_path)
    if states.empty:
        raise ValueError("states CSV contains no stocks to chart")
    automatic_codes = codes_file is None and stock_codes is None
    codes_source = "stock_codes" if stock_codes is not None else "explicit_file"
    full_backtest = None
    if automatic_codes:
        run_codes_file = state_path.parent / RUN_CODES_FILENAME
        sampled_files = sorted(state_path.parent.glob("sampled_chart_codes_*.txt"))
        if run_codes_file.is_file():
            codes_file = run_codes_file
            codes_source = "run_file"
        elif sampled_files:
            codes_file = sampled_files[-1]
            codes_source = "run_sample_snapshot"
        else:
            full_backtest = build_short_trade_backtest(
                states, as_of_date=str(states["trade_date"].max()),
            )
            codes = sample_stock_trade_codes(full_backtest["trades"])
            input_errors = []
            codes_source = "current_run_backtest"
    if full_backtest is None:
        codes, input_errors = _resolve_chart_codes(codes_file, stock_codes)
    if not codes:
        if input_errors or stock_codes is not None:
            raise ValueError("chart code file contains no valid stock codes")
        codes = sorted(states["ts_code"].unique().tolist())
        if not codes:
            raise ValueError("states CSV contains no stocks to chart")
        if full_backtest is not None:
            codes_source = "current_run_all_stocks"
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
    if output_directory is not None:
        # An explicit output path is an opt-in to updating that directory.
        output = Path(output_directory)
        output.mkdir(parents=True, exist_ok=True)
    else:
        # A chart render is a separate artifact from the strategy run.  Never
        # write HTML next to the source states.csv by default: users often
        # render the same saved run repeatedly while comparing presentation
        # changes, and each render must remain available for comparison.
        output_root = (
            state_path.parent.parent
            if state_path.parent.name.startswith("charts_")
            else state_path.parent
        )
        output = output_root / (
            f"charts_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        )
        output.mkdir(parents=True, exist_ok=False)
    if automatic_codes and full_backtest is not None:
        codes_file = output / RUN_CODES_FILENAME
        codes_file.write_text("\n".join(codes) + "\n", encoding="utf-8")
    positive_output = output / "positive"
    negative_output = output / "negative"
    unclassified_output = output / "unclassified"
    positive_output.mkdir(parents=True, exist_ok=True)
    negative_output.mkdir(parents=True, exist_ok=True)
    unclassified_output.mkdir(parents=True, exist_ok=True)

    chart_rows: list[dict[str, Any]] = []
    category_rows = {"positive": [], "negative": [], "unclassified": []}
    errors = list(input_errors)
    backtest = full_backtest if full_backtest is not None else build_short_trade_backtest(
        selected_states, as_of_date=end_date,
    )
    # Default sampling calculated the whole run; chart only the chosen stocks.
    selected_codes = set(codes)
    backtest = {
        **backtest,
        "trades": [trade for trade in backtest["trades"] if trade["ts_code"] in selected_codes],
    }
    trades_by_code: dict[str, list[dict[str, Any]]] = {}
    for trade in backtest.get("trades", []):
        trades_by_code.setdefault(str(trade["ts_code"]), []).append(trade)
    returns_by_stock = sum_evaluated_returns_by_stock(backtest.get("trades", []))
    positive_codes = sorted(
        (code for code in codes if returns_by_stock.get(code, 0.0) > 0),
        key=lambda code: (-returns_by_stock[code], code),
    )
    positive_code_set = set(positive_codes)
    # Use the same descending gain order for generation and both HTML indexes.
    codes = [code for code in codes if code not in positive_code_set] + positive_codes

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
            stock_backtest = {"metadata": backtest["metadata"], "trades": stock_trades,
                              "industry_blocked_entries": [event for event in backtest.get("industry_blocked_entries", []) if event["ts_code"] == code]}
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
                cache_root=cache_root,
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
        "states_csv": str(state_path),
        "codes_source": codes_source,
        "codes_file": str(codes_file) if codes_file is not None else None,
        "requested_stocks": len(codes),
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
            f"<td>{_format_percent(item.get('classified_return_pct'))}</td>"
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
        rows.append("<tr><td colspan=\"10\">没有生成图表</td></tr>")
    error_items = "".join(f"<li>{escape(error)}</li>" for error in errors)
    report_links = " · ".join(
        f'<a href="{escape(path.name)}">{escape(path.stem)}</a>'
        for path in sorted(output_directory.glob("short_trade_backtest*.html"))
    )
    if (output_directory / "shortable_stocks.html").is_file():
        stock_list_link = '<a href="shortable_stocks.html">可做空股票状态列表</a>'
        report_links = f"{report_links} · {stock_list_link}" if report_links else stock_list_link
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
<p class="note">黄框表示每笔实际持仓，同一股票不重叠，包含波谷确认平仓、盈利后连续上涨平仓、止损和失效强平；持仓中延伸至最后数据日。黄色倒三角标记实际开仓，蓝色正三角标记平仓，紫色叉号标记策略异常；候选低点后首次收盘站上波谷确认均线时确认波谷，最低点是事后回标，不能在最低点平仓。独立盈利平仓要求平仓当天做空浮盈严格超过5%，且开仓后连续3个交易日收盘价高于前一交易日；下跌或平盘清零。此类平仓后，后续收盘先从该次实际平仓价上涨至少10%，再回到该平仓价或以下时可重开。开仓信号、连续上涨天数、重开关联平仓价及上涨达标日、波谷确认日和平仓原因通过鼠标悬停查看。绿K为收盘低于开盘，候选回调仅按收盘价跌幅判断。</p>
<p>状态来源：<code>{escape(str(states_csv))}</code></p>
{f'<p>本次报告：{report_links}</p>' if report_links else ''}
<p>正收益股票按汇总收益率从高到低排列，同收益按股票代码排序。汇总收益沿用逐股交易收益相加的口径，包含持仓中的浮动收益。</p>
<table>
<thead><tr><th>股票</th><th>名称</th><th>汇总收益率</th><th>最新状态</th><th>回测交易</th><th>其中波峰交易</th><th>异常</th><th>总市值</th><th>流通市值</th><th>图表</th></tr></thead>
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
        for trade in backtest["trades"]
        if trade["status"] == "INVALIDATED"
        or trade.get("status_reason") in {"HIGH_INVALIDATION_EXIT", "CYCLE_CHANGE_EXIT"}
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
    lines = [
        f"<b>做空交易 · {status}</b>",
        f"开仓：{trade['entry_date']} · {_format_number(trade['entry_close'])}",
        f"{endpoint}：{trade['evaluation_date']} · {_format_number(trade['evaluation_close'])}",
        f"收益：{_format_percent(trade['mark_return_pct'])} · 持有 {trade['holding_trade_days']} 个交易日",
        f"最大回撤：{_format_percent(trade['max_drawdown_pct'])}",
    ]
    if trade.get("entry_source") == PEAK_1_PULLBACK_SIGNAL:
        lines.append("开仓来源：第一波反弹接近确认高点后回落")
        lines.append(
            f"第一波峰最高收盘：{trade.get('entry_peak_1_high_date') or '-'} · {_format_number(trade.get('entry_peak_1_high_close'))}"
        )
        lines.append(f"开仓时距第一波峰高点回落：{_format_percent(trade.get('entry_peak_1_drawdown_pct'))}")
    elif trade.get("entry_source") == PROFIT_REBOUND_REENTRY:
        lines.extend([
            f"开仓来源：盈利平仓后上涨再回落重开（{PROFIT_REBOUND_REENTRY}）",
            f"关联原开仓日：{trade.get('entry_reentry_origin_entry_date') or '-'}",
            f"关联盈利平仓：{trade.get('entry_reentry_exit_date') or '-'} · {_format_number(trade.get('entry_reentry_exit_close'))}",
            f"平仓后上涨门槛：{_format_number(trade.get('entry_reentry_rise_threshold_close'))} · 上涨达标日：{trade.get('entry_reentry_rise_qualified_date') or '-'}",
            f"平仓后最高收盘：{trade.get('entry_reentry_high_date') or '-'} · {_format_number(trade.get('entry_reentry_high_close'))}",
        ])
    reason = trade.get("status_reason")
    if reason in EXIT_REASON_LABELS:
        lines.append(
            f"平仓原因：{EXIT_REASON_LABELS[reason]}"
            f"（{reason}）"
        )
    if reason == "TROUGH_CONFIRM_EXIT":
        lines.append(
            f"波谷确认日：{trade.get('evaluation_trough_confirm_date') or '-'}"
            f" · MA{trade['trough_ma_days']}：{_format_number(trade.get('evaluation_trough_ma_close'))}"
        )
    elif reason == PROFIT_REBOUND_EXIT:
        lines.append(f"连续收盘上涨：{_number_or_zero(trade.get('evaluation_consecutive_up_days'))} 天")
    if trade.get("stop_trigger_close") is not None:
        lines.extend([
            f"止损触发日收盘：{_format_number(trade['stop_trigger_close'])}",
            "平仓价按回测止损阈值模拟成交",
        ])
    lines.append(
        f"开仓判定高点：{trade.get('entry_pattern_start_date') or '-'}"
        f" · {_format_number(trade.get('entry_reference_high_close'))}"
    )
    if trade.get("entry_source") != PROFIT_REBOUND_REENTRY and _number_or_zero(trade.get("entry_cost_peak_count")):
        lines.extend([
            f"成本区：{_format_number(trade.get('entry_cost_zone_low'))} ～ {_format_number(trade.get('entry_cost_zone_high'))}",
            f"历史峰值均值：{_format_number(trade.get('entry_breakout_base_close'))}",
            f"冻结日期：{trade.get('entry_cost_zone_freeze_date') or '-'}",
            f"开仓时突破涨幅：{_format_percent(trade.get('entry_breakout_gain_pct'))}",
            f"历史峰值（{_number_or_zero(trade.get('entry_cost_peak_count'))}个）：<br>"
            + _cost_peaks_hover(trade.get("entry_cost_peak_points")),
        ])
    return "<br>".join(lines)


def _cost_peaks_hover(value: Any) -> str:
    """Keep chart peak lists compact; full confirmation evidence stays in reports."""
    try:
        points = json.loads(value) if isinstance(value, str) else value
        labels = [
            f"{escape(str(point['trade_date']))} · {float(point['close']):.2f}"
            for point in points
        ]
    except (TypeError, ValueError, KeyError):
        return "-"
    return "<br>".join(
        "；".join(labels[start:start + 3])
        for start in range(0, len(labels), 3)
    ) or "-"


def _industry_hover(row: dict[str, Any]) -> str:
    reason = row.get("entry_blocked_reason") or ""
    return (
        f"二级行业：{escape(str(row.get('industry_name') or '-'))} {escape(str(row.get('industry_code') or ''))}"
        f"<br>行业状态：{row.get('industry_state_name') or '-'} · {row.get('industry_data_status') or '-'}"
        f"<br>行业收盘：{_format_number(row.get('industry_close'))}"
        f"<br>窗口最高 / 最低收盘：{_format_number(row.get('industry_high_close'))} / {_format_number(row.get('industry_low_close'))}"
        f"<br>行业回撤 / 反弹：{_format_percent(row.get('industry_drawdown_pct'))} / {_format_percent(row.get('industry_rebound_pct'))}"
        f"<br>行业过滤：{'关闭' if not row.get('industry_filter_enabled') else ('通过' if row.get('entry_industry_qualified') else BLOCK_NAMES.get(reason, reason))}"
    )


def _state_hover(row: dict[str, Any]) -> str:
    state = _number_or_zero(row.get("state"))
    observed = _number_or_zero(row.get("observed_state", state))
    description = STATE_DESCRIPTIONS.get(observed, row.get("state_description", ""))
    if observed == state:
        description = row.get("state_description") or description
    lines = [
        f"<b>{row.get('trade_date', '')}</b>",
        f"开盘 / 收盘：{_format_number(row.get('open'))} / {_format_number(row.get('close'))}",
        f"最高 / 最低：{_format_number(row.get('high'))} / {_format_number(row.get('low'))}",
        f"当日状态：{description}",
    ]
    if state != observed:
        lines.append(f"事后标记：{row.get('state_description', '')}")
    if _number_or_zero(row.get("entry_signal")) == int(StockShortState.HIGH_CANDIDATE_PULLBACK):
        lines.append("回调信号：已触发，实际开仓见黄色标记")
    elif row.get("entry_signal_type") == PEAK_1_PULLBACK_SIGNAL:
        lines.append("双峰信号：第一波反弹达到确认高点90%后回落至少10%，实际开仓见黄色标记")
    if _number_or_zero(row.get("peak_1_high_close")) > 0:
        lines.append(
            f"第一波峰最高收盘：{row.get('peak_1_high_date') or '-'} · {_format_number(row.get('peak_1_high_close'))}"
            f" · 距高点回落 {_format_percent(row.get('peak_1_drawdown_pct'))}"
        )
    if _normalise_date(row.get("reference_high_date")):
        lines.append(
            f"本轮高点：{row['reference_high_date']}"
            f" · {_format_number(row.get('reference_high_close'))}"
        )
    if _normalise_date(row.get("trough_confirm_date")):
        lines.append(f"波谷确认日：{row['trough_confirm_date']}")
    lines.append(f"突破过滤：{BREAKOUT_REASON_LABELS.get(row.get('entry_breakout_reason'), '-')}")
    if _number_or_zero(row.get("cost_peak_count")):
        lines.extend([
            f"成本区：{_format_number(row.get('cost_zone_low'))} ～ {_format_number(row.get('cost_zone_high'))}",
            f"历史峰值均值：{_format_number(row.get('breakout_base_close'))}",
            f"成本区状态：{COST_ZONE_STATUS_LABELS.get(row.get('cost_zone_status'), '-')}",
        ])
        if _normalise_date(row.get("cost_zone_freeze_date")):
            lines.extend([
                f"冻结日期：{row['cost_zone_freeze_date']}",
                f"突破涨幅 / 门槛：{_format_percent(row.get('breakout_gain_pct'))} / {_format_percent(row.get('min_breakout_gain_pct'))}",
            ])
        lines.append(
            f"历史峰值（{_number_or_zero(row.get('cost_peak_count'))}个）：<br>"
            + _cost_peaks_hover(row.get("cost_peak_points"))
        )
    return "<br>".join(lines)


def _alert_hover(row: dict[str, Any]) -> str:
    return (
        "<b>策略异常：突破已确认高点</b>"
        f"<br>日期：{row.get('trade_date', '')}"
        f"<br>收盘：{_format_number(row.get('close'))}"
        f"<br>原参考高点：{row.get('reference_high_date', '')} "
        f"{_format_number(row.get('reference_high_close'))}"
    )


def _latest_nonempty(values: pd.Series) -> str:
    nonempty = values.fillna("").astype(str).str.strip()
    nonempty = nonempty[nonempty.ne("")]
    return str(nonempty.iloc[-1]) if not nonempty.empty else ""


def _resolve_chart_codes(
    codes_file: str | Path | None,
    stock_codes: Iterable[str] | None,
) -> tuple[list[str], list[str]]:
    if stock_codes is None:
        if codes_file is None:
            raise ValueError("a chart codes file or stock_codes is required")
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
