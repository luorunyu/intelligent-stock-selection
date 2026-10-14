"""Helpers for joining cached daily stock market-capitalization data."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from stock_selection.data.tushare_cache import DEFAULT_CACHE_ROOT


MARKET_CAP_COLUMNS = ("total_mv", "circ_mv")


def load_cached_market_caps(
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    stock_codes: Iterable[str] | None = None,
    cache_root: str | Path | None = None,
) -> pd.DataFrame:
    """Read cached ``daily_basic`` market caps for a date/code subset.

    Tushare reports ``total_mv`` and ``circ_mv`` in 10,000 yuan (万元). Missing
    daily-basic files are treated as an optional-data condition and return an
    empty frame so the price/state workflow can still run.
    """
    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    directory = root / "daily_basic"
    start = _normalise_date(start_date) if start_date is not None else ""
    end = _normalise_date(end_date) if end_date is not None else ""
    if start_date is not None and not start:
        raise ValueError("start_date must be a valid date")
    if end_date is not None and not end:
        raise ValueError("end_date must be a valid date")
    if start and end and start > end:
        raise ValueError("start_date must not be after end_date")

    selected_codes = (
        {str(code).strip() for code in stock_codes if str(code).strip()}
        if stock_codes is not None
        else None
    )
    paths = [
        path
        for path in sorted(directory.glob("*.parquet"))
        if (not start or path.stem >= start) and (not end or path.stem <= end)
    ]
    if not paths:
        return pd.DataFrame(columns=["ts_code", "trade_date", *MARKET_CAP_COLUMNS])

    frames: list[pd.DataFrame] = []
    columns = ["ts_code", "trade_date", *MARKET_CAP_COLUMNS]
    for path in paths:
        try:
            frame = pd.read_parquet(path, columns=columns)
        except (FileNotFoundError, KeyError, ValueError):
            continue
        if selected_codes is not None:
            frame = frame[frame["ts_code"].astype(str).isin(selected_codes)]
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=columns)
    return normalise_market_caps(pd.concat(frames, ignore_index=True))


def normalise_market_caps(data: pd.DataFrame) -> pd.DataFrame:
    """Normalize daily-basic identifiers, dates, and market-cap units."""
    columns = ["ts_code", "trade_date", *MARKET_CAP_COLUMNS]
    if data is None or data.empty:
        return pd.DataFrame(columns=columns)
    result = pd.DataFrame(index=data.index)
    for column in columns:
        result[column] = data[column] if column in data.columns else None
    result["ts_code"] = result["ts_code"].fillna("").astype(str).str.strip()
    result["trade_date"] = result["trade_date"].map(_normalise_date)
    for column in MARKET_CAP_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result[result["ts_code"].ne("") & result["trade_date"].ne("")]
    return result.drop_duplicates(["ts_code", "trade_date"], keep="last").reset_index(
        drop=True
    )


def merge_market_caps(
    data: pd.DataFrame,
    market_caps: pd.DataFrame | None,
) -> pd.DataFrame:
    """Left-join total and circulating market caps by stock and trade date."""
    result = data.copy()
    for column in MARKET_CAP_COLUMNS:
        if column not in result.columns:
            result[column] = pd.NA
    if result.empty or market_caps is None or market_caps.empty:
        return result
    if not {"ts_code", "trade_date"}.issubset(result.columns):
        raise ValueError("data must contain ts_code and trade_date for market-cap merge")

    left = result.copy()
    left["ts_code"] = left["ts_code"].fillna("").astype(str).str.strip()
    left["trade_date"] = left["trade_date"].map(_normalise_date)
    right = normalise_market_caps(market_caps).rename(
        columns={column: f"{column}__daily_basic" for column in MARKET_CAP_COLUMNS}
    )
    merged = left.merge(right, on=["ts_code", "trade_date"], how="left")
    for column in MARKET_CAP_COLUMNS:
        source = f"{column}__daily_basic"
        merged[column] = pd.to_numeric(merged[column], errors="coerce").fillna(
            pd.to_numeric(merged[source], errors="coerce")
        )
        merged = merged.drop(columns=source)
    return merged


def format_market_cap(value: Any) -> str:
    """Format Tushare's 万元 value for human-facing reports and hover text."""
    if value is None or pd.isna(value):
        return "-"
    amount = float(value)
    if abs(amount) >= 10000:
        return f"{amount / 10000:,.2f}亿元"
    return f"{amount:,.0f}万元"


def _normalise_date(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip().replace("-", "")
    return text[:8] if len(text) >= 8 and text[:8].isdigit() else ""
