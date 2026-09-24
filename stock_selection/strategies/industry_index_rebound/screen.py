"""策略一：基于申万行业指数近月低点的简单初筛指标。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from stock_selection.data.tushare_cache import DEFAULT_CACHE_ROOT


OUTPUT_COLUMNS = [
    "index_code",
    "name",
    "level",
    "latest_trade_date",
    "current_close",
    "window",
    "window_start_date",
    "window_low_date",
    "window_low_close",
    "days_since_low",
    "rise_from_low_pct",
    "up_days_after_low",
    "up_pct_sum_after_low",
    "down_days_after_low",
    "down_pct_sum_after_low",
    "flat_days_after_low",
    "data_quality",
]


def calculate_industry_index_screen(
    index_data: pd.DataFrame,
    *,
    as_of_date: str | None = None,
    window: int = 20,
) -> pd.DataFrame:
    """按行业指数计算近月低点后的简单涨跌统计。"""
    if window <= 0:
        raise ValueError("window must be positive")
    data = normalise_industry_index_data(index_data)
    if as_of_date is not None:
        target_date = _normalise_date(as_of_date)
        if not target_date:
            raise ValueError("as_of_date must be a valid date")
        data = data[data["trade_date"] <= target_date]
    if data.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    rows: list[dict[str, Any]] = []
    for (index_code, name, level), group in data.groupby(
        ["index_code", "name", "level"], dropna=False, sort=True
    ):
        rows.append(_calculate_one_index(group, index_code, name, level, window))
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS).sort_values(
        ["level", "rise_from_low_pct", "index_code"],
        ascending=[True, False, True],
        na_position="last",
    ).reset_index(drop=True)


def screen_industry_index_rebound(
    metrics: pd.DataFrame,
    *,
    min_rise_from_low: float | None = None,
    min_days_since_low: int | None = None,
    min_up_days: int | None = None,
) -> pd.DataFrame:
    """按简单阈值筛选行业指数初筛结果。"""
    result = metrics.copy()
    if min_rise_from_low is not None:
        result = result[result["rise_from_low_pct"] >= min_rise_from_low]
    if min_days_since_low is not None:
        result = result[result["days_since_low"] >= min_days_since_low]
    if min_up_days is not None:
        result = result[result["up_days_after_low"] >= min_up_days]
    return result.reset_index(drop=True)


def read_cached_industry_index_data(
    *,
    cache_root: str | Path | None = None,
) -> pd.DataFrame:
    """读取缓存目录下所有申万行业指数日线。"""
    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    directory = root / "sw_daily"
    paths = sorted(directory.glob("*.parquet"))
    if not paths:
        raise FileNotFoundError(f"no sw_daily cache files found under {directory}")
    return normalise_industry_index_data(
        pd.concat((pd.read_parquet(path) for path in paths), ignore_index=True)
    )


def normalise_industry_index_data(index_data: pd.DataFrame) -> pd.DataFrame:
    """统一 Tushare 申万行业日线字段，并去重排序。"""
    columns = ["index_code", "name", "level", "trade_date", "close", "pct_chg"]
    if index_data is None or index_data.empty:
        return pd.DataFrame(columns=columns)

    aliases = {
        "index_code": ("ts_code", "index_code"),
        "name": ("name", "index_name"),
        "level": ("level",),
        "trade_date": ("trade_date",),
        "close": ("close",),
        "pct_chg": ("pct_chg", "pct_change", "change_pct"),
    }
    result = pd.DataFrame(index=index_data.index)
    for target, choices in aliases.items():
        source = next((column for column in choices if column in index_data.columns), None)
        result[target] = index_data[source] if source else ""

    result["index_code"] = result["index_code"].fillna("").astype(str).str.strip()
    result["name"] = result["name"].fillna("").astype(str).str.strip()
    result["level"] = result["level"].fillna("").astype(str).str.strip()
    result["trade_date"] = result["trade_date"].map(_normalise_date)
    result["close"] = pd.to_numeric(result["close"], errors="coerce")
    result["pct_chg"] = pd.to_numeric(result["pct_chg"], errors="coerce")
    result = result[result["index_code"].ne("") & result["trade_date"].ne("") & result["close"].notna()]
    result = result.drop_duplicates(["index_code", "trade_date"], keep="last")
    return result[columns].sort_values(["index_code", "trade_date"]).reset_index(drop=True)


def _calculate_one_index(
    group: pd.DataFrame,
    index_code: str,
    name: str,
    level: str,
    window: int,
) -> dict[str, Any]:
    series = group.sort_values("trade_date").tail(window).reset_index(drop=True)
    base = {
        "index_code": index_code,
        "name": name,
        "level": level,
        "latest_trade_date": series["trade_date"].iloc[-1],
        "current_close": float(series["close"].iloc[-1]),
        "window": window,
        "window_start_date": series["trade_date"].iloc[0],
        "window_low_date": None,
        "window_low_close": None,
        "days_since_low": None,
        "rise_from_low_pct": None,
        "up_days_after_low": None,
        "up_pct_sum_after_low": None,
        "down_days_after_low": None,
        "down_pct_sum_after_low": None,
        "flat_days_after_low": None,
        "data_quality": "ok" if len(series) >= window else "insufficient_history",
    }
    if len(series) < window:
        return base

    low_close = float(series["close"].min())
    low_rows = series[series["close"].eq(low_close)]
    low_position = int(low_rows.index[-1])
    low_date = str(series.loc[low_position, "trade_date"])
    after_low = series.iloc[low_position + 1 :]
    pct_chg = _fill_pct_chg(series).iloc[low_position + 1 :]

    base.update(
        {
            "window_low_date": low_date,
            "window_low_close": low_close,
            "days_since_low": int(len(after_low)),
            "rise_from_low_pct": float(series["close"].iloc[-1] / low_close - 1),
            "up_days_after_low": int((pct_chg > 0).sum()),
            "up_pct_sum_after_low": float(pct_chg[pct_chg > 0].sum()),
            "down_days_after_low": int((pct_chg < 0).sum()),
            "down_pct_sum_after_low": float(pct_chg[pct_chg < 0].sum()),
            "flat_days_after_low": int((pct_chg == 0).sum()),
        }
    )
    return base


def _fill_pct_chg(series: pd.DataFrame) -> pd.Series:
    pct_chg = series["pct_chg"].copy()
    calculated = series["close"].pct_change() * 100
    return pct_chg.fillna(calculated).fillna(0.0)


def _normalise_date(value: Any) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip().replace("-", "")
    return text[:8]
