"""Find historical close-only peak-pullback-rebound patterns."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from stock_selection.data.tushare_cache import DEFAULT_CACHE_ROOT


OUTPUT_COLUMNS = [
    "ts_code",
    "stock_name",
    "peak_date",
    "peak_close",
    "pullback_low_date",
    "pullback_low_close",
    "pullback_pct",
    "pullback_days",
    "second_peak_date",
    "second_peak_close",
    "second_peak_ratio",
    "peak_to_second_peak_days",
    "peak_lookback_days",
    "peak_lookforward_days",
    "max_rebound_days",
    "min_pullback_pct",
    "min_second_peak_ratio",
]


def calculate_peak_pullback_rebounds(
    stock_daily: pd.DataFrame,
    *,
    as_of_date: str | None = None,
    peak_lookback_days: int = 120,
    peak_lookforward_days: int = 120,
    max_rebound_days: int = 40,
    min_pullback_pct: float = 0.10,
    min_second_peak_ratio: float = 0.70,
    stock_codes: Iterable[str] | None = None,
    stock_basic: pd.DataFrame | None = None,
    include_st: bool = False,
) -> pd.DataFrame:
    """Find every close-only peak-pullback-rebound event.

    A peak must be at least as high as every close in the preceding and
    following centered windows. After the peak, price must fall at least
    ``min_pullback_pct`` and then recover to ``min_second_peak_ratio`` of the
    peak close within ``max_rebound_days`` trading days.
    """
    _validate_parameters(
        peak_lookback_days=peak_lookback_days,
        peak_lookforward_days=peak_lookforward_days,
        max_rebound_days=max_rebound_days,
        min_pullback_pct=min_pullback_pct,
        min_second_peak_ratio=min_second_peak_ratio,
    )
    data = normalise_stock_daily(stock_daily, stock_basic=stock_basic)
    target_date = _normalise_date(as_of_date)
    if as_of_date is not None and not target_date:
        raise ValueError("as_of_date must be a valid date")
    if target_date:
        data = data[data["trade_date"].le(target_date)]
    if stock_codes is not None:
        selected = {str(code).strip() for code in stock_codes}
        data = data[data["ts_code"].isin(selected)]
    if not include_st:
        data = data[~data["stock_name"].map(_is_st_name)]
    if data.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    rows: list[dict[str, Any]] = []
    for ts_code, group in data.groupby("ts_code", sort=True):
        rows.extend(
            _calculate_one_stock(
                group,
                ts_code=ts_code,
                stock_name=_latest_name(group["stock_name"]),
                peak_lookback_days=peak_lookback_days,
                peak_lookforward_days=peak_lookforward_days,
                max_rebound_days=max_rebound_days,
                min_pullback_pct=min_pullback_pct,
                min_second_peak_ratio=min_second_peak_ratio,
            )
        )
    if not rows:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS).sort_values(
        ["peak_date", "ts_code"], ascending=[True, True]
    ).reset_index(drop=True)


def read_cached_stock_daily(
    *,
    cache_root: str | Path | None = None,
    as_of_date: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read daily stock bars and optional stock-basic names from cache."""
    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    directory = root / "daily"
    target = _normalise_date(as_of_date)
    if as_of_date is not None and not target:
        raise ValueError("as_of_date must be a valid date")
    paths = [
        path for path in sorted(directory.glob("*.parquet"))
        if not target or path.stem <= target
    ]
    if not paths:
        raise FileNotFoundError(f"no daily cache files found under {directory}")
    daily = pd.concat((pd.read_parquet(path) for path in paths), ignore_index=True)
    basic_path = root / "static" / "stock_basic.parquet"
    basic = pd.read_parquet(basic_path) if basic_path.exists() else pd.DataFrame()
    return daily, basic


def normalise_stock_daily(
    stock_daily: pd.DataFrame,
    *,
    stock_basic: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Normalize stock codes, names, dates, and close prices."""
    columns = ["ts_code", "stock_name", "trade_date", "close"]
    if stock_daily is None or stock_daily.empty:
        return pd.DataFrame(columns=columns)

    code = _series_from(stock_daily, ("ts_code", "stock_code"), "")
    name = _series_from(stock_daily, ("stock_name", "name"), "")
    result = pd.DataFrame(
        {
            "ts_code": code.fillna("").astype(str).str.strip(),
            "stock_name": name.fillna("").astype(str).str.strip(),
            "trade_date": _series_from(stock_daily, ("trade_date",), "").map(
                _normalise_date
            ),
            "close": pd.to_numeric(
                _series_from(stock_daily, ("close", "close_price"), None),
                errors="coerce",
            ),
        }
    )
    if (
        stock_basic is not None
        and not stock_basic.empty
        and {"ts_code", "name"}.issubset(stock_basic.columns)
    ):
        basic_names = (
            stock_basic[["ts_code", "name"]]
            .drop_duplicates("ts_code")
            .set_index("ts_code")["name"]
        )
        result["stock_name"] = result["stock_name"].mask(
            result["stock_name"].eq(""),
            result["ts_code"].map(basic_names),
        )
    result = result[
        result["ts_code"].ne("")
        & result["trade_date"].ne("")
        & result["close"].notna()
        & result["close"].gt(0)
    ]
    return result.drop_duplicates(
        ["ts_code", "trade_date"], keep="last"
    ).sort_values(["ts_code", "trade_date"]).reset_index(drop=True)


def _calculate_one_stock(
    group: pd.DataFrame,
    *,
    ts_code: str,
    stock_name: str,
    peak_lookback_days: int,
    peak_lookforward_days: int,
    max_rebound_days: int,
    min_pullback_pct: float,
    min_second_peak_ratio: float,
) -> list[dict[str, Any]]:
    series = group.sort_values("trade_date").reset_index(drop=True)
    closes = series["close"].to_numpy(dtype=float)
    total_window = peak_lookback_days + peak_lookforward_days + 1
    if len(series) < total_window + 1:
        return []

    local_max = (
        pd.Series(closes)
        .rolling(total_window, center=True, min_periods=total_window)
        .max()
        .to_numpy()
    )
    peak_positions = np.flatnonzero(
        np.isfinite(local_max) & np.isclose(closes, local_max, rtol=0.0, atol=1e-12)
    )
    peak_positions = _first_position_per_equal_plateau(peak_positions, closes)

    rows: list[dict[str, Any]] = []
    for peak_position in peak_positions:
        search_end = min(len(series), peak_position + 1 + max_rebound_days)
        after_peak = series.iloc[peak_position + 1 : search_end]
        if after_peak.empty:
            continue

        low_position = int(after_peak["close"].idxmin())
        low_close = float(series.loc[low_position, "close"])
        peak_close = float(series.loc[peak_position, "close"])
        pullback_pct = low_close / peak_close - 1
        if pullback_pct > -min_pullback_pct:
            continue

        after_low = series.iloc[low_position + 1 : search_end]
        if after_low.empty:
            continue
        second_peak_position = int(after_low["close"].idxmax())
        second_peak_close = float(series.loc[second_peak_position, "close"])
        second_peak_ratio = second_peak_close / peak_close
        if second_peak_ratio < min_second_peak_ratio:
            continue

        rows.append(
            {
                "ts_code": ts_code,
                "stock_name": stock_name,
                "peak_date": str(series.loc[peak_position, "trade_date"]),
                "peak_close": peak_close,
                "pullback_low_date": str(series.loc[low_position, "trade_date"]),
                "pullback_low_close": low_close,
                "pullback_pct": pullback_pct,
                "pullback_days": int(low_position - peak_position),
                "second_peak_date": str(
                    series.loc[second_peak_position, "trade_date"]
                ),
                "second_peak_close": second_peak_close,
                "second_peak_ratio": second_peak_ratio,
                "peak_to_second_peak_days": int(
                    second_peak_position - peak_position
                ),
                "peak_lookback_days": peak_lookback_days,
                "peak_lookforward_days": peak_lookforward_days,
                "max_rebound_days": max_rebound_days,
                "min_pullback_pct": min_pullback_pct,
                "min_second_peak_ratio": min_second_peak_ratio,
            }
        )
    return rows


def _first_position_per_equal_plateau(
    positions: np.ndarray,
    closes: np.ndarray,
) -> np.ndarray:
    if len(positions) < 2:
        return positions
    keep = [positions[0]]
    for position in positions[1:]:
        previous = keep[-1]
        if not np.isclose(closes[position], closes[previous], rtol=0.0, atol=1e-12):
            keep.append(position)
    return np.asarray(keep, dtype=int)


def _validate_parameters(**parameters: Any) -> None:
    for name in (
        "peak_lookback_days",
        "peak_lookforward_days",
        "max_rebound_days",
    ):
        if int(parameters[name]) <= 0:
            raise ValueError(f"{name} must be greater than zero")
    for name in ("min_pullback_pct", "min_second_peak_ratio"):
        value = float(parameters[name])
        if not 0 < value <= 1:
            raise ValueError(f"{name} must be greater than zero and at most one")


def _series_from(data: pd.DataFrame, choices: tuple[str, ...], default: Any) -> pd.Series:
    column = next((choice for choice in choices if choice in data.columns), None)
    return data[column] if column is not None else pd.Series(default, index=data.index)


def _latest_name(values: pd.Series) -> str:
    nonempty = values.fillna("").astype(str).str.strip()
    nonempty = nonempty[nonempty.ne("")]
    return str(nonempty.iloc[-1]) if not nonempty.empty else ""


def _is_st_name(name: str) -> bool:
    return "ST" in str(name).upper()


def _normalise_date(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip().replace("-", "")
    return text[:8] if len(text) >= 8 and text[:8].isdigit() else ""
