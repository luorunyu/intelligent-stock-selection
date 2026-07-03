from __future__ import annotations

from pathlib import Path

from stock_selection.data.tushare_cache import DEFAULT_CACHE_ROOT


def cached_trade_dates(
    *,
    cache_root: str | Path | None = None,
    api_name: str = "daily",
) -> list[str]:
    """Return sorted trade dates available in the formal cache."""

    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    data_dir = root / api_name
    if not data_dir.exists():
        return []
    dates = {_date_from_path(path) for path in data_dir.glob("*.*")}
    return sorted(date for date in dates if date is not None)


def resolve_cached_offsets(
    trade_date: str,
    offsets: list[int],
    *,
    cache_root: str | Path | None = None,
    api_name: str = "daily",
) -> dict[int, str | None]:
    """Map T+ offsets to cached trading dates.

    Offsets are counted from the cached trading-date sequence. If a future
    offset is not yet cached, the value is ``None``.
    """

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
    dates = cached_trade_dates(cache_root=cache_root, api_name=api_name)
    return dates[-1] if dates else None


def _date_from_path(path: Path) -> str | None:
    value = path.stem
    return value if len(value) == 8 and value.isdigit() else None
