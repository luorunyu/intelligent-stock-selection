"""策略二：计算个股与申万二级或三级行业指数的收益联动。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy as np
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
    "l1_code",
    "l1_name",
    "l2_code",
    "l2_name",
    "l3_code",
    "l3_name",
    "window",
    "observations",
    "direction_observations",
    "same_direction_days",
    "same_up_days",
    "same_down_days",
    "opposite_direction_days",
    "neutral_days",
    "same_direction_ratio",
    "regression_observations",
    "return_correlation",
    "industry_beta",
    "industry_r_squared",
    "regression_quality",
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
    """按当前申万成分关系计算每只股票与行业的最新窗口指标。

    一只股票如果属于多个指定层级行业，会保留多行结果。零涨跌日没有明确方向，
    因此记入 ``neutral_days``，但不进入同方向比例的分母。相关系数、Beta 和
    R² 使用同一窗口内的全部已对齐收益率，包含零收益日。
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
            l1_code=membership.l1_code,
            l1_name=membership.l1_name,
            l2_code=membership.l2_code,
            l2_name=membership.l2_name,
            l3_code=membership.l3_code,
            l3_name=membership.l3_name,
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


def calculate_stock_industry_direction_history(
    stock_daily: pd.DataFrame,
    industry_daily: pd.DataFrame,
    industry_dictionary: dict[str, Any],
    *,
    level: str,
    window: int = 20,
    start_date: str | None = None,
    end_date: str | None = None,
    stock_basic: pd.DataFrame | None = None,
    include_st: bool = False,
    industry_codes: Iterable[str] | None = None,
) -> pd.DataFrame:
    """一次计算所有完整历史滚动窗口。

    返回长表，每行是一个“日期-股票-行业”关系。``start_date`` 只限制
    输出日期，计算该日窗口时仍会使用更早的已加载数据。
    """
    batches = list(
        iter_stock_industry_direction_history(
            stock_daily,
            industry_daily,
            industry_dictionary,
            level=level,
            window=window,
            start_date=start_date,
            end_date=end_date,
            stock_basic=stock_basic,
            include_st=include_st,
            industry_codes=industry_codes,
        )
    )
    if not batches:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    return pd.concat(batches, ignore_index=True)[OUTPUT_COLUMNS].sort_values(
        ["as_of_date", "industry_code", "ts_code"],
        ascending=[True, True, True],
    ).reset_index(drop=True)


def iter_stock_industry_direction_history(
    stock_daily: pd.DataFrame,
    industry_daily: pd.DataFrame,
    industry_dictionary: dict[str, Any],
    *,
    level: str,
    window: int = 20,
    start_date: str | None = None,
    end_date: str | None = None,
    stock_basic: pd.DataFrame | None = None,
    include_st: bool = False,
    industry_codes: Iterable[str] | None = None,
    batch_relationships: int = 50,
) -> Iterator[pd.DataFrame]:
    """分批产生历史滚动长表，供大规模历史输出流式写入。"""
    normalised_level = str(level).strip().upper()
    if normalised_level not in SUPPORTED_LEVELS:
        raise ValueError("level must be L2 or L3")
    if window <= 1:
        raise ValueError("window must be greater than 1")
    if batch_relationships <= 0:
        raise ValueError("batch_relationships must be greater than zero")

    first_output_date = _normalise_optional_date(start_date, "start_date")
    last_output_date = _normalise_optional_date(end_date, "end_date")
    if first_output_date and last_output_date and first_output_date > last_output_date:
        raise ValueError("start_date must be on or before end_date")

    stocks = normalise_stock_daily(stock_daily, stock_basic=stock_basic)
    industries = normalise_industry_daily(industry_daily)
    if last_output_date:
        stocks = stocks[stocks["trade_date"].le(last_output_date)]
        industries = industries[industries["trade_date"].le(last_output_date)]

    memberships = build_stock_industry_memberships(
        industry_dictionary,
        normalised_level,
        industry_codes=industry_codes,
    )
    stock_groups = {
        code: group.set_index("trade_date")
        for code, group in stocks.groupby("ts_code")
    }
    industry_groups = {
        code: group.set_index("trade_date")
        for code, group in industries.groupby("industry_code")
    }
    names = _stock_name_map(stocks, stock_basic)

    frames: list[pd.DataFrame] = []
    for membership in memberships.itertuples(index=False):
        stock_name = names.get(membership.ts_code, "")
        if _is_st_name(stock_name) and not include_st:
            continue
        stock = stock_groups.get(membership.ts_code)
        industry = industry_groups.get(membership.industry_code)
        if stock is None or stock.empty or industry is None or industry.empty:
            continue
        frame = _calculate_one_history(
            stock=stock,
            industry=industry,
            ts_code=membership.ts_code,
            stock_name=stock_name,
            industry_code=membership.industry_code,
            industry_name=membership.industry_name,
            industry_level=normalised_level,
            l1_code=membership.l1_code,
            l1_name=membership.l1_name,
            l2_code=membership.l2_code,
            l2_name=membership.l2_name,
            l3_code=membership.l3_code,
            l3_name=membership.l3_name,
            window=window,
            start_date=first_output_date,
        )
        if not frame.empty:
            frames.append(frame)
        if len(frames) >= batch_relationships:
            yield pd.concat(frames, ignore_index=True)[OUTPUT_COLUMNS]
            frames.clear()

    if frames:
        yield pd.concat(frames, ignore_index=True)[OUTPUT_COLUMNS]


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
    paths = _industry_paths(industry_dictionary)
    rows = []
    for industry_code, entry in sorted(index.items()):
        if selected is not None and industry_code not in selected:
            continue
        if not isinstance(entry, dict):
            continue
        for ts_code in entry.get("stock_codes") or []:
            code = str(ts_code).strip()
            matching_paths = [
                path for path in paths
                if path["ts_code"] == code and path[f"{normalised_level.lower()}_code"] == industry_code
            ]
            if not matching_paths:
                matching_paths = [{
                    "ts_code": code,
                    "l1_code": "",
                    "l1_name": "",
                    "l2_code": industry_code if normalised_level == "L2" else "",
                    "l2_name": str(entry.get("name") or "").strip() if normalised_level == "L2" else "",
                    "l3_code": industry_code if normalised_level == "L3" else "",
                    "l3_name": str(entry.get("name") or "").strip() if normalised_level == "L3" else "",
                }]
            for path in matching_paths:
                rows.append(
                    {
                        "ts_code": code,
                        "industry_code": str(industry_code).strip(),
                        "industry_name": str(entry.get("name") or "").strip(),
                        "l1_code": path["l1_code"],
                        "l1_name": path["l1_name"],
                        "l2_code": path["l2_code"],
                        "l2_name": path["l2_name"],
                        "l3_code": path["l3_code"],
                        "l3_name": path["l3_name"],
                    }
                )
    columns = [
        "ts_code", "industry_code", "industry_name",
        "l1_code", "l1_name", "l2_code", "l2_name", "l3_code", "l3_name",
    ]
    if not rows:
        return pd.DataFrame(columns=columns)
    result = pd.DataFrame(rows, columns=columns)
    group_columns = ["ts_code", "industry_code", "industry_name"]
    result = (
        result.groupby(group_columns, dropna=False, sort=True, as_index=False)
        .agg({
            "l1_code": _join_unique,
            "l1_name": _join_unique,
            "l2_code": _join_unique,
            "l2_name": _join_unique,
            "l3_code": _join_unique,
            "l3_name": _join_unique,
        })
    )
    return result[columns].reset_index(drop=True)


def _industry_paths(industry_dictionary: dict[str, Any]) -> list[dict[str, str]]:
    """Flatten the hierarchy into stock-to-L1/L2/L3 paths."""
    paths: list[dict[str, str]] = []
    level1 = industry_dictionary.get("level1")
    if not isinstance(level1, dict):
        return paths
    for l1_code, l1_entry in level1.items():
        if not isinstance(l1_entry, dict):
            continue
        for l2_code, l2_entry in (l1_entry.get("children") or {}).items():
            if not isinstance(l2_entry, dict):
                continue
            for l3_code, l3_entry in (l2_entry.get("children") or {}).items():
                if not isinstance(l3_entry, dict):
                    continue
                for ts_code in l3_entry.get("stock_codes") or []:
                    paths.append(
                        {
                            "ts_code": str(ts_code).strip(),
                            "l1_code": str(l1_code).strip(),
                            "l1_name": str(l1_entry.get("name") or "").strip(),
                            "l2_code": str(l2_code).strip(),
                            "l2_name": str(l2_entry.get("name") or "").strip(),
                            "l3_code": str(l3_code).strip(),
                            "l3_name": str(l3_entry.get("name") or "").strip(),
                        }
                    )
    return paths


def _join_unique(values: pd.Series) -> str:
    return "; ".join(sorted({str(value).strip() for value in values if str(value).strip()}))


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
    l1_code: str,
    l1_name: str,
    l2_code: str,
    l2_name: str,
    l3_code: str,
    l3_name: str,
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
            "l1_code": l1_code,
            "l1_name": l1_name,
            "l2_code": l2_code,
            "l2_name": l2_name,
            "l3_code": l3_code,
            "l3_name": l3_name,
            "window": window,
            "observations": 0,
            "direction_observations": 0,
            "regression_observations": 0,
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

    row.update(_calculate_regression_metrics(aligned))

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


def _calculate_one_history(
    *,
    stock: pd.DataFrame,
    industry: pd.DataFrame,
    ts_code: str,
    stock_name: str,
    industry_code: str,
    industry_name: str,
    industry_level: str,
    l1_code: str,
    l1_name: str,
    l2_code: str,
    l2_name: str,
    l3_code: str,
    l3_name: str,
    window: int,
    start_date: str,
) -> pd.DataFrame:
    aligned = stock[["return_pct"]].rename(columns={"return_pct": "stock_return"}).join(
        industry[["return_pct"]].rename(columns={"return_pct": "industry_return"}),
        how="inner",
    )
    aligned = aligned.dropna().sort_index()
    if len(aligned) < window:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    stock_returns = aligned["stock_return"].astype(float) / 100.0
    industry_returns = aligned["industry_return"].astype(float) / 100.0
    directional = stock_returns.ne(0) & industry_returns.ne(0)
    same_up = (stock_returns.gt(0) & industry_returns.gt(0)).astype("int64")
    same_down = (stock_returns.lt(0) & industry_returns.lt(0)).astype("int64")
    opposite = (
        directional & ((stock_returns.gt(0) & industry_returns.lt(0))
                       | (stock_returns.lt(0) & industry_returns.gt(0)))
    ).astype("int64")

    rolling = pd.DataFrame(index=aligned.index)
    rolling["same_up_days"] = same_up.rolling(window, min_periods=window).sum()
    rolling["same_down_days"] = same_down.rolling(window, min_periods=window).sum()
    rolling["opposite_direction_days"] = opposite.rolling(window, min_periods=window).sum()
    rolling["direction_observations"] = (
        directional.astype("int64").rolling(window, min_periods=window).sum()
    )
    rolling["neutral_days"] = window - rolling["direction_observations"]
    rolling["same_direction_days"] = (
        rolling["same_up_days"] + rolling["same_down_days"]
    )
    rolling["same_direction_ratio"] = (
        rolling["same_direction_days"] / rolling["direction_observations"]
    ).where(rolling["direction_observations"].gt(0))

    industry_variance = industry_returns.rolling(window, min_periods=window).var()
    stock_variance = stock_returns.rolling(window, min_periods=window).var()
    covariance = stock_returns.rolling(window, min_periods=window).cov(industry_returns)
    correlation = stock_returns.rolling(window, min_periods=window).corr(industry_returns)
    industry_constant = industry_variance.abs().le(_VARIANCE_EPSILON)
    stock_constant = stock_variance.abs().le(_VARIANCE_EPSILON)
    rolling["industry_beta"] = (covariance / industry_variance).where(~industry_constant)
    rolling["return_correlation"] = correlation.where(
        ~industry_constant & ~stock_constant
    )
    rolling["industry_r_squared"] = rolling["return_correlation"].pow(2).clip(0, 1)
    rolling["regression_quality"] = np.select(
        [industry_constant, stock_constant],
        ["zero_industry_variance", "zero_stock_variance"],
        default="ok",
    )

    rolling = rolling.iloc[window - 1 :].copy()
    if start_date:
        rolling = rolling[rolling.index >= start_date]
    if rolling.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    integer_columns = [
        "direction_observations",
        "same_direction_days",
        "same_up_days",
        "same_down_days",
        "opposite_direction_days",
        "neutral_days",
    ]
    rolling[integer_columns] = rolling[integer_columns].astype("int64")
    rolling.insert(0, "as_of_date", rolling.index.astype(str))
    rolling["ts_code"] = ts_code
    rolling["stock_name"] = stock_name
    rolling["industry_code"] = industry_code
    rolling["industry_name"] = industry_name
    rolling["industry_level"] = industry_level
    rolling["l1_code"] = l1_code
    rolling["l1_name"] = l1_name
    rolling["l2_code"] = l2_code
    rolling["l2_name"] = l2_name
    rolling["l3_code"] = l3_code
    rolling["l3_name"] = l3_name
    rolling["window"] = int(window)
    rolling["observations"] = int(window)
    rolling["regression_observations"] = int(window)
    rolling["data_quality"] = np.where(
        rolling["direction_observations"].gt(0),
        "ok",
        "no_direction_observations",
    )
    rolling["missing_reason"] = np.where(
        rolling["direction_observations"].gt(0),
        "",
        "all aligned returns contain zero",
    )
    rolling["latest_stock_date"] = rolling["as_of_date"]
    rolling["latest_industry_date"] = rolling["as_of_date"]
    return rolling.reset_index(drop=True)


_VARIANCE_EPSILON = 1e-15


def _calculate_regression_metrics(aligned: pd.DataFrame) -> dict[str, Any]:
    """使用日收益率计算带截距一元回归的相关系数、Beta 和 R²。"""
    stock_returns = aligned["stock_return"].astype(float) / 100.0
    industry_returns = aligned["industry_return"].astype(float) / 100.0
    result: dict[str, Any] = {
        "regression_observations": int(len(aligned)),
        "return_correlation": None,
        "industry_beta": None,
        "industry_r_squared": None,
        "regression_quality": "ok",
    }
    industry_variance = float(industry_returns.var(ddof=1))
    stock_variance = float(stock_returns.var(ddof=1))
    if abs(industry_variance) <= _VARIANCE_EPSILON:
        result["regression_quality"] = "zero_industry_variance"
        return result

    covariance = float(stock_returns.cov(industry_returns))
    result["industry_beta"] = covariance / industry_variance
    if abs(stock_variance) <= _VARIANCE_EPSILON:
        result["regression_quality"] = "zero_stock_variance"
        return result

    correlation = float(stock_returns.corr(industry_returns))
    result["return_correlation"] = correlation
    result["industry_r_squared"] = min(1.0, max(0.0, correlation**2))
    return result


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


def _normalise_optional_date(value: Any, parameter_name: str) -> str:
    if value is None:
        return ""
    normalised = _normalise_date(value)
    if not normalised:
        raise ValueError(f"{parameter_name} must be a valid date")
    return normalised
