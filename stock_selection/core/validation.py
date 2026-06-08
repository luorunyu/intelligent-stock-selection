"""Input DataFrame validation and normalization."""

from __future__ import annotations

import pandas as pd

from stock_selection.core.schema import (
    DATE_COL,
    PRICE_COLUMNS,
    REQUIRED_PRICE_COLUMNS,
    SYMBOL_COL,
    VOLUME_COL,
)


def normalize_price_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Return a validated, sorted copy of a long-form daily price frame."""

    validate_price_frame(df)
    out = df.copy()
    out[DATE_COL] = pd.to_datetime(out[DATE_COL])
    out[SYMBOL_COL] = out[SYMBOL_COL].astype(str)
    return out.sort_values([SYMBOL_COL, DATE_COL]).reset_index(drop=True)


def validate_price_frame(df: pd.DataFrame) -> None:
    """Validate required columns and obvious data issues."""

    missing = [col for col in REQUIRED_PRICE_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    duplicated = df.duplicated([DATE_COL, SYMBOL_COL])
    if duplicated.any():
        sample = df.loc[duplicated, [DATE_COL, SYMBOL_COL]].head(5).to_dict("records")
        raise ValueError(f"Duplicate date/symbol rows found, sample={sample}")

    if df[SYMBOL_COL].isna().any():
        raise ValueError("symbol column contains null values")

    prices = df[PRICE_COLUMNS]
    if prices.isna().any().any():
        bad = prices.columns[prices.isna().any()].tolist()
        raise ValueError(f"Price columns contain null values: {bad}")

    if (prices <= 0).any().any():
        bad = prices.columns[(prices <= 0).any()].tolist()
        raise ValueError(f"Price columns contain non-positive values: {bad}")

    if df[VOLUME_COL].isna().any() or (df[VOLUME_COL] < 0).any():
        raise ValueError("volume column contains null or negative values")


def filter_as_of(df: pd.DataFrame, as_of_date: str | pd.Timestamp | None) -> pd.DataFrame:
    """Return rows visible on or before as_of_date."""

    if as_of_date is None:
        return df
    date = pd.Timestamp(as_of_date)
    return df[pd.to_datetime(df[DATE_COL]) <= date]
