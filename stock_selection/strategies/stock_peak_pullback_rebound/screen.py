"""Find historical close-only peak-pullback-rebound patterns."""

from __future__ import annotations

import json
from enum import IntEnum
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from stock_selection.data.tushare_cache import DEFAULT_CACHE_ROOT


DEFAULT_PEAK_LOOKBACK_DAYS = 120
DEFAULT_MAX_REBOUND_DAYS = 40
DEFAULT_MIN_PULLBACK_PCT = 0.10
DEFAULT_MIN_SECOND_PEAK_RATIO = 0.70
DEFAULT_REBOUND_END_DRAWDOWN_PCT = 0.10
DEFAULT_REBOUND_ENDED_STATE_DAYS = 10


class PeakPullbackReboundState(IntEnum):
    """Daily states for the peak-pullback-rebound strategy."""

    REBOUND_ACTIVE = 10
    REBOUND_ENDED = 20


STATE_DESCRIPTIONS = {
    PeakPullbackReboundState.REBOUND_ACTIVE: "高点回落后反弹中",
    PeakPullbackReboundState.REBOUND_ENDED: "反弹结束",
}


STATE_OUTPUT_COLUMNS = [
    "ts_code",
    "stock_name",
    "trade_date",
    "close",
    "state",
    "state_name",
    "state_description",
    "cycle_id",
    "state_start_date",
    "state_days",
    "reference_high_date",
    "reference_high_close",
    "reference_low_date",
    "reference_low_close",
    "rebound_start_date",
    "rebound_high_date",
    "rebound_high_close",
    "pullback_pct",
    "rebound_pct_from_low",
    "drawdown_from_rebound_high",
    "alert_code",
    "alert_message",
]


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


def calculate_peak_pullback_rebound_states(
    stock_daily: pd.DataFrame,
    *,
    as_of_date: str | None = None,
    peak_lookback_days: int = DEFAULT_PEAK_LOOKBACK_DAYS,
    max_rebound_days: int = DEFAULT_MAX_REBOUND_DAYS,
    min_pullback_pct: float = DEFAULT_MIN_PULLBACK_PCT,
    min_second_peak_ratio: float = DEFAULT_MIN_SECOND_PEAK_RATIO,
    rebound_end_drawdown_pct: float = DEFAULT_REBOUND_END_DRAWDOWN_PCT,
    rebound_ended_state_days: int = DEFAULT_REBOUND_ENDED_STATE_DAYS,
    stock_codes: Iterable[str] | None = None,
    stock_basic: pd.DataFrame | None = None,
    include_st: bool = False,
) -> pd.DataFrame:
    """Return one strategy-four state row for every stock trading day."""
    _validate_state_parameters(
        peak_lookback_days=peak_lookback_days,
        max_rebound_days=max_rebound_days,
        min_pullback_pct=min_pullback_pct,
        min_second_peak_ratio=min_second_peak_ratio,
        rebound_end_drawdown_pct=rebound_end_drawdown_pct,
        rebound_ended_state_days=rebound_ended_state_days,
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
        return pd.DataFrame(columns=STATE_OUTPUT_COLUMNS)

    rows: list[dict[str, Any]] = []
    for ts_code, group in data.groupby("ts_code", sort=True):
        rows.extend(
            _calculate_one_stock_states(
                group,
                ts_code=ts_code,
                stock_name=_latest_name(group["stock_name"]),
                peak_lookback_days=peak_lookback_days,
                max_rebound_days=max_rebound_days,
                min_pullback_pct=min_pullback_pct,
                min_second_peak_ratio=min_second_peak_ratio,
                rebound_end_drawdown_pct=rebound_end_drawdown_pct,
                rebound_ended_state_days=rebound_ended_state_days,
            )
        )
    return pd.DataFrame(rows, columns=STATE_OUTPUT_COLUMNS).sort_values(
        ["trade_date", "ts_code"], ascending=[True, True]
    ).reset_index(drop=True)


def latest_peak_pullback_rebound_states(states: pd.DataFrame) -> pd.DataFrame:
    """Return the latest daily state row for every stock."""
    if states is None or states.empty:
        return pd.DataFrame(columns=STATE_OUTPUT_COLUMNS)
    return (
        states.sort_values(["ts_code", "trade_date"])
        .groupby("ts_code", as_index=False, sort=True)
        .tail(1)
        .sort_values("ts_code")
        .reset_index(drop=True)
    )


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


def _calculate_one_stock_states(
    group: pd.DataFrame,
    *,
    ts_code: str,
    stock_name: str,
    peak_lookback_days: int,
    max_rebound_days: int,
    min_pullback_pct: float,
    min_second_peak_ratio: float,
    rebound_end_drawdown_pct: float,
    rebound_ended_state_days: int,
) -> list[dict[str, Any]]:
    series = group.sort_values("trade_date").reset_index(drop=True)
    closes = series["close"].to_numpy(dtype=float)
    dates = series["trade_date"].astype(str).to_numpy()
    records = [
        _base_state_record(
            ts_code=ts_code,
            stock_name=stock_name,
            trade_date=dates[position],
            close=closes[position],
        )
        for position in range(len(series))
    ]

    phase = "normal"
    next_cycle_id = 0
    cycle_id = 0
    high_position = -1
    high_close = np.nan
    low_position = -1
    low_close = np.nan
    rebound_start_position = -1
    rebound_high_position = -1
    rebound_high_close = np.nan
    state_start_position = -1
    ended_state_days = 0

    def reset_cycle() -> None:
        nonlocal phase
        nonlocal cycle_id
        nonlocal high_position
        nonlocal high_close
        nonlocal low_position
        nonlocal low_close
        nonlocal rebound_start_position
        nonlocal rebound_high_position
        nonlocal rebound_high_close
        nonlocal state_start_position
        nonlocal ended_state_days
        phase = "normal"
        cycle_id = 0
        high_position = -1
        high_close = np.nan
        low_position = -1
        low_close = np.nan
        rebound_start_position = -1
        rebound_high_position = -1
        rebound_high_close = np.nan
        state_start_position = -1
        ended_state_days = 0

    def start_candidate(position: int, close: float) -> None:
        nonlocal phase
        nonlocal next_cycle_id
        nonlocal cycle_id
        nonlocal high_position
        nonlocal high_close
        next_cycle_id += 1
        cycle_id = next_cycle_id
        high_position = position
        high_close = close
        phase = "candidate"

    for position, close in enumerate(closes):
        if phase == "ended" and ended_state_days >= rebound_ended_state_days:
            reset_cycle()

        if phase == "normal":
            if position < peak_lookback_days:
                continue
            previous_high = float(
                np.max(closes[position - peak_lookback_days : position])
            )
            if close + 1e-12 < previous_high:
                continue
            start_candidate(position, close)
            _set_state_references(
                records[position],
                cycle_id=cycle_id,
                high_position=high_position,
                low_position=low_position,
                rebound_start_position=rebound_start_position,
                rebound_high_position=rebound_high_position,
                dates=dates,
                closes=closes,
            )
            continue

        if phase == "candidate":
            if close >= high_close:
                high_position = position
                high_close = close
            pullback_pct = close / high_close - 1.0
            _set_state_references(
                records[position],
                cycle_id=cycle_id,
                high_position=high_position,
                low_position=low_position,
                rebound_start_position=rebound_start_position,
                rebound_high_position=rebound_high_position,
                dates=dates,
                closes=closes,
                pullback_pct=pullback_pct,
            )
            if pullback_pct > -min_pullback_pct + 1e-12:
                continue
            low_position = position
            low_close = close
            phase = "waiting_rebound"
            _set_state_references(
                records[position],
                cycle_id=cycle_id,
                high_position=high_position,
                low_position=low_position,
                rebound_start_position=rebound_start_position,
                rebound_high_position=rebound_high_position,
                dates=dates,
                closes=closes,
                pullback_pct=pullback_pct,
            )
            continue

        if close > high_close + 1e-12:
            _set_state_references(
                records[position],
                cycle_id=cycle_id,
                high_position=high_position,
                low_position=low_position,
                rebound_start_position=rebound_start_position,
                rebound_high_position=rebound_high_position,
                dates=dates,
                closes=closes,
                alert_code="BREAK_PREVIOUS_HIGH",
                alert_message="反弹突破前高，原高点定义可能无效",
            )
            reset_cycle()
            start_candidate(position, close)
            continue

        pullback_pct = low_close / high_close - 1.0
        rebound_pct_from_low = close / low_close - 1.0

        if phase == "waiting_rebound":
            if position - high_position > max_rebound_days:
                reset_cycle()
                continue
            if close <= low_close:
                low_position = position
                low_close = close
                pullback_pct = low_close / high_close - 1.0
                rebound_pct_from_low = 0.0
            rebound_reaches_reference = (
                close / high_close + 1e-12 >= min_second_peak_ratio
            )
            if (
                position > low_position
                and close > low_close + 1e-12
                and rebound_reaches_reference
            ):
                phase = "active"
                rebound_start_position = position
                rebound_high_position = position
                rebound_high_close = close
                state_start_position = position
                _set_daily_state(
                    records[position],
                    PeakPullbackReboundState.REBOUND_ACTIVE,
                    cycle_id=cycle_id,
                    state_start_position=state_start_position,
                    current_position=position,
                    high_position=high_position,
                    low_position=low_position,
                    rebound_start_position=rebound_start_position,
                    rebound_high_position=rebound_high_position,
                    dates=dates,
                    closes=closes,
                    pullback_pct=pullback_pct,
                    rebound_pct_from_low=rebound_pct_from_low,
                    drawdown_from_rebound_high=0.0,
                )
            else:
                _set_state_references(
                    records[position],
                    cycle_id=cycle_id,
                    high_position=high_position,
                    low_position=low_position,
                    rebound_start_position=rebound_start_position,
                    rebound_high_position=rebound_high_position,
                    dates=dates,
                    closes=closes,
                    pullback_pct=pullback_pct,
                    rebound_pct_from_low=rebound_pct_from_low,
                )
            continue

        if phase == "active":
            if close >= rebound_high_close:
                rebound_high_position = position
                rebound_high_close = close
            drawdown = close / rebound_high_close - 1.0
            if drawdown <= -rebound_end_drawdown_pct + 1e-12:
                phase = "ended"
                state_start_position = position
                ended_state_days = 1
                state = PeakPullbackReboundState.REBOUND_ENDED
            else:
                state = PeakPullbackReboundState.REBOUND_ACTIVE
            _set_daily_state(
                records[position],
                state,
                cycle_id=cycle_id,
                state_start_position=state_start_position,
                current_position=position,
                high_position=high_position,
                low_position=low_position,
                rebound_start_position=rebound_start_position,
                rebound_high_position=rebound_high_position,
                dates=dates,
                closes=closes,
                pullback_pct=pullback_pct,
                rebound_pct_from_low=rebound_pct_from_low,
                drawdown_from_rebound_high=drawdown,
            )
            continue

        if phase == "ended":
            ended_state_days += 1
            drawdown = close / rebound_high_close - 1.0
            _set_daily_state(
                records[position],
                PeakPullbackReboundState.REBOUND_ENDED,
                cycle_id=cycle_id,
                state_start_position=state_start_position,
                current_position=position,
                high_position=high_position,
                low_position=low_position,
                rebound_start_position=rebound_start_position,
                rebound_high_position=rebound_high_position,
                dates=dates,
                closes=closes,
                pullback_pct=pullback_pct,
                rebound_pct_from_low=rebound_pct_from_low,
                drawdown_from_rebound_high=drawdown,
            )

    return records


def _base_state_record(
    *,
    ts_code: str,
    stock_name: str,
    trade_date: str,
    close: float,
) -> dict[str, Any]:
    return {
        "ts_code": ts_code,
        "stock_name": stock_name,
        "trade_date": trade_date,
        "close": float(close),
        "state": None,
        "state_name": "",
        "state_description": "",
        "cycle_id": 0,
        "state_start_date": "",
        "state_days": 0,
        "reference_high_date": "",
        "reference_high_close": None,
        "reference_low_date": "",
        "reference_low_close": None,
        "rebound_start_date": "",
        "rebound_high_date": "",
        "rebound_high_close": None,
        "pullback_pct": None,
        "rebound_pct_from_low": None,
        "drawdown_from_rebound_high": None,
        "alert_code": "",
        "alert_message": "",
    }


def _set_daily_state(
    record: dict[str, Any],
    state: PeakPullbackReboundState,
    *,
    cycle_id: int,
    state_start_position: int,
    current_position: int,
    high_position: int,
    low_position: int,
    rebound_start_position: int,
    rebound_high_position: int,
    dates: np.ndarray,
    closes: np.ndarray,
    pullback_pct: float,
    rebound_pct_from_low: float,
    drawdown_from_rebound_high: float,
) -> None:
    record["state"] = int(state)
    record["state_name"] = state.name
    record["state_description"] = STATE_DESCRIPTIONS[state]
    record["state_start_date"] = str(dates[state_start_position])
    record["state_days"] = int(current_position - state_start_position + 1)
    _set_state_references(
        record,
        cycle_id=cycle_id,
        high_position=high_position,
        low_position=low_position,
        rebound_start_position=rebound_start_position,
        rebound_high_position=rebound_high_position,
        dates=dates,
        closes=closes,
        pullback_pct=pullback_pct,
        rebound_pct_from_low=rebound_pct_from_low,
        drawdown_from_rebound_high=drawdown_from_rebound_high,
    )


def _set_state_references(
    record: dict[str, Any],
    *,
    cycle_id: int,
    high_position: int,
    low_position: int,
    rebound_start_position: int,
    rebound_high_position: int,
    dates: np.ndarray,
    closes: np.ndarray,
    pullback_pct: float | None = None,
    rebound_pct_from_low: float | None = None,
    drawdown_from_rebound_high: float | None = None,
    alert_code: str = "",
    alert_message: str = "",
) -> None:
    record["cycle_id"] = int(cycle_id)
    if high_position >= 0:
        record["reference_high_date"] = str(dates[high_position])
        record["reference_high_close"] = float(closes[high_position])
    if low_position >= 0:
        record["reference_low_date"] = str(dates[low_position])
        record["reference_low_close"] = float(closes[low_position])
    if rebound_start_position >= 0:
        record["rebound_start_date"] = str(dates[rebound_start_position])
    if rebound_high_position >= 0:
        record["rebound_high_date"] = str(dates[rebound_high_position])
        record["rebound_high_close"] = float(closes[rebound_high_position])
    record["pullback_pct"] = pullback_pct
    record["rebound_pct_from_low"] = rebound_pct_from_low
    record["drawdown_from_rebound_high"] = drawdown_from_rebound_high
    record["alert_code"] = alert_code
    record["alert_message"] = alert_message


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


def _validate_state_parameters(**parameters: Any) -> None:
    for name in (
        "peak_lookback_days",
        "max_rebound_days",
        "rebound_ended_state_days",
    ):
        if int(parameters[name]) <= 0:
            raise ValueError(f"{name} must be greater than zero")
    for name in (
        "min_pullback_pct",
        "min_second_peak_ratio",
        "rebound_end_drawdown_pct",
    ):
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
