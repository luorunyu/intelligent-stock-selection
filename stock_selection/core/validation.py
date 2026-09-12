"""行情 DataFrame 的字段校验和标准化，阻止错误数据进入研究流程。"""

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
    """校验长表行情后复制、规范日期/代码类型，并按股票和日期排序。"""

    # 先拒绝缺列、重复或不合理价格，避免排序后掩盖源数据问题。
    validate_price_frame(df)
    out = df.copy()
    out[DATE_COL] = pd.to_datetime(out[DATE_COL])
    out[SYMBOL_COL] = out[SYMBOL_COL].astype(str)
    return out.sort_values([SYMBOL_COL, DATE_COL]).reset_index(drop=True)


def validate_price_frame(df: pd.DataFrame) -> None:
    """验证必需字段、主键唯一性、价格正值和成交量非负等基础约束。"""

    missing = [col for col in REQUIRED_PRICE_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    # 日期和代码共同构成日线长表主键；重复行会使后续收益计算失真。
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
    """返回截止某日当时可见的行，用于避免回测或复盘的未来数据泄露。"""

    if as_of_date is None:
        return df
    date = pd.Timestamp(as_of_date)
    return df[pd.to_datetime(df[DATE_COL]) <= date]
