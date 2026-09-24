"""策略二：计算个股与申万二级或三级行业指数的同方向比例。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from stock_selection.data.tushare_cache import DEFAULT_CACHE_ROOT


SUPPORTED_LEVELS = {"L2": "level2_index", "L3": "level3_index"}
OUTPUT_COLUMNS = [
    "as_of_date",
    "ts_code",
    "stock_name",
    "industry_code",
    "industry_name",
    "industry_level",
    "window",
    "observations",
    "direction_observations",
    "same_direction_days",
    "same_up_days",
    "same_down_days",
    "opposite_direction_days",
    "neutral_days",
    "same_direction_ratio",
    "data_quality",
    "missing_reason",
    "latest_stock_date",
    "latest_industry_date",
]


def calculate_stock_industry_direction(
    stock_daily: pd.DataFrame,
    industry_daily: pd.DataFrame,
    industry_dictionary: dict[str, Any],
    *,
    level: str,
    as_of_date: str | None = None,
    window: int = 20,
    stock_basic: pd.DataFrame | None = None,
    include_st: bool = False,
    industry_codes: Iterable[str] | None = None,
) -> pd.DataFrame:
    """按当前申万成分关系计算每只股票与行业的同方向比例。

    一只股票如果属于多个指定层级行业，会保留多行结果。零涨跌日没有明确方向，
    因此记入 ``neutral_days``，但不进入同方向比例的分母。
    """
    normalised_level = str(level).strip().upper()
    if normalised_level not in SUPPORTED_LEVELS:
        raise ValueError("level must be L2 or L3")
    if window <= 1:
        raise ValueError("window must be greater than 1")

    stocks = normalise_stock_daily(stock_daily, stock_basic=stock_basic)
    industries = normalise_industry_daily(industry_daily)
    target_date = _normalise_date(as_of_date)
    if as_of_date is not None and not target_date:
        raise ValueError("as_of_date must be a valid date")
    if target_date:
        stocks = stocks[stocks["trade_date"] <= target_date]
        industries = industries[industries["trade_date"] <= target_date]
    else:
        dates = pd.concat([stocks["trade_date"], industries["trade_date"]], ignore_index=True)
        target_date = str(dates.max()) if not dates.empty else ""

    memberships = build_stock_industry_memberships(
        industry_dictionary,
        normalised_level,
        industry_codes=industry_codes,
    )
    stock_groups = {code: group.set_index("trade_date") for code, group in stocks.groupby("ts_code")}
    industry_groups = {
        code: group.set_index("trade_date") for code, group in industries.groupby("industry_code")
    }
    names = _stock_name_map(stocks, stock_basic)

    rows = [
        _calculate_one(
            stock=stock_groups.get(membership.ts_code),
            industry=industry_groups.get(membership.industry_code),
            as_of_date=target_date,
            ts_code=membership.ts_code,
            stock_name=names.get(membership.ts_code, ""),
            industry_code=membership.industry_code,
            industry_name=membership.industry_name,
            industry_level=normalised_level,
            window=window,
            include_st=include_st,
        )
        for membership in memberships.itertuples(index=False)
    ]
    if not rows:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS).sort_values(
        ["data_quality", "same_direction_ratio", "industry_code", "ts_code"],
        ascending=[True, False, True, True],
        na_position="last",
    ).reset_index(drop=True)


def screen_stock_industry_direction(
    metrics: pd.DataFrame,
    *,
    min_same_direction_ratio: float | None = None,
) -> pd.DataFrame:
    """只保留数据有效、并达到可选同方向比例阈值的结果。"""
    result = metrics[metrics["data_quality"].eq("ok")].copy()
    if min_same_direction_ratio is not None:
        threshold = float(min_same_direction_ratio)
        if not 0 <= threshold <= 1:
            raise ValueError("min_same_direction_ratio must be between 0 and 1")
        result = result[result["same_direction_ratio"].ge(threshold)]
    return result.sort_values(
        ["same_direction_ratio", "direction_observations", "ts_code"],
        ascending=[False, False, True],
    ).reset_index(drop=True)


def build_stock_industry_memberships(
    industry_dictionary: dict[str, Any],
    level: str,
    *,
    industry_codes: Iterable[str] | None = None,
) -> pd.DataFrame:
    """把行业到股票的字典反转成长表，并保留一股多行业关系。"""
    normalised_level = str(level).strip().upper()
    if normalised_level not in SUPPORTED_LEVELS:
        raise ValueError("level must be L2 or L3")
    index = industry_dictionary.get(SUPPORTED_LEVELS[normalised_level])
    if not isinstance(index, dict):
        raise ValueError(f"industry dictionary does not contain {SUPPORTED_LEVELS[normalised_level]}")
    selected = None if industry_codes is None else {str(code).strip() for code in industry_codes}
    rows = []
    for industry_code, entry in sorted(index.items()):
        if selected is not None and industry_code not in selected:
            continue
        if not isinstance(entry, dict):
            continue
        for ts_code in entry.get("stock_codes") or []:
            rows.append(
                {
                    "ts_code": str(ts_code).strip(),
                    "industry_code": str(industry_code).strip(),
                    "industry_name": str(entry.get("name") or "").strip(),
                }
            )
    columns = ["ts_code", "industry_code", "industry_name"]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns).drop_duplicates().reset_index(drop=True)


def read_cached_direction_inputs(
    *,
    as_of_date: str | None = None,
    cache_root: str | Path | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """按日期批量读取正式缓存，不对全市场股票逐只请求行情。"""
    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    stock_daily = _read_daily_directory(root / "daily", as_of_date)
    industry_daily = _read_daily_directory(root / "sw_daily", as_of_date)
    basic_path = root / "static" / "stock_basic.parquet"
    stock_basic = pd.read_parquet(basic_path) if basic_path.exists() else pd.DataFrame()
    return stock_daily, industry_daily, stock_basic


def load_industry_dictionary(path: str | Path) -> dict[str, Any]:
    """读取申万行业成分大字典。"""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("industry dictionary root must be an object")
    return payload


def normalise_stock_daily(
    stock_daily: pd.DataFrame,
    *,
    stock_basic: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """统一股票日线字段；Tushare 百分点涨跌幅只用于判断正、负、零。"""
    columns = ["ts_code", "stock_name", "trade_date", "return_pct"]
    if stock_daily is None or stock_daily.empty:
        return pd.DataFrame(columns=columns)
    result = pd.DataFrame(
        {
            "ts_code": _series_from(stock_daily, ("ts_code", "stock_code"), "")
            .fillna("")
            .astype(str)
            .str.strip(),
            "stock_name": _series_from(stock_daily, ("stock_name", "name"), "")
            .fillna("")
            .astype(str)
            .str.strip(),
            "trade_date": _series_from(stock_daily, ("trade_date",), "").map(_normalise_date),
            "return_pct": pd.to_numeric(
                _series_from(stock_daily, ("pct_chg", "pct_change", "change_pct"), None),
                errors="coerce",
            ),
        }
    )
    if stock_basic is not None and not stock_basic.empty and {"ts_code", "name"}.issubset(stock_basic.columns):
        basic_names = stock_basic[["ts_code", "name"]].drop_duplicates("ts_code").set_index("ts_code")["name"]
        result["stock_name"] = result["stock_name"].mask(
            result["stock_name"].eq(""), result["ts_code"].map(basic_names)
        )
    result = result[result["ts_code"].ne("") & result["trade_date"].ne("")]
    return result.drop_duplicates(["ts_code", "trade_date"], keep="last").sort_values(
        ["ts_code", "trade_date"]
    ).reset_index(drop=True)


def normalise_industry_daily(industry_daily: pd.DataFrame) -> pd.DataFrame:
    """统一申万行业指数代码、日期和涨跌幅字段。"""
    columns = ["industry_code", "trade_date", "return_pct"]
    if industry_daily is None or industry_daily.empty:
        return pd.DataFrame(columns=columns)
    result = pd.DataFrame(
        {
            "industry_code": _series_from(industry_daily, ("ts_code", "index_code"), "")
            .fillna("")
            .astype(str)
            .str.strip(),
            "trade_date": _series_from(industry_daily, ("trade_date",), "").map(_normalise_date),
            "return_pct": pd.to_numeric(
                _series_from(industry_daily, ("pct_chg", "pct_change", "change_pct"), None),
                errors="coerce",
            ),
        }
    )
    result = result[result["industry_code"].ne("") & result["trade_date"].ne("")]
    return result.drop_duplicates(["industry_code", "trade_date"], keep="last").sort_values(
        ["industry_code", "trade_date"]
    ).reset_index(drop=True)


def _calculate_one(
    *,
    stock: pd.DataFrame | None,
    industry: pd.DataFrame | None,
    as_of_date: str,
    ts_code: str,
    stock_name: str,
    industry_code: str,
    industry_name: str,
    industry_level: str,
    window: int,
    include_st: bool,
) -> dict[str, Any]:
    row = {column: None for column in OUTPUT_COLUMNS}
    row.update(
        {
            "as_of_date": as_of_date,
            "ts_code": ts_code,
            "stock_name": stock_name,
            "industry_code": industry_code,
            "industry_name": industry_name,
            "industry_level": industry_level,
            "window": window,
            "observations": 0,
            "direction_observations": 0,
            "latest_stock_date": _latest_index(stock),
            "latest_industry_date": _latest_index(industry),
        }
    )
    if _is_st_name(stock_name) and not include_st:
        return _mark_unavailable(row, "st_excluded", "ST stock excluded by default")
    if stock is None or stock.empty or stock["return_pct"].notna().sum() == 0:
        return _mark_unavailable(row, "missing_stock_returns", "no cached stock return data")
    if industry is None or industry.empty or industry["return_pct"].notna().sum() == 0:
        return _mark_unavailable(row, "missing_industry_index", "no cached industry index return data")
    if row["latest_stock_date"] != row["latest_industry_date"]:
        return _mark_unavailable(
            row,
            "no_latest_trade",
            f"stock latest date {row['latest_stock_date']} differs from industry {row['latest_industry_date']}",
        )

    aligned = stock[["return_pct"]].rename(columns={"return_pct": "stock_return"}).join(
        industry[["return_pct"]].rename(columns={"return_pct": "industry_return"}),
        how="inner",
    )
    aligned = aligned.dropna().sort_index().tail(window)
    row["observations"] = len(aligned)
    if len(aligned) < window:
        return _mark_unavailable(
            row,
            "insufficient_history",
            f"requires {window} aligned returns but found {len(aligned)}",
        )

    directional = aligned[
        aligned["stock_return"].ne(0) & aligned["industry_return"].ne(0)
    ]
    if directional.empty:
        row["neutral_days"] = len(aligned)
        return _mark_unavailable(row, "no_direction_observations", "all aligned returns contain zero")

    same_up = int(((directional["stock_return"] > 0) & (directional["industry_return"] > 0)).sum())
    same_down = int(((directional["stock_return"] < 0) & (directional["industry_return"] < 0)).sum())
    same = same_up + same_down
    opposite = int(len(directional) - same)
    row.update(
        {
            "direction_observations": int(len(directional)),
            "same_direction_days": same,
            "same_up_days": same_up,
            "same_down_days": same_down,
            "opposite_direction_days": opposite,
            "neutral_days": int(len(aligned) - len(directional)),
            "same_direction_ratio": same / len(directional),
            "data_quality": "ok",
            "missing_reason": "",
        }
    )
    return row


def _read_daily_directory(directory: Path, as_of_date: str | None) -> pd.DataFrame:
    target = _normalise_date(as_of_date)
    paths = [path for path in sorted(directory.glob("*.parquet")) if not target or path.stem <= target]
    if not paths:
        raise FileNotFoundError(f"no cache files found under {directory}")
    return pd.concat((pd.read_parquet(path) for path in paths), ignore_index=True)


def _stock_name_map(stock_daily: pd.DataFrame, stock_basic: pd.DataFrame | None) -> dict[str, str]:
    names = dict(
        zip(
            stock_daily.loc[stock_daily["stock_name"].ne(""), "ts_code"],
            stock_daily.loc[stock_daily["stock_name"].ne(""), "stock_name"],
        )
    )
    if stock_basic is not None and not stock_basic.empty and {"ts_code", "name"}.issubset(stock_basic.columns):
        names.update(dict(zip(stock_basic["ts_code"].astype(str), stock_basic["name"].fillna("").astype(str))))
    return names


def _series_from(data: pd.DataFrame, choices: Iterable[str], default: Any) -> pd.Series:
    column = next((choice for choice in choices if choice in data.columns), None)
    return data[column] if column is not None else pd.Series(default, index=data.index)


def _latest_index(data: pd.DataFrame | None) -> str:
    return "" if data is None or data.empty else str(data.index.max())


def _mark_unavailable(row: dict[str, Any], quality: str, reason: str) -> dict[str, Any]:
    row["data_quality"] = quality
    row["missing_reason"] = reason
    return row


def _is_st_name(name: str) -> bool:
    return "ST" in str(name).upper()


def _normalise_date(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip().replace("-", "")
    return text[:8] if len(text) >= 8 and text[:8].isdigit() else ""
