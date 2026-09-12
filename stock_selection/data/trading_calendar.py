"""从正式缓存推导交易日序列，避免把自然日误当成已落库交易日。"""

from __future__ import annotations

from pathlib import Path

from stock_selection.data.tushare_cache import DEFAULT_CACHE_ROOT


def cached_trade_dates(
    *,
    cache_root: str | Path | None = None,
    api_name: str = "daily",
) -> list[str]:
    """返回指定接口在正式缓存中实际存在的交易日，按日期升序排列。"""

    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    data_dir = root / api_name
    if not data_dir.exists():
        return []
    # 同一交易日可能同时存在 parquet/csv 回退文件，集合可避免重复。
    dates = {_date_from_path(path) for path in data_dir.glob("*.*")}
    return sorted(date for date in dates if date is not None)


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


def _date_from_path(path: Path) -> str | None:
    """只接受形如 YYYYMMDD 的缓存文件名，忽略元数据和其他杂项文件。"""
    value = path.stem
    return value if len(value) == 8 and value.isdigit() else None
