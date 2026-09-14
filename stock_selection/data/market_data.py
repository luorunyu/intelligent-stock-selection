"""从本地缓存读取个股日K和申万行业日K。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from stock_selection.data.industry_taxonomy import load_sw_catalog, normalise_sw_level
from stock_selection.data.tushare_cache import read_date_range
from stock_selection.data.tushare_registry import get_spec


def load_stock_daily(
    ts_code: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    cache_root: str | Path | None = None,
) -> pd.DataFrame:
    """从按交易日保存的 ``daily`` 缓存拼出一只股票的未复权日K。"""
    return read_date_range(
        "daily",
        start_date=start_date,
        end_date=end_date,
        filters={"ts_code": ts_code},
        required_fields=get_spec("daily").required_fields,
        cache_root=cache_root,
    )


def load_sw_daily(
    *,
    ts_code: str | None = None,
    level: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    cache_root: str | Path | None = None,
) -> pd.DataFrame:
    """读取申万行业日K，并可按行业代码、层级和日期范围过滤。"""
    data = read_date_range(
        "sw_daily",
        start_date=start_date,
        end_date=end_date,
        filters={"ts_code": ts_code} if ts_code is not None else None,
        required_fields=get_spec("sw_daily").required_fields,
        cache_root=cache_root,
    )
    if data.empty:
        return data

    catalog = load_sw_catalog(level=level, cache_root=cache_root)
    if catalog.empty or "index_code" not in catalog.columns:
        return data.iloc[0:0].copy() if level is not None else data

    metadata_columns = [
        column
        for column in ["index_code", "industry_name", "level", "industry_code", "parent_code", "src"]
        if column in catalog.columns
    ]
    catalog = catalog[metadata_columns].drop_duplicates("index_code")
    result = data.merge(catalog, left_on="ts_code", right_on="index_code", how="left")
    if level is not None:
        result = result[result["level"].fillna("").astype(str).str.upper() == normalise_sw_level(level)]
    return result.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
