"""Simple cycle detection helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd

from stock_selection.core.schema import CLOSE_COL, DATE_COL, SYMBOL_COL
from stock_selection.core.validation import filter_as_of, normalize_price_frame


def analyze_cycle_position(
    df: pd.DataFrame,
    symbol: str,
    price_col: str = CLOSE_COL,
    min_period: int = 20,
    max_period: int = 120,
    window: int = 252,
    periodicity_threshold: float = 0.30,
    bottom_quantile: float = 0.25,
    top_quantile: float = 0.75,
    as_of_date: str | pd.Timestamp | None = None,
) -> dict[str, object]:
    """Analyze whether one stock is cyclical and where it sits in the cycle.

    The method is intentionally small and transparent:
    1. use only data visible on or before ``as_of_date``;
    2. detect the dominant period with FFT on detrended log prices;
    3. classify the current location by the close price's percentile within
       the lookback window.
    """

    data = _prepared_symbol_frame(df, symbol, price_col, window, as_of_date)
    if len(data) < max(min_period * 2, 40):
        return _empty_result(symbol, as_of_date, "insufficient_history")

    prices = data[price_col].astype(float).to_numpy()
    dominant_period, periodicity_score = _dominant_period_score(prices, min_period, max_period)
    periodic = bool(periodicity_score >= periodicity_threshold)

    low = float(np.nanmin(prices))
    high = float(np.nanmax(prices))
    current_price = float(prices[-1])
    price_position = 0.5 if high <= low else float((current_price - low) / (high - low))
    cycle_position = classify_cycle_position(price_position, bottom_quantile, top_quantile)

    return {
        "symbol": symbol,
        "as_of_date": pd.Timestamp(data[DATE_COL].iloc[-1]),
        "periodic": periodic,
        "cycle_position": cycle_position if periodic else "not_periodic",
        "dominant_period": dominant_period,
        "periodicity_score": periodicity_score,
        "price_position": price_position,
        "current_price": current_price,
        "window_start": pd.Timestamp(data[DATE_COL].iloc[0]),
        "window_end": pd.Timestamp(data[DATE_COL].iloc[-1]),
        "reason": "ok" if periodic else "weak_periodicity",
    }


def analyze_all_cycles(
    df: pd.DataFrame,
    symbols: list[str] | None = None,
    as_of_date: str | pd.Timestamp | None = None,
    **kwargs,
) -> pd.DataFrame:
    """Analyze cycle state for every requested symbol."""

    data = filter_as_of(normalize_price_frame(df), as_of_date)
    target_symbols = symbols or sorted(data[SYMBOL_COL].dropna().astype(str).unique().tolist())
    rows = [analyze_cycle_position(data, symbol, as_of_date=as_of_date, **kwargs) for symbol in target_symbols]
    return pd.DataFrame(rows)


def classify_cycle_position(
    price_position: float,
    bottom_quantile: float = 0.25,
    top_quantile: float = 0.75,
) -> str:
    """Classify a normalized price location as bottom, middle, or top."""

    if price_position <= bottom_quantile:
        return "bottom"
    if price_position >= top_quantile:
        return "top"
    return "middle"


def _prepared_symbol_frame(
    df: pd.DataFrame,
    symbol: str,
    price_col: str,
    window: int,
    as_of_date: str | pd.Timestamp | None,
) -> pd.DataFrame:
    data = filter_as_of(normalize_price_frame(df), as_of_date)
    return data[data[SYMBOL_COL].astype(str) == str(symbol)].sort_values(DATE_COL).tail(window)


def _dominant_period_score(
    prices: np.ndarray,
    min_period: int,
    max_period: int,
) -> tuple[float, float]:
    log_prices = np.log(prices)
    x = np.arange(len(log_prices), dtype=float)
    trend = np.polyval(np.polyfit(x, log_prices, deg=1), x)
    detrended = log_prices - trend
    detrended = detrended - np.nanmean(detrended)

    spectrum = np.fft.rfft(detrended)
    power = np.abs(spectrum) ** 2
    freqs = np.fft.rfftfreq(len(detrended), d=1.0)
    periods = np.divide(1.0, freqs, out=np.full_like(freqs, np.inf), where=freqs != 0)

    mask = (periods >= min_period) & (periods <= max_period)
    if not mask.any() or power[mask].sum() == 0:
        return float("nan"), 0.0

    masked_power = power[mask]
    masked_periods = periods[mask]
    idx = int(np.argmax(masked_power))
    score = float(masked_power[idx] / masked_power.sum())
    return float(masked_periods[idx]), score


def _empty_result(symbol: str, as_of_date, reason: str) -> dict[str, object]:
    return {
        "symbol": symbol,
        "as_of_date": pd.Timestamp(as_of_date) if as_of_date is not None else pd.NaT,
        "periodic": False,
        "cycle_position": "unknown",
        "dominant_period": np.nan,
        "periodicity_score": 0.0,
        "price_position": np.nan,
        "current_price": np.nan,
        "window_start": pd.NaT,
        "window_end": pd.NaT,
        "reason": reason,
    }
