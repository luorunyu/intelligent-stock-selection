"""Detect and validate historical stabilization signals in SW L2 indexes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from stock_selection.data.tushare_cache import DEFAULT_CACHE_ROOT


OUTPUT_COLUMNS = [
    "index_code",
    "name",
    "level",
    "signal_date",
    "signal_close",
    "stable_start_date",
    "stable_days",
    "stable_open_min",
    "stable_open_max",
    "stable_close_min",
    "stable_close_max",
    "stable_price_min",
    "stable_price_max",
    "stable_range_points",
    "decline_start_date",
    "decline_start_close",
    "decline_low_date",
    "decline_low_close",
    "decline_days",
    "decline_pct",
    "downtrend_by_duration",
    "downtrend_by_drawdown",
    "forward_window",
    "forward_observations",
    "target_peak_ratio",
    "target_close",
    "max_forward_close",
    "max_forward_return",
    "max_forward_peak_ratio",
    "target_hit",
    "target_hit_date",
    "trading_days_to_target",
    "validation_status",
]


def calculate_industry_index_stabilization(
    index_data: pd.DataFrame,
    industry_dictionary: dict[str, Any],
    *,
    level: str = "L2",
    as_of_date: str | None = None,
    trend_lookback: int = 120,
    min_decline_days: int = 20,
    min_decline_pct: float = 0.30,
    min_stable_days: int = 5,
    max_stable_range_points: float = 10.0,
    forward_window: int = 60,
    target_peak_ratio: float = 0.80,
    industry_codes: Iterable[str] | None = None,
) -> pd.DataFrame:
    """Find every historical L2/L3 stabilization episode and validate its outcome.

    A signal is emitted on the first day when the trailing stabilization
    interval reaches ``min_stable_days``. All open and close values in that
    interval must fit inside ``max_stable_range_points``.

    Signal detection uses only data available on or before the signal date.
    Future bars are used exclusively for validation.
    """
    _validate_parameters(
        trend_lookback=trend_lookback,
        min_decline_days=min_decline_days,
        min_decline_pct=min_decline_pct,
        min_stable_days=min_stable_days,
        max_stable_range_points=max_stable_range_points,
        forward_window=forward_window,
        target_peak_ratio=target_peak_ratio,
    )
    normalised_level = _normalise_level(level)
    data = normalise_industry_index_data(index_data)
    target_date = _normalise_date(as_of_date)
    if as_of_date is not None and not target_date:
        raise ValueError("as_of_date must be a valid date")
    if target_date:
        data = data[data["trade_date"].le(target_date)]

    industry_names = industry_level_names(industry_dictionary, normalised_level)
    selected_codes = set(industry_names)
    if industry_codes is not None:
        selected_codes &= {str(code).strip() for code in industry_codes}
    data = data[data["index_code"].isin(selected_codes)].copy()
    if data.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    rows: list[dict[str, Any]] = []
    for index_code, group in data.groupby("index_code", sort=True):
        rows.extend(
            _calculate_one_index_history(
                group,
                index_code=index_code,
                name=industry_names.get(index_code) or _latest_nonempty(group["name"]),
                level=normalised_level,
                trend_lookback=trend_lookback,
                min_decline_days=min_decline_days,
                min_decline_pct=min_decline_pct,
                min_stable_days=min_stable_days,
                max_stable_range_points=max_stable_range_points,
                forward_window=forward_window,
                target_peak_ratio=target_peak_ratio,
            )
        )
    if not rows:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS).sort_values(
        ["signal_date", "index_code"], ascending=[True, True]
    ).reset_index(drop=True)


def screen_industry_index_stabilization(
    signals: pd.DataFrame,
    *,
    validation_status: str | None = None,
    target_hit: bool | None = None,
) -> pd.DataFrame:
    """Filter calculated signals without changing signal definitions."""
    result = signals.copy()
    if validation_status is not None:
        allowed = {"success", "failed", "pending"}
        if validation_status not in allowed:
            raise ValueError(f"validation_status must be one of {sorted(allowed)}")
        result = result[result["validation_status"].eq(validation_status)]
    if target_hit is not None:
        result = result[result["target_hit"].eq(bool(target_hit))]
    return result.reset_index(drop=True)


def read_cached_industry_index_data(
    *,
    cache_root: str | Path | None = None,
) -> pd.DataFrame:
    """Read every cached SW daily-bar file."""
    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    directory = root / "sw_daily"
    paths = sorted(directory.glob("*.parquet"))
    if not paths:
        raise FileNotFoundError(f"no sw_daily cache files found under {directory}")
    return pd.concat((pd.read_parquet(path) for path in paths), ignore_index=True)


def load_industry_dictionary(path: str | Path) -> dict[str, Any]:
    """Load the SW hierarchy dictionary used as the L2 authority."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("industry dictionary root must be an object")
    return payload


def industry_level_names(
    industry_dictionary: dict[str, Any],
    level: str,
) -> dict[str, str]:
    """Return ``index_code -> name`` for the requested SW2021 industry level."""
    normalised_level = _normalise_level(level)
    dictionary_key = f"level{normalised_level[-1]}_index"
    entries = industry_dictionary.get(dictionary_key)
    if not isinstance(entries, dict):
        raise ValueError(f"industry dictionary does not contain {dictionary_key}")
    return {
        str(code).strip(): str(entry.get("name") or "").strip()
        for code, entry in entries.items()
        if isinstance(entry, dict) and str(code).strip()
    }


def normalise_industry_index_data(index_data: pd.DataFrame) -> pd.DataFrame:
    """Normalize the OHLC fields required by the strategy."""
    columns = ["index_code", "name", "trade_date", "open", "close"]
    if index_data is None or index_data.empty:
        return pd.DataFrame(columns=columns)

    aliases = {
        "index_code": ("ts_code", "index_code"),
        "name": ("name", "index_name"),
        "trade_date": ("trade_date",),
        "open": ("open",),
        "close": ("close",),
    }
    result = pd.DataFrame(index=index_data.index)
    for target, choices in aliases.items():
        source = next((column for column in choices if column in index_data.columns), None)
        result[target] = index_data[source] if source else None

    result["index_code"] = result["index_code"].fillna("").astype(str).str.strip()
    result["name"] = result["name"].fillna("").astype(str).str.strip()
    result["trade_date"] = result["trade_date"].map(_normalise_date)
    result["open"] = pd.to_numeric(result["open"], errors="coerce")
    result["close"] = pd.to_numeric(result["close"], errors="coerce")
    result = result[
        result["index_code"].ne("")
        & result["trade_date"].ne("")
        & result["open"].notna()
        & result["close"].notna()
        & result["open"].gt(0)
        & result["close"].gt(0)
    ]
    result = result.drop_duplicates(["index_code", "trade_date"], keep="last")
    return result[columns].sort_values(["index_code", "trade_date"]).reset_index(drop=True)


def _calculate_one_index_history(
    group: pd.DataFrame,
    *,
    index_code: str,
    name: str,
    level: str,
    trend_lookback: int,
    min_decline_days: int,
    min_decline_pct: float,
    min_stable_days: int,
    max_stable_range_points: float,
    forward_window: int,
    target_peak_ratio: float,
) -> list[dict[str, Any]]:
    series = group.sort_values("trade_date").reset_index(drop=True)
    rows: list[dict[str, Any]] = []
    stable_episode_active = False

    for signal_position in range(len(series)):
        stable_start = _trailing_stable_start(
            series, signal_position, max_stable_range_points
        )
        stable_days = signal_position - stable_start + 1
        stable_now = stable_days >= min_stable_days
        if not stable_now:
            stable_episode_active = False
            continue
        if stable_episode_active:
            continue
        stable_episode_active = True

        trend_start = max(0, stable_start - trend_lookback)
        before_stable = series.iloc[trend_start:stable_start]
        if before_stable.empty:
            continue
        peak_position = int(before_stable["close"].idxmax())
        decline_section = series.iloc[peak_position : signal_position + 1]
        low_position = int(decline_section["close"].idxmin())
        # The stabilization band must form at the end of the decline.  If the
        # lowest close happened before this band, prices have already rebounded
        # and a later sideways pause must not reuse the old decline as a new
        # reversal signal.
        if low_position < stable_start:
            continue
        peak_close = float(series.loc[peak_position, "close"])
        low_close = float(series.loc[low_position, "close"])
        decline_days = stable_start - peak_position
        decline_pct = low_close / peak_close - 1
        by_duration = decline_days >= min_decline_days
        by_drawdown = decline_pct <= -min_decline_pct
        if not (by_duration or by_drawdown):
            continue

        stable = series.iloc[stable_start : signal_position + 1]
        signal_close = float(series.loc[signal_position, "close"])
        future = series.iloc[
            signal_position + 1 : signal_position + 1 + forward_window
        ]
        validation = _validate_forward_outcome(
            future,
            signal_close=signal_close,
            decline_start_close=peak_close,
            forward_window=forward_window,
            target_peak_ratio=target_peak_ratio,
        )
        stable_open_min = float(stable["open"].min())
        stable_open_max = float(stable["open"].max())
        stable_close_min = float(stable["close"].min())
        stable_close_max = float(stable["close"].max())
        stable_price_min = min(stable_open_min, stable_close_min)
        stable_price_max = max(stable_open_max, stable_close_max)

        rows.append(
            {
                "index_code": index_code,
                "name": name,
                "level": level,
                "signal_date": str(series.loc[signal_position, "trade_date"]),
                "signal_close": signal_close,
                "stable_start_date": str(series.loc[stable_start, "trade_date"]),
                "stable_days": stable_days,
                "stable_open_min": stable_open_min,
                "stable_open_max": stable_open_max,
                "stable_close_min": stable_close_min,
                "stable_close_max": stable_close_max,
                "stable_price_min": stable_price_min,
                "stable_price_max": stable_price_max,
                "stable_range_points": stable_price_max - stable_price_min,
                "decline_start_date": str(series.loc[peak_position, "trade_date"]),
                "decline_start_close": peak_close,
                "decline_low_date": str(series.loc[low_position, "trade_date"]),
                "decline_low_close": low_close,
                "decline_days": decline_days,
                "decline_pct": decline_pct,
                "downtrend_by_duration": bool(by_duration),
                "downtrend_by_drawdown": bool(by_drawdown),
                "forward_window": forward_window,
                "target_peak_ratio": target_peak_ratio,
                "target_close": peak_close * target_peak_ratio,
                **validation,
            }
        )
    return rows


def _trailing_stable_start(
    series: pd.DataFrame,
    end_position: int,
    max_stable_range_points: float,
) -> int:
    """Find the longest trailing open/close interval inside one price band."""
    price_min = float("inf")
    price_max = float("-inf")
    start_position = end_position
    for position in range(end_position, -1, -1):
        open_price = float(series.loc[position, "open"])
        close_price = float(series.loc[position, "close"])
        next_min = min(price_min, open_price, close_price)
        next_max = max(price_max, open_price, close_price)
        if next_max - next_min > max_stable_range_points:
            break
        price_min = next_min
        price_max = next_max
        start_position = position
    return start_position


def _validate_forward_outcome(
    future: pd.DataFrame,
    *,
    signal_close: float,
    decline_start_close: float,
    forward_window: int,
    target_peak_ratio: float,
) -> dict[str, Any]:
    """判断策略是否有效"""
    target_close = decline_start_close * target_peak_ratio
    if future.empty:
        return {
            "forward_observations": 0,
            "max_forward_close": None,
            "max_forward_return": None,
            "max_forward_peak_ratio": None,
            "target_hit": False,
            "target_hit_date": None,
            "trading_days_to_target": None,
            "validation_status": "pending",
        }

    returns = future["close"] / signal_close - 1
    peak_ratios = future["close"] / decline_start_close
    maximum_position = int(returns.idxmax())
    hit_rows = future[future["close"].ge(target_close)]
    target_hit = not hit_rows.empty
    hit_position = int(hit_rows.index[0]) if target_hit else None
    status = (
        "success"
        if target_hit
        else "failed"
        if len(future) >= forward_window
        else "pending"
    )
    return {
        "forward_observations": int(len(future)),
        "max_forward_close": float(future.loc[maximum_position, "close"]),
        "max_forward_return": float(returns.loc[maximum_position]),
        "max_forward_peak_ratio": float(peak_ratios.loc[maximum_position]),
        "target_hit": bool(target_hit),
        "target_hit_date": (
            str(future.loc[hit_position, "trade_date"]) if hit_position is not None else None
        ),
        "trading_days_to_target": (
            int(future.index.get_loc(hit_position) + 1) if hit_position is not None else None
        ),
        "validation_status": status,
    }


def _validate_parameters(**parameters: Any) -> None:
    integer_positive = ("trend_lookback", "min_decline_days", "min_stable_days", "forward_window")
    for name in integer_positive:
        if int(parameters[name]) <= 0:
            raise ValueError(f"{name} must be greater than zero")
    for name in ("min_decline_pct", "max_stable_range_points"):
        if float(parameters[name]) <= 0:
            raise ValueError(f"{name} must be greater than zero")
    target_peak_ratio = float(parameters["target_peak_ratio"])
    if not 0 < target_peak_ratio <= 1:
        raise ValueError("target_peak_ratio must be greater than zero and at most one")


def _latest_nonempty(values: pd.Series) -> str:
    nonempty = values[values.fillna("").astype(str).str.strip().ne("")]
    return str(nonempty.iloc[-1]).strip() if not nonempty.empty else ""


def _normalise_level(value: Any) -> str:
    level = str(value).strip().upper()
    if level not in {"L2", "L3"}:
        raise ValueError("level must be L2 or L3")
    return level


def _normalise_date(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip().replace("-", "")
    return text[:8] if len(text) >= 8 and text[:8].isdigit() else ""
