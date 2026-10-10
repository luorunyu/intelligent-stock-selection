"""Detect and validate historical stabilization signals in SW L2 indexes."""

from __future__ import annotations

from collections import deque
from enum import IntEnum
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from stock_selection.data.tushare_cache import DEFAULT_CACHE_ROOT


class IndustryStabilizationState(IntEnum):
    """Daily states for industry-index stabilization episodes."""

    NORMAL = 0
    STABILIZING_CANDIDATE = 10
    STABILIZED = 20


STATE_DESCRIPTIONS = {
    IndustryStabilizationState.NORMAL: "普通状态",
    IndustryStabilizationState.STABILIZING_CANDIDATE: "企稳候选区间",
    IndustryStabilizationState.STABILIZED: "已确认企稳区间",
}


OUTPUT_COLUMNS = [
    "index_code",
    "name",
    "level",
    "episode_id",
    "signal_date",
    "signal_close",
    "stable_start_date",
    "stable_end_date",
    "stable_days",
    "episode_trading_days",
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

STATE_OUTPUT_COLUMNS = [
    "index_code",
    "name",
    "level",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "vol",
    "state",
    "state_name",
    "state_description",
    "episode_id",
    "state_start_date",
    "state_days",
    "signal_date",
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
    """Return one validated event row for every confirmed stabilization episode."""
    states = calculate_industry_index_stabilization_states(
        index_data,
        industry_dictionary,
        level=level,
        as_of_date=as_of_date,
        trend_lookback=trend_lookback,
        min_decline_days=min_decline_days,
        min_decline_pct=min_decline_pct,
        min_stable_days=min_stable_days,
        max_stable_range_points=max_stable_range_points,
        industry_codes=industry_codes,
    )
    return extract_stabilization_episodes(
        states,
        forward_window=forward_window,
        target_peak_ratio=target_peak_ratio,
    )


def calculate_industry_index_stabilization_states(
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
    industry_codes: Iterable[str] | None = None,
) -> pd.DataFrame:
    """Return one stabilization-state row for every selected industry trading day."""
    _validate_parameters(
        trend_lookback=trend_lookback,
        min_decline_days=min_decline_days,
        min_decline_pct=min_decline_pct,
        min_stable_days=min_stable_days,
        max_stable_range_points=max_stable_range_points,
        forward_window=1,
        target_peak_ratio=1,
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
        return pd.DataFrame(columns=STATE_OUTPUT_COLUMNS)

    rows: list[dict[str, Any]] = []
    for index_code, group in data.groupby("index_code", sort=True):
        rows.extend(
            _calculate_one_index_states(
                group,
                index_code=index_code,
                name=industry_names.get(index_code) or _latest_nonempty(group["name"]),
                level=normalised_level,
                trend_lookback=trend_lookback,
                min_decline_days=min_decline_days,
                min_decline_pct=min_decline_pct,
                min_stable_days=min_stable_days,
                max_stable_range_points=max_stable_range_points,
            )
        )
    if not rows:
        return pd.DataFrame(columns=STATE_OUTPUT_COLUMNS)
    return pd.DataFrame(rows, columns=STATE_OUTPUT_COLUMNS).sort_values(
        ["trade_date", "index_code"], ascending=[True, True]
    ).reset_index(drop=True)


def extract_stabilization_episodes(
    states: pd.DataFrame,
    *,
    forward_window: int = 60,
    target_peak_ratio: float = 0.80,
) -> pd.DataFrame:
    """Extract confirmed episodes from daily states and validate future outcomes."""
    if forward_window <= 0:
        raise ValueError("forward_window must be greater than zero")
    if not 0 < target_peak_ratio <= 1:
        raise ValueError("target_peak_ratio must be greater than zero and at most one")
    if states is None or states.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    rows: list[dict[str, Any]] = []
    ordered = states.sort_values(["index_code", "trade_date"]).reset_index(drop=True)
    for index_code, series in ordered.groupby("index_code", sort=True):
        series = series.reset_index(drop=True)
        confirmed = series[
            series["state"].eq(int(IndustryStabilizationState.STABILIZED))
            & series["episode_id"].gt(0)
        ]
        for episode_id, episode in confirmed.groupby("episode_id", sort=True):
            episode = episode.sort_values("trade_date").reset_index(drop=True)
            signal_date = str(episode["signal_date"].dropna().iloc[0])
            signal_rows = series[series["trade_date"].eq(signal_date)]
            if signal_rows.empty:
                continue
            signal_position = int(signal_rows.index[0])
            signal_row = signal_rows.iloc[0]
            confirmation = episode[episode["trade_date"].le(signal_date)]
            future = series.iloc[
                signal_position + 1 : signal_position + 1 + forward_window
            ].reset_index(drop=True)
            validation = _validate_forward_outcome(
                future,
                signal_close=float(signal_row["close"]),
                decline_start_close=float(signal_row["decline_start_close"]),
                forward_window=forward_window,
                target_peak_ratio=target_peak_ratio,
            )
            rows.append(
                {
                    "index_code": index_code,
                    "name": str(signal_row["name"]),
                    "level": str(signal_row["level"]),
                    "episode_id": int(episode_id),
                    "signal_date": signal_date,
                    "signal_close": float(signal_row["close"]),
                    "stable_start_date": str(episode["trade_date"].iloc[0]),
                    "stable_end_date": str(episode["trade_date"].iloc[-1]),
                    "stable_days": int(len(confirmation)),
                    "episode_trading_days": int(len(episode)),
                    "stable_open_min": float(confirmation["open"].min()),
                    "stable_open_max": float(confirmation["open"].max()),
                    "stable_close_min": float(confirmation["close"].min()),
                    "stable_close_max": float(confirmation["close"].max()),
                    "stable_price_min": float(
                        min(
                            confirmation["open"].min(),
                            confirmation["close"].min(),
                        )
                    ),
                    "stable_price_max": float(
                        max(
                            confirmation["open"].max(),
                            confirmation["close"].max(),
                        )
                    ),
                    "stable_range_points": float(
                        max(
                            confirmation["open"].max(),
                            confirmation["close"].max(),
                        )
                        - min(
                            confirmation["open"].min(),
                            confirmation["close"].min(),
                        )
                    ),
                    "decline_start_date": str(signal_row["decline_start_date"]),
                    "decline_start_close": float(signal_row["decline_start_close"]),
                    "decline_low_date": str(signal_row["decline_low_date"]),
                    "decline_low_close": float(signal_row["decline_low_close"]),
                    "decline_days": int(signal_row["decline_days"]),
                    "decline_pct": float(signal_row["decline_pct"]),
                    "downtrend_by_duration": bool(
                        signal_row["downtrend_by_duration"]
                    ),
                    "downtrend_by_drawdown": bool(
                        signal_row["downtrend_by_drawdown"]
                    ),
                    "forward_window": forward_window,
                    "target_peak_ratio": target_peak_ratio,
                    "target_close": float(signal_row["decline_start_close"])
                    * target_peak_ratio,
                    **validation,
                }
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


def latest_industry_stabilization_states(states: pd.DataFrame) -> pd.DataFrame:
    """Return the latest daily state for every industry index."""
    if states is None or states.empty:
        return pd.DataFrame(columns=STATE_OUTPUT_COLUMNS)
    ordered = states.sort_values(["index_code", "trade_date"])
    return ordered.groupby("index_code", sort=True, as_index=False).tail(1).reset_index(
        drop=True
    )


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
    columns = [
        "index_code",
        "name",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "vol",
    ]
    if index_data is None or index_data.empty:
        return pd.DataFrame(columns=columns)

    aliases = {
        "index_code": ("ts_code", "index_code"),
        "name": ("name", "index_name"),
        "trade_date": ("trade_date",),
        "open": ("open",),
        "high": ("high",),
        "low": ("low",),
        "close": ("close",),
        "vol": ("vol", "volume"),
    }
    result = pd.DataFrame(index=index_data.index)
    for target, choices in aliases.items():
        source = next((column for column in choices if column in index_data.columns), None)
        result[target] = index_data[source] if source else None

    result["index_code"] = result["index_code"].fillna("").astype(str).str.strip()
    result["name"] = result["name"].fillna("").astype(str).str.strip()
    result["trade_date"] = result["trade_date"].map(_normalise_date)
    result["open"] = pd.to_numeric(result["open"], errors="coerce")
    result["high"] = pd.to_numeric(result["high"], errors="coerce")
    result["low"] = pd.to_numeric(result["low"], errors="coerce")
    result["close"] = pd.to_numeric(result["close"], errors="coerce")
    result["vol"] = pd.to_numeric(result["vol"], errors="coerce").fillna(0.0)
    result["high"] = result["high"].fillna(result[["open", "close"]].max(axis=1))
    result["low"] = result["low"].fillna(result[["open", "close"]].min(axis=1))
    result = result[
        result["index_code"].ne("")
        & result["trade_date"].ne("")
        & result["open"].notna()
        & result["high"].notna()
        & result["low"].notna()
        & result["close"].notna()
        & result["open"].gt(0)
        & result["high"].gt(0)
        & result["low"].gt(0)
        & result["close"].gt(0)
    ]
    result = result.drop_duplicates(["index_code", "trade_date"], keep="last")
    return result[columns].sort_values(["index_code", "trade_date"]).reset_index(drop=True)


def _calculate_one_index_states(
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
) -> list[dict[str, Any]]:
    series = group.sort_values("trade_date").reset_index(drop=True)
    rows = [
        _base_state_record(
            row,
            index_code=index_code,
            name=name,
            level=level,
        )
        for row in series.to_dict("records")
    ]
    stable_episode_active = False
    active_episode_id = 0
    active_episode_start = -1
    active_signal_date = ""
    active_context: dict[str, Any] | None = None
    next_episode_id = 0
    stable_starts = _trailing_stable_starts(series, max_stable_range_points)

    for position, stable_start in enumerate(stable_starts):
        stable_days = position - stable_start + 1
        stable_now = stable_days >= min_stable_days
        if not stable_now:
            stable_episode_active = False
            active_episode_id = 0
            active_episode_start = -1
            active_signal_date = ""
            active_context = None
            context = _decline_context(
                series,
                stable_start=stable_start,
                end_position=position,
                trend_lookback=trend_lookback,
                min_decline_days=min_decline_days,
                min_decline_pct=min_decline_pct,
            )
            if context is not None:
                _set_state_record(
                    rows[position],
                    IndustryStabilizationState.STABILIZING_CANDIDATE,
                    episode_id=next_episode_id + 1,
                    state_start_position=stable_start,
                    state_days=stable_days,
                    signal_date="",
                    series=series,
                    context=context,
                    band_start=stable_start,
                    band_end=position,
                )
            continue
        if stable_episode_active:
            if active_context is None:
                continue
            _set_state_record(
                rows[position],
                IndustryStabilizationState.STABILIZED,
                episode_id=active_episode_id,
                state_start_position=active_episode_start,
                state_days=position - active_episode_start + 1,
                signal_date=active_signal_date,
                series=series,
                context=active_context,
                band_start=stable_start,
                band_end=position,
            )
            continue
        stable_episode_active = True

        context = _decline_context(
            series,
            stable_start=stable_start,
            end_position=position,
            trend_lookback=trend_lookback,
            min_decline_days=min_decline_days,
            min_decline_pct=min_decline_pct,
        )
        if context is None:
            continue

        next_episode_id += 1
        active_episode_id = next_episode_id
        active_episode_start = stable_start
        active_signal_date = str(series.loc[position, "trade_date"])
        active_context = context
        for historical_position in range(stable_start, position + 1):
            _set_state_record(
                rows[historical_position],
                IndustryStabilizationState.STABILIZED,
                episode_id=active_episode_id,
                state_start_position=stable_start,
                state_days=historical_position - stable_start + 1,
                signal_date=active_signal_date,
                series=series,
                context=context,
                band_start=stable_start,
                band_end=historical_position,
            )
    return rows


def _base_state_record(
    row: dict[str, Any],
    *,
    index_code: str,
    name: str,
    level: str,
) -> dict[str, Any]:
    state = IndustryStabilizationState.NORMAL
    return {
        "index_code": index_code,
        "name": name,
        "level": level,
        "trade_date": str(row["trade_date"]),
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "vol": float(row["vol"]),
        "state": int(state),
        "state_name": state.name,
        "state_description": STATE_DESCRIPTIONS[state],
        "episode_id": 0,
        "state_start_date": None,
        "state_days": 0,
        "signal_date": None,
        "stable_price_min": None,
        "stable_price_max": None,
        "stable_range_points": None,
        "decline_start_date": None,
        "decline_start_close": None,
        "decline_low_date": None,
        "decline_low_close": None,
        "decline_days": None,
        "decline_pct": None,
        "downtrend_by_duration": False,
        "downtrend_by_drawdown": False,
    }


def _set_state_record(
    record: dict[str, Any],
    state: IndustryStabilizationState,
    *,
    episode_id: int,
    state_start_position: int,
    state_days: int,
    signal_date: str,
    series: pd.DataFrame,
    context: dict[str, Any] | None,
    band_start: int,
    band_end: int,
) -> None:
    band = series.iloc[band_start : band_end + 1]
    stable_price_min = float(min(band["open"].min(), band["close"].min()))
    stable_price_max = float(max(band["open"].max(), band["close"].max()))
    record.update(
        {
            "state": int(state),
            "state_name": state.name,
            "state_description": STATE_DESCRIPTIONS[state],
            "episode_id": int(episode_id),
            "state_start_date": str(
                series.loc[state_start_position, "trade_date"]
            ),
            "state_days": int(state_days),
            "signal_date": signal_date or None,
            "stable_price_min": stable_price_min,
            "stable_price_max": stable_price_max,
            "stable_range_points": stable_price_max - stable_price_min,
        }
    )
    if context is not None:
        record.update(context)


def _decline_context(
    series: pd.DataFrame,
    *,
    stable_start: int,
    end_position: int,
    trend_lookback: int,
    min_decline_days: int,
    min_decline_pct: float,
) -> dict[str, Any] | None:
    trend_start = max(0, stable_start - trend_lookback)
    before_stable = series.iloc[trend_start:stable_start]
    if before_stable.empty:
        return None
    peak_position = int(before_stable["close"].idxmax())
    decline_section = series.iloc[peak_position : end_position + 1]
    low_position = int(decline_section["close"].idxmin())
    if low_position < stable_start:
        return None
    peak_close = float(series.loc[peak_position, "close"])
    low_close = float(series.loc[low_position, "close"])
    decline_days = stable_start - peak_position
    decline_pct = low_close / peak_close - 1
    by_duration = decline_days >= min_decline_days
    by_drawdown = decline_pct <= -min_decline_pct
    if not (by_duration or by_drawdown):
        return None
    return {
        "decline_start_date": str(series.loc[peak_position, "trade_date"]),
        "decline_start_close": peak_close,
        "decline_low_date": str(series.loc[low_position, "trade_date"]),
        "decline_low_close": low_close,
        "decline_days": int(decline_days),
        "decline_pct": float(decline_pct),
        "downtrend_by_duration": bool(by_duration),
        "downtrend_by_drawdown": bool(by_drawdown),
    }


def _trailing_stable_starts(
    series: pd.DataFrame,
    max_stable_range_points: float,
) -> list[int]:
    """Find every longest trailing stable-window start in linear time.

    Each daily interval contributes ``min(open, close)`` and
    ``max(open, close)``. Two monotonic queues maintain the minimum and maximum
    over the current window. The left boundary only moves forward, so every
    row enters and leaves each queue at most once.
    """
    if series.empty:
        return []

    opens = series["open"].to_numpy(dtype=float)
    closes = series["close"].to_numpy(dtype=float)
    daily_mins = np.minimum(opens, closes)
    daily_maxes = np.maximum(opens, closes)
    min_queue: deque[int] = deque()
    max_queue: deque[int] = deque()
    starts: list[int] = []
    left = 0

    for right in range(len(series)):
        while min_queue and daily_mins[min_queue[-1]] >= daily_mins[right]:
            min_queue.pop()
        min_queue.append(right)
        while max_queue and daily_maxes[max_queue[-1]] <= daily_maxes[right]:
            max_queue.pop()
        max_queue.append(right)

        while min_queue and max_queue and (
            daily_maxes[max_queue[0]] - daily_mins[min_queue[0]]
            > max_stable_range_points
        ):
            if min_queue[0] == left:
                min_queue.popleft()
            if max_queue[0] == left:
                max_queue.popleft()
            left += 1
        starts.append(left)

    return starts


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
