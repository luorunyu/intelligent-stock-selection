"""方案一：先筛选 L2 行业，再筛选行业内同方向个股。"""

from __future__ import annotations

from typing import Any

import pandas as pd

from stock_selection.strategies.industry_index_rebound.screen import (
    calculate_industry_index_screen,
    screen_industry_index_rebound,
)
from stock_selection.strategies.stock_industry_direction.screen import (
    calculate_stock_industry_direction,
    screen_stock_industry_direction,
)


RESULT_COLUMNS = [
    "industry_rank",
    "industry_code",
    "industry_name",
    "industry_rise_from_low_pct",
    "industry_days_since_low",
    "industry_up_days_after_low",
    "industry_window_low_date",
    "ts_code",
    "stock_name",
    "same_direction_ratio",
    "same_direction_days",
    "same_up_days",
    "same_down_days",
    "opposite_direction_days",
    "neutral_days",
    "direction_observations",
    "stock_window",
]


def run_plan_one(
    industry_daily: pd.DataFrame,
    stock_daily: pd.DataFrame,
    industry_dictionary: dict[str, Any],
    *,
    as_of_date: str,
    industry_window: int = 20,
    min_rise_from_low: float = 0.05,
    min_days_since_low: int = 5,
    min_up_days: int = 4,
    stock_window: int = 20,
    min_same_direction_ratio: float = 0.60,
    stock_basic: pd.DataFrame | None = None,
    include_st: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """执行方案一并返回合格行业、候选行业股票指标和最终入选结果。

    返回三张表是为了保持过程可追溯：第一张是策略一合格 L2 行业，第二张是
    这些行业全部当前成分股的策略二指标，第三张是达到同方向比例阈值的最终结果。
    """
    if not 0 <= min_same_direction_ratio <= 1:
        raise ValueError("min_same_direction_ratio must be between 0 and 1")

    industry_metrics = calculate_industry_index_screen(
        industry_daily,
        as_of_date=as_of_date,
        window=industry_window,
    )
    level2_index = industry_dictionary.get("level2_index")
    if not isinstance(level2_index, dict):
        raise ValueError("industry dictionary does not contain level2_index")
    # sw_daily 缓存通常没有 level 字段，因此以 SW2021 字典作为层级权威来源。
    level2_codes = set(level2_index)
    level2 = industry_metrics[
        industry_metrics["index_code"].isin(level2_codes)
        & industry_metrics["data_quality"].eq("ok")
    ].copy()
    qualified_industries = screen_industry_index_rebound(
        level2,
        min_rise_from_low=min_rise_from_low,
        min_days_since_low=min_days_since_low,
        min_up_days=min_up_days,
    )
    qualified_industries = qualified_industries.sort_values(
        ["rise_from_low_pct", "up_days_after_low", "days_since_low", "index_code"],
        ascending=[False, False, False, True],
    ).reset_index(drop=True)
    qualified_industries.insert(0, "industry_rank", range(1, len(qualified_industries) + 1))

    industry_codes = qualified_industries["index_code"].tolist()
    if not industry_codes:
        empty_stock_metrics = pd.DataFrame()
        return qualified_industries, empty_stock_metrics, pd.DataFrame(columns=RESULT_COLUMNS)

    stock_metrics = calculate_stock_industry_direction(
        stock_daily,
        industry_daily,
        industry_dictionary,
        level="L2",
        as_of_date=as_of_date,
        window=stock_window,
        stock_basic=stock_basic,
        include_st=include_st,
        industry_codes=industry_codes,
    )
    selected_stocks = screen_stock_industry_direction(
        stock_metrics,
        min_same_direction_ratio=min_same_direction_ratio,
    )
    result = _join_strategy_results(qualified_industries, selected_stocks, stock_window)
    return qualified_industries, stock_metrics, result


def _join_strategy_results(
    qualified_industries: pd.DataFrame,
    selected_stocks: pd.DataFrame,
    stock_window: int,
) -> pd.DataFrame:
    if selected_stocks.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)
    industry_columns = qualified_industries[
        [
            "industry_rank",
            "index_code",
            "name",
            "rise_from_low_pct",
            "days_since_low",
            "up_days_after_low",
            "window_low_date",
        ]
    ].rename(
        columns={
            "index_code": "industry_code",
            "name": "industry_name_from_strategy_one",
            "rise_from_low_pct": "industry_rise_from_low_pct",
            "days_since_low": "industry_days_since_low",
            "up_days_after_low": "industry_up_days_after_low",
            "window_low_date": "industry_window_low_date",
        }
    )
    result = selected_stocks.merge(industry_columns, on="industry_code", how="inner")
    result["industry_name"] = result["industry_name"].mask(
        result["industry_name"].eq(""), result["industry_name_from_strategy_one"]
    )
    result["stock_window"] = int(stock_window)
    count_columns = [
        "industry_rank",
        "industry_days_since_low",
        "industry_up_days_after_low",
        "same_direction_days",
        "same_up_days",
        "same_down_days",
        "opposite_direction_days",
        "neutral_days",
        "direction_observations",
        "stock_window",
    ]
    result[count_columns] = result[count_columns].astype(int)
    return result[RESULT_COLUMNS].sort_values(
        ["industry_rank", "same_direction_ratio", "direction_observations", "ts_code"],
        ascending=[True, False, False, True],
    ).reset_index(drop=True)
