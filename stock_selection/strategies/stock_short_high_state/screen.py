"""Classify close-only high regions and rebound-peak states."""

from __future__ import annotations

from enum import IntEnum
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from stock_selection.data.tushare_cache import DEFAULT_CACHE_ROOT


DEFAULT_LOOKBACK_DAYS = 120
DEFAULT_CONFIRM_DRAWDOWN_PCT = 0.30
DEFAULT_REBOUND_PCT = 0.10
DEFAULT_HIGH_HEAT_DAYS = 10
DEFAULT_TROUGH_HEAT_DAYS = 5


class StockShortState(IntEnum):
    """Daily states used by the high-region short-side strategy."""

    NORMAL = 0
    HIGH_CANDIDATE = 10
    HIGH_CONFIRMED = 20
    HIGH_DRAWDOWN = 30
    TROUGH_WAIT_REBOUND = 40
    PEAK_1 = 51
    PEAK_2 = 52
    PEAK_3 = 53
    PEAK_N = 59
    PEAK_1_PULLBACK = 61
    PEAK_2_PULLBACK = 62
    PEAK_3_PULLBACK = 63
    PEAK_N_PULLBACK = 69
    INVALID_HIGH_NEW_HIGH = 90


STATE_DESCRIPTIONS = {
    StockShortState.NORMAL: "普通状态",
    StockShortState.HIGH_CANDIDATE: "最高点候选区域",
    StockShortState.HIGH_CONFIRMED: "已确认最高点区域",
    StockShortState.HIGH_DRAWDOWN: "最高点后的确认前回落",
    StockShortState.TROUGH_WAIT_REBOUND: "回落已达阈值，寻找波谷并等待反弹",
    StockShortState.PEAK_1: "第一波峰区域",
    StockShortState.PEAK_2: "第二波峰区域",
    StockShortState.PEAK_3: "第三波峰区域",
    StockShortState.PEAK_N: "第N波峰区域",
    StockShortState.PEAK_1_PULLBACK: "第一波峰结束后的回落",
    StockShortState.PEAK_2_PULLBACK: "第二波峰结束后的回落",
    StockShortState.PEAK_3_PULLBACK: "第三波峰结束后的回落",
    StockShortState.PEAK_N_PULLBACK: "第N波峰结束后的回落",
    StockShortState.INVALID_HIGH_NEW_HIGH: "最高点确认后再次创新高，原判断失效",
}


OUTPUT_COLUMNS = [
    "ts_code",
    "stock_name",
    "trade_date",
    "close",
    "total_mv",
    "circ_mv",
    "state",
    "state_name",
    "state_description",
    "cycle_id",
    "peak_number",
    "state_start_date",
    "state_start_close",
    "reference_high_date",
    "reference_high_close",
    "reference_low_date",
    "reference_low_close",
    "heat",
    "alert_code",
]


def calculate_stock_short_states(
    stock_daily: pd.DataFrame,
    *,
    as_of_date: str | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    confirm_drawdown_pct: float = DEFAULT_CONFIRM_DRAWDOWN_PCT,
    rebound_pct: float = DEFAULT_REBOUND_PCT,
    high_heat_days: int = DEFAULT_HIGH_HEAT_DAYS,
    trough_heat_days: int = DEFAULT_TROUGH_HEAT_DAYS,
    stock_codes: Iterable[str] | None = None,
    stock_basic: pd.DataFrame | None = None,
    include_st: bool = False,
) -> pd.DataFrame:
    """Return one state row for every stock trading day.

    A high candidate starts when the close reaches the highest close of the
    preceding ``lookback_days`` trading days. The candidate is confirmed only
    after price falls ``confirm_drawdown_pct`` from the running high. A rebound
    peak starts after price rises ``rebound_pct`` from the current trough and
    ends after price returns to the close where that rebound peak started.
    """
    _validate_parameters(
        lookback_days=lookback_days,
        confirm_drawdown_pct=confirm_drawdown_pct,
        rebound_pct=rebound_pct,
        high_heat_days=high_heat_days,
        trough_heat_days=trough_heat_days,
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
                lookback_days=lookback_days,
                confirm_drawdown_pct=confirm_drawdown_pct,
                rebound_pct=rebound_pct,
                high_heat_days=high_heat_days,
                trough_heat_days=trough_heat_days,
            )
        )
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS).sort_values(
        ["trade_date", "ts_code"], ascending=[True, True]
    ).reset_index(drop=True)


def latest_stock_short_states(states: pd.DataFrame) -> pd.DataFrame:
    """Return the latest state row for every stock."""
    if states is None or states.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    ordered = states.sort_values(["ts_code", "trade_date"])
    return ordered.groupby("ts_code", sort=True, as_index=False).tail(1).reset_index(
        drop=True
    )


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
        path
        for path in sorted(directory.glob("*.parquet"))
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


def state_for_peak(peak_number: int) -> StockShortState:
    """Return the enum state for a rebound peak number."""
    if peak_number == 1:
        return StockShortState.PEAK_1
    if peak_number == 2:
        return StockShortState.PEAK_2
    if peak_number == 3:
        return StockShortState.PEAK_3
    return StockShortState.PEAK_N


def state_for_peak_pullback(peak_number: int) -> StockShortState:
    """Return the enum state for a post-peak pullback number."""
    if peak_number == 1:
        return StockShortState.PEAK_1_PULLBACK
    if peak_number == 2:
        return StockShortState.PEAK_2_PULLBACK
    if peak_number == 3:
        return StockShortState.PEAK_3_PULLBACK
    return StockShortState.PEAK_N_PULLBACK


def describe_state(state: StockShortState, peak_number: int = 0) -> str:
    """Return a Chinese description, including the exact N when available."""
    if state == StockShortState.PEAK_N and peak_number > 3:
        return f"第{peak_number}波峰区域"
    if state == StockShortState.PEAK_N_PULLBACK and peak_number > 3:
        return f"第{peak_number}波峰结束后的回落"
    return STATE_DESCRIPTIONS[state]


def _calculate_one_stock(
    group: pd.DataFrame,
    *,
    ts_code: str,
    stock_name: str,
    lookback_days: int,
    confirm_drawdown_pct: float,
    rebound_pct: float,
    high_heat_days: int,
    trough_heat_days: int,
) -> list[dict[str, Any]]:
    series = group.sort_values("trade_date").reset_index(drop=True)
    closes = series["close"].to_numpy(dtype=float)
    dates = series["trade_date"].astype(str).to_numpy()
    high_heat_means = (
        pd.Series(closes)
        .shift(1)
        .rolling(high_heat_days, min_periods=high_heat_days)
        .mean()
        .to_numpy()
    )
    trough_heat_means = (
        pd.Series(closes)
        .shift(1)
        .rolling(trough_heat_days, min_periods=trough_heat_days)
        .mean()
        .to_numpy()
    )
    records = [
        _base_record(
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
    candidate_start = -1
    high_position = -1
    high_close = np.nan
    trough_position = -1
    trough_close = np.nan
    peak_number = 0
    peak_start_position = -1
    peak_start_close = np.nan

    for position, close in enumerate(closes):
        if phase == "normal":
            if position < lookback_days:
                continue
            previous_high = float(np.max(closes[position - lookback_days : position]))
            if close < previous_high:
                continue
            next_cycle_id += 1
            cycle_id = next_cycle_id
            candidate_start = position
            high_position = position
            high_close = close
            peak_number = 0
            phase = "candidate"
            _set_record(
                records[position],
                StockShortState.HIGH_CANDIDATE,
                cycle_id=cycle_id,
                state_start_position=candidate_start,
                dates=dates,
                closes=closes,
                reference_high_position=high_position,
                heat=_ratio_heat(close, high_heat_means[position]),
            )
            continue

        if phase == "candidate":
            if close >= high_close:
                high_position = position
                high_close = close
            _set_record(
                records[position],
                StockShortState.HIGH_CANDIDATE,
                cycle_id=cycle_id,
                state_start_position=candidate_start,
                dates=dates,
                closes=closes,
                reference_high_position=high_position,
                heat=_ratio_heat(close, high_heat_means[position]),
            )
            drawdown = 1.0 - close / high_close
            if drawdown + 1e-12 < confirm_drawdown_pct:
                continue

            for historical_position in range(candidate_start, high_position + 1):
                if (
                    records[historical_position]["state"]
                    == int(StockShortState.INVALID_HIGH_NEW_HIGH)
                ):
                    continue
                _set_record(
                    records[historical_position],
                    StockShortState.HIGH_CONFIRMED,
                    cycle_id=cycle_id,
                    state_start_position=candidate_start,
                    dates=dates,
                    closes=closes,
                    reference_high_position=high_position,
                    heat=_ratio_heat(
                        closes[historical_position],
                        high_heat_means[historical_position],
                    ),
                )
            for historical_position in range(high_position + 1, position):
                _set_record(
                    records[historical_position],
                    StockShortState.HIGH_DRAWDOWN,
                    cycle_id=cycle_id,
                    state_start_position=high_position + 1,
                    dates=dates,
                    closes=closes,
                    reference_high_position=high_position,
                )
            trough_position = position
            trough_close = close
            phase = "waiting_rebound"
            _set_record(
                records[position],
                StockShortState.TROUGH_WAIT_REBOUND,
                cycle_id=cycle_id,
                state_start_position=position,
                dates=dates,
                closes=closes,
                reference_high_position=high_position,
                reference_low_position=trough_position,
            )
            continue

        if close > high_close + 1e-12:
            old_cycle_id = cycle_id
            _set_record(
                records[position],
                StockShortState.INVALID_HIGH_NEW_HIGH,
                cycle_id=old_cycle_id,
                peak_number=peak_number,
                state_start_position=position,
                dates=dates,
                closes=closes,
                reference_high_position=high_position,
                reference_low_position=trough_position,
                alert_code="CONFIRMED_HIGH_BROKEN",
            )
            next_cycle_id += 1
            cycle_id = next_cycle_id
            candidate_start = position
            high_position = position
            high_close = close
            trough_position = -1
            trough_close = np.nan
            peak_number = 0
            peak_start_position = -1
            peak_start_close = np.nan
            phase = "candidate"
            continue

        if phase == "waiting_rebound":
            if close <= trough_close:
                trough_position = position
                trough_close = close
            rebound = close / trough_close - 1.0
            if rebound + 1e-12 >= rebound_pct:
                peak_number += 1
                peak_start_position = position
                peak_start_close = close
                phase = "peak"
                _set_record(
                    records[position],
                    state_for_peak(peak_number),
                    cycle_id=cycle_id,
                    peak_number=peak_number,
                    state_start_position=peak_start_position,
                    dates=dates,
                    closes=closes,
                    reference_high_position=high_position,
                    reference_low_position=trough_position,
                    heat=_ratio_heat(close, trough_heat_means[trough_position]),
                )
            else:
                state = (
                    StockShortState.TROUGH_WAIT_REBOUND
                    if peak_number == 0
                    else state_for_peak_pullback(peak_number)
                )
                _set_record(
                    records[position],
                    state,
                    cycle_id=cycle_id,
                    peak_number=peak_number,
                    state_start_position=trough_position,
                    dates=dates,
                    closes=closes,
                    reference_high_position=high_position,
                    reference_low_position=trough_position,
                )
            continue

        if phase == "peak":
            if position > peak_start_position and close <= peak_start_close + 1e-12:
                phase = "waiting_rebound"
                trough_position = position
                trough_close = close
                _set_record(
                    records[position],
                    state_for_peak_pullback(peak_number),
                    cycle_id=cycle_id,
                    peak_number=peak_number,
                    state_start_position=position,
                    dates=dates,
                    closes=closes,
                    reference_high_position=high_position,
                    reference_low_position=trough_position,
                )
            else:
                _set_record(
                    records[position],
                    state_for_peak(peak_number),
                    cycle_id=cycle_id,
                    peak_number=peak_number,
                    state_start_position=peak_start_position,
                    dates=dates,
                    closes=closes,
                    reference_high_position=high_position,
                    reference_low_position=trough_position,
                    heat=_ratio_heat(close, trough_heat_means[trough_position]),
                )

    return records


def _base_record(
    *,
    ts_code: str,
    stock_name: str,
    trade_date: str,
    close: float,
) -> dict[str, Any]:
    state = StockShortState.NORMAL
    return {
        "ts_code": ts_code,
        "stock_name": stock_name,
        "trade_date": trade_date,
        "close": float(close),
        "total_mv": np.nan,
        "circ_mv": np.nan,
        "state": int(state),
        "state_name": state.name,
        "state_description": STATE_DESCRIPTIONS[state],
        "cycle_id": 0,
        "peak_number": 0,
        "state_start_date": "",
        "state_start_close": np.nan,
        "reference_high_date": "",
        "reference_high_close": np.nan,
        "reference_low_date": "",
        "reference_low_close": np.nan,
        "heat": np.nan,
        "alert_code": "",
    }


def _set_record(
    record: dict[str, Any],
    state: StockShortState,
    *,
    cycle_id: int,
    dates: np.ndarray,
    closes: np.ndarray,
    peak_number: int = 0,
    state_start_position: int = -1,
    reference_high_position: int = -1,
    reference_low_position: int = -1,
    heat: float = np.nan,
    alert_code: str = "",
) -> None:
    record.update(
        {
            "state": int(state),
            "state_name": state.name,
            "state_description": describe_state(state, peak_number),
            "cycle_id": int(cycle_id),
            "peak_number": int(peak_number),
            "state_start_date": _position_value(dates, state_start_position, ""),
            "state_start_close": _position_value(
                closes, state_start_position, np.nan, float
            ),
            "reference_high_date": _position_value(
                dates, reference_high_position, ""
            ),
            "reference_high_close": _position_value(
                closes, reference_high_position, np.nan, float
            ),
            "reference_low_date": _position_value(
                dates, reference_low_position, ""
            ),
            "reference_low_close": _position_value(
                closes, reference_low_position, np.nan, float
            ),
            "heat": float(heat) if np.isfinite(heat) else np.nan,
            "alert_code": alert_code,
        }
    )


def _position_value(
    values: np.ndarray,
    position: int,
    default: Any,
    converter: Any | None = None,
) -> Any:
    if position < 0 or position >= len(values):
        return default
    value = values[position]
    return converter(value) if converter is not None else str(value)


def _ratio_heat(value: float, reference: float) -> float:
    if not np.isfinite(reference) or reference <= 0:
        return np.nan
    return float(value / reference - 1.0)


def _validate_parameters(**parameters: Any) -> None:
    for name in ("lookback_days", "high_heat_days", "trough_heat_days"):
        if int(parameters[name]) <= 0:
            raise ValueError(f"{name} must be greater than zero")
    for name in ("confirm_drawdown_pct", "rebound_pct"):
        value = float(parameters[name])
        if not 0 < value < 1:
            raise ValueError(f"{name} must be greater than zero and less than one")


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
