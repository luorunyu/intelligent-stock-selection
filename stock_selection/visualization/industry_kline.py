"""Render one industry index and its largest constituents in a shared timeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from stock_selection.data.trading_calendar import cached_trade_dates
from stock_selection.data.tushare_cache import DEFAULT_CACHE_ROOT, read_dataset


DATE_ALIASES = ("trade_date", "date")
VOLUME_ALIASES = ("vol", "volume")
OHLC_COLUMNS = ("open", "high", "low", "close")
RISE_COLOR = "#d84a3a"
FALL_COLOR = "#169c72"


@dataclass(frozen=True)
class KlinePanel:
    """Normalized inputs for one equal-height price/volume panel."""

    code: str
    name: str
    bars: pd.DataFrame
    circ_mv: float | None = None


def build_industry_kline_figure(
    index_bars: pd.DataFrame,
    stock_bars: Mapping[str, pd.DataFrame],
    *,
    index_code: str | None = None,
    index_name: str | None = None,
    stock_info: pd.DataFrame | None = None,
    title: str | None = None,
    panel_height: int = 300,
    initial_bars: int | None = 80,
) -> Any:
    """Build one Plotly figure with equal panels and a shared date axis.

    ``stock_bars`` maps security code to a daily OHLCV DataFrame. ``stock_info``
    may contain ``ts_code``/``symbol``, ``name`` and ``circ_mv``; constituents
    are ordered by ``circ_mv`` descending. Each logical panel has a 3:1 split
    between candlesticks and volume, while all panels have the same total height.
    """

    if panel_height < 180:
        raise ValueError("panel_height must be at least 180 pixels")
    if not stock_bars:
        raise ValueError("stock_bars must contain at least one stock")

    go, make_subplots = _import_plotly()
    resolved_index_code = index_code or _first_text(index_bars, ("ts_code", "symbol")) or "INDEX"
    resolved_index_name = index_name or _first_text(index_bars, ("name",)) or resolved_index_code
    panels = [
        KlinePanel(
            code=resolved_index_code,
            name=resolved_index_name,
            bars=_normalize_bars(index_bars, label=resolved_index_code),
        )
    ]
    panels.extend(_stock_panels(stock_bars, stock_info))

    rows = len(panels) * 2
    row_heights: list[float] = []
    for _ in panels:
        row_heights.extend((0.75, 0.25))

    fig = make_subplots(
        rows=rows,
        cols=1,
        shared_xaxes=True,
        row_heights=row_heights,
        vertical_spacing=min(0.008, 0.18 / rows),
    )

    for panel_number, panel in enumerate(panels):
        price_row = panel_number * 2 + 1
        volume_row = price_row + 1
        bars = panel.bars
        label = _panel_label(panel, is_index=panel_number == 0)
        colors = [
            RISE_COLOR if close >= open_ else FALL_COLOR
            for open_, close in zip(bars["open"], bars["close"])
        ]

        fig.add_trace(
            go.Candlestick(
                x=bars.index,
                open=bars["open"],
                high=bars["high"],
                low=bars["low"],
                close=bars["close"],
                increasing_line_color=RISE_COLOR,
                decreasing_line_color=FALL_COLOR,
                increasing_fillcolor=RISE_COLOR,
                decreasing_fillcolor=FALL_COLOR,
                name=label,
                legendgroup=panel.code,
                showlegend=False,
                hoverlabel={"namelength": -1},
            ),
            row=price_row,
            col=1,
        )
        fig.add_trace(
            go.Bar(
                x=bars.index,
                y=bars["volume"],
                marker_color=colors,
                marker_line_width=0,
                opacity=0.72,
                name=f"{label} 成交量",
                legendgroup=panel.code,
                showlegend=False,
                hovertemplate="%{x|%Y-%m-%d}<br>成交量 %{y:,.0f}<extra></extra>",
            ),
            row=volume_row,
            col=1,
        )

        fig.update_yaxes(
            title_text=label,
            title_standoff=8,
            fixedrange=False,
            showgrid=True,
            gridcolor="#e7ebf0",
            zeroline=False,
            row=price_row,
            col=1,
        )
        fig.update_yaxes(
            title_text="量",
            title_standoff=8,
            rangemode="tozero",
            fixedrange=False,
            showgrid=False,
            zeroline=False,
            row=volume_row,
            col=1,
        )

    for row in range(1, rows + 1):
        fig.update_xaxes(
            matches="x",
            rangeslider_visible=False,
            showgrid=True,
            gridcolor="#eef1f4",
            showticklabels=row == rows,
            rangebreaks=[{"bounds": ["sat", "mon"]}],
            row=row,
            col=1,
        )

    all_dates = sorted({timestamp for panel in panels for timestamp in panel.bars.index})
    if initial_bars and len(all_dates) > initial_bars:
        initial_range = [all_dates[-initial_bars], all_dates[-1] + pd.Timedelta(days=1)]
        for row in range(1, rows + 1):
            fig.update_xaxes(range=initial_range, row=row, col=1)

    chart_title = title or f"{resolved_index_name}指数与成分股日K对照"
    fig.update_layout(
        title={"text": chart_title, "x": 0.01, "xanchor": "left"},
        height=max(500, panel_height * len(panels) + 90),
        template="plotly_white",
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        margin={"l": 150, "r": 38, "t": 72, "b": 52},
        hovermode="x unified",
        dragmode="zoom",
        bargap=0.16,
        showlegend=False,
        font={"family": "Arial, PingFang SC, Microsoft YaHei, sans-serif", "size": 12, "color": "#22262b"},
        modebar={"orientation": "h"},
        uirevision="industry-kline",
    )
    return fig


def write_industry_kline_html(
    index_bars: pd.DataFrame,
    stock_bars: Mapping[str, pd.DataFrame],
    output_path: str | Path,
    **figure_kwargs: Any,
) -> Path:
    """Build the figure and write a self-contained, offline HTML file."""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    figure = build_industry_kline_figure(index_bars, stock_bars, **figure_kwargs)
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


def load_cached_industry_klines(
    industry_code: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    top_n: int = 5,
    cache_root: str | Path | None = None,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], pd.DataFrame]:
    """Load an SW industry and its largest active constituents from daily caches."""

    if top_n < 1:
        raise ValueError("top_n must be at least 1")
    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    dates = _common_cached_dates(root, start_date=start_date, end_date=end_date)
    if not dates:
        raise RuntimeError("no common cached daily/sw_daily dates in the requested range")

    basic_dates = cached_trade_dates(cache_root=root, api_name="daily_basic")
    eligible_basic_dates = [date for date in basic_dates if date <= dates[-1]]
    if not eligible_basic_dates:
        raise RuntimeError("no daily_basic cache is available for constituent ranking")
    ranking_date = max(eligible_basic_dates)

    complete_members = root / "static" / "index_member_all_complete.parquet"
    member_dataset = "index_member_all_complete" if complete_members.exists() else "index_member_all"
    membership = read_dataset(member_dataset, static=True, cache_root=root)
    stock_basic = read_dataset("stock_basic", static=True, cache_root=root)
    latest_basic = read_dataset("daily_basic", ranking_date, cache_root=root)
    member_rows, industry_name = _industry_members(membership, industry_code)
    stock_info = (
        member_rows[["ts_code", "name"]]
        .drop_duplicates("ts_code")
        .merge(latest_basic[["ts_code", "circ_mv"]], on="ts_code", how="left")
    )
    if "name" in stock_basic.columns:
        fallback_names = stock_basic[["ts_code", "name"]].rename(columns={"name": "basic_name"})
        stock_info = stock_info.merge(fallback_names, on="ts_code", how="left")
        stock_info["name"] = stock_info["name"].fillna(stock_info["basic_name"])
        stock_info = stock_info.drop(columns="basic_name")
    stock_info = stock_info.dropna(subset=["circ_mv"]).sort_values(
        ["circ_mv", "ts_code"], ascending=[False, True]
    ).head(top_n)
    if stock_info.empty:
        raise RuntimeError(f"no constituents with circ_mv found for {industry_code} on {ranking_date}")

    index_frames: list[pd.DataFrame] = []
    stock_frames: dict[str, list[pd.DataFrame]] = {code: [] for code in stock_info["ts_code"]}
    selected_codes = set(stock_frames)
    for trade_date in dates:
        industry_daily = read_dataset("sw_daily", trade_date, cache_root=root)
        index_part = industry_daily[industry_daily["ts_code"].astype(str) == industry_code]
        if not index_part.empty:
            index_frames.append(index_part)

        daily = read_dataset("daily", trade_date, cache_root=root)
        selected = daily[daily["ts_code"].astype(str).isin(selected_codes)]
        for code, group in selected.groupby("ts_code"):
            stock_frames[str(code)].append(group)

    if not index_frames:
        raise RuntimeError(f"no sw_daily bars found for industry {industry_code}")
    missing = [code for code, frames in stock_frames.items() if not frames]
    if missing:
        raise RuntimeError(f"no daily bars found for constituents: {', '.join(missing)}")

    index_bars = pd.concat(index_frames, ignore_index=True)
    index_bars["name"] = industry_name
    stocks = {code: pd.concat(frames, ignore_index=True) for code, frames in stock_frames.items()}
    return index_bars, stocks, stock_info.reset_index(drop=True)


def _stock_panels(
    stock_bars: Mapping[str, pd.DataFrame], stock_info: pd.DataFrame | None
) -> list[KlinePanel]:
    details: dict[str, dict[str, Any]] = {}
    if stock_info is not None and not stock_info.empty:
        code_column = _find_column(stock_info, ("ts_code", "symbol"))
        for _, row in stock_info.iterrows():
            code = str(row[code_column])
            details[code] = {
                "name": str(row.get("name") or code),
                "circ_mv": _optional_float(row.get("circ_mv")),
            }

    panels = []
    for code, frame in stock_bars.items():
        normalized_code = str(code)
        detail = details.get(normalized_code, {})
        panels.append(
            KlinePanel(
                code=normalized_code,
                name=detail.get("name") or _first_text(frame, ("name",)) or normalized_code,
                circ_mv=detail.get("circ_mv"),
                bars=_normalize_bars(frame, label=normalized_code),
            )
        )
    panels.sort(key=lambda item: (item.circ_mv is None, -(item.circ_mv or 0), item.code))
    return panels


def _normalize_bars(frame: pd.DataFrame, *, label: str) -> pd.DataFrame:
    if frame.empty:
        raise ValueError(f"{label} has no bars")
    date_column = _find_column(frame, DATE_ALIASES)
    volume_column = _find_column(frame, VOLUME_ALIASES)
    missing = [column for column in OHLC_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"{label} is missing OHLC columns: {missing}")

    out = frame[[date_column, *OHLC_COLUMNS, volume_column]].copy()
    out.columns = ["date", *OHLC_COLUMNS, "volume"]
    out["date"] = pd.to_datetime(out["date"].astype(str), errors="coerce")
    for column in (*OHLC_COLUMNS, "volume"):
        out[column] = pd.to_numeric(out[column], errors="coerce")
    out = out.dropna().drop_duplicates("date", keep="last").sort_values("date")
    valid = (out[list(OHLC_COLUMNS)] > 0).all(axis=1) & (out["volume"] >= 0)
    out = out.loc[valid].set_index("date")
    if out.empty:
        raise ValueError(f"{label} has no valid OHLCV bars")
    return out


def _common_cached_dates(root: Path, *, start_date: str | None, end_date: str | None) -> list[str]:
    daily_dates = set(cached_trade_dates(cache_root=root, api_name="daily"))
    industry_dates = set(cached_trade_dates(cache_root=root, api_name="sw_daily"))
    dates = sorted(daily_dates & industry_dates)
    if start_date:
        start = _compact_date(start_date)
        dates = [date for date in dates if date >= start]
    if end_date:
        end = _compact_date(end_date)
        dates = [date for date in dates if date <= end]
    return dates


def _industry_members(membership: pd.DataFrame, industry_code: str) -> tuple[pd.DataFrame, str]:
    level_pairs = (("l1_code", "l1_name"), ("l2_code", "l2_name"), ("l3_code", "l3_name"))
    for code_column, name_column in level_pairs:
        if code_column not in membership.columns:
            continue
        matched = membership[membership[code_column].astype(str) == industry_code].copy()
        if matched.empty:
            continue
        if "is_new" in matched.columns:
            matched = matched[matched["is_new"].astype(str).str.upper().isin({"Y", "1", "TRUE"})]
        if matched.empty:
            raise RuntimeError(f"industry {industry_code} has no active constituents")
        names = matched[name_column].dropna().astype(str)
        return matched, (names.iloc[0] if not names.empty else industry_code)
    raise ValueError(f"industry code {industry_code} was not found in cached SW membership")


def _panel_label(panel: KlinePanel, *, is_index: bool) -> str:
    parts = [panel.name, panel.code]
    if is_index:
        parts.append("指数")
    if panel.circ_mv is not None:
        parts.append(f"流通市值 {panel.circ_mv / 10000:.1f}亿")
    return "  ".join(parts)


def _find_column(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str:
    for candidate in candidates:
        if candidate in frame.columns:
            return candidate
    raise ValueError(f"missing required column; expected one of {list(candidates)}")


def _first_text(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if candidate in frame.columns:
            values = frame[candidate].dropna().astype(str)
            if not values.empty:
                return values.iloc[0]
    return None


def _optional_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _compact_date(value: str) -> str:
    return pd.Timestamp(value).strftime("%Y%m%d")


def _import_plotly() -> tuple[Any, Any]:
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError as exc:
        raise RuntimeError(
            'Plotly is required. Install the visualization extra with `pip install -e ".[viz]"`.'
        ) from exc
    return go, make_subplots
