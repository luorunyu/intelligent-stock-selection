"""从正式缓存推导交易日序列，避免把自然日误当成已落库交易日。"""

from __future__ import annotations

from pathlib import Path

from stock_selection.data.tushare_cache import (
    complete_trade_dates,
    latest_complete_trade_date as latest_common_trade_date,
    list_dataset_dates,
)


def cached_trade_dates(
    *,
    cache_root: str | Path | None = None,
    api_name: str = "daily",
) -> list[str]:
    """返回指定接口在正式缓存中实际存在的交易日，按日期升序排列。"""

    return list_dataset_dates(api_name, cache_root=cache_root)


def cached_common_trade_dates(
    api_names: list[str] | tuple[str, ...] = ("daily", "sw_daily"),
    *,
    cache_root: str | Path | None = None,
) -> list[str]:
    """返回个股和申万行业数据共同可用的交易日。"""
    return complete_trade_dates(api_names, cache_root=cache_root)


def resolve_cached_offsets(
    trade_date: str,
    offsets: list[int],
    *,
    cache_root: str | Path | None = None,
    api_name: str = "daily",
) -> dict[int, str | None]:
    """将 T+偏移映射为缓存中的交易日；尚未缓存的未来日期返回 ``None``。"""

    dates = cached_trade_dates(cache_root=cache_root, api_name=api_name)
    if trade_date not in dates:
        return {offset: None for offset in offsets}
    base_index = dates.index(trade_date)
    resolved: dict[int, str | None] = {}
    for offset in offsets:
        target_index = base_index + offset
        resolved[offset] = dates[target_index] if target_index < len(dates) else None
    return resolved


def latest_cached_trade_date(*, cache_root: str | Path | None = None, api_name: str = "daily") -> str | None:
    """返回某接口最近已落库的交易日；缓存为空时返回 ``None``。"""
    dates = cached_trade_dates(cache_root=cache_root, api_name=api_name)
    return dates[-1] if dates else None


def latest_complete_trade_date(
    api_names: list[str] | tuple[str, ...] = ("daily", "sw_daily"),
    *,
    cache_root: str | Path | None = None,
) -> str | None:
    """返回个股和申万行业数据共同存在的最近交易日。"""
    return latest_common_trade_date(api_names, cache_root=cache_root)
