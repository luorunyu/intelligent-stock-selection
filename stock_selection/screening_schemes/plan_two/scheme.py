"""Plan two: classify currently lendable stocks with strategy five states."""

from __future__ import annotations

from enum import IntEnum
from typing import Any

import numpy as np
import pandas as pd

from stock_selection.strategies.stock_short_high_state.screen import (
    DEFAULT_CONFIRM_DRAWDOWN_PCT,
    DEFAULT_HIGH_HEAT_DAYS,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_REBOUND_PCT,
    DEFAULT_TROUGH_HEAT_DAYS,
    StockShortState,
    calculate_stock_short_states,
    latest_stock_short_states,
    normalise_stock_daily,
)


class PlanTwoClassification(IntEnum):
    """Final plan-two classification values."""

    UNCLASSIFIED = 0
    NON_SHORT_SCOPE = 10
    SHORT_SCOPE = 20
    ALERT = 90


CLASSIFICATION_DESCRIPTIONS = {
    PlanTwoClassification.UNCLASSIFIED: "无法判断",
    PlanTwoClassification.NON_SHORT_SCOPE: "非做空范围",
    PlanTwoClassification.SHORT_SCOPE: "做空范围",
    PlanTwoClassification.ALERT: "策略状态异常",
}

SHORT_SCOPE_STATES = {
    StockShortState.HIGH_CANDIDATE,
    StockShortState.HIGH_CONFIRMED,
    StockShortState.PEAK_1,
    StockShortState.PEAK_2,
    StockShortState.PEAK_3,
    StockShortState.PEAK_N,
}

NON_SHORT_SCOPE_STATES = {
    StockShortState.NORMAL,
    StockShortState.HIGH_DRAWDOWN,
    StockShortState.TROUGH_WAIT_REBOUND,
    StockShortState.PEAK_1_PULLBACK,
    StockShortState.PEAK_2_PULLBACK,
    StockShortState.PEAK_3_PULLBACK,
    StockShortState.PEAK_N_PULLBACK,
}

RESULT_COLUMNS = [
    "lending_date",
    "requested_state_date",
    "market_data_date",
    "state_date",
    "market",
    "security_code",
    "ts_code",
    "security_name",
    "available_quantity",
    "margin_ratio",
    "lending_status",
    "instrument_match_status",
    "history_days",
    "close",
    "state",
    "state_name",
    "state_description",
    "cycle_id",
    "peak_number",
    "heat",
    "reference_high_date",
    "reference_high_close",
    "reference_low_date",
    "reference_low_close",
    "alert_code",
    "classification",
    "classification_name",
    "classification_description",
    "classification_reason",
    "market_data_stale",
]


def run_plan_two(
    lending_data: pd.DataFrame,
    stock_daily: pd.DataFrame,
    *,
    as_of_date: str,
    min_available_quantity: int = 1,
    required_lending_status: str = "正常",
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    confirm_drawdown_pct: float = DEFAULT_CONFIRM_DRAWDOWN_PCT,
    rebound_pct: float = DEFAULT_REBOUND_PCT,
    high_heat_days: int = DEFAULT_HIGH_HEAT_DAYS,
    trough_heat_days: int = DEFAULT_TROUGH_HEAT_DAYS,
    stock_basic: pd.DataFrame | None = None,
    include_st: bool = False,
    allow_stale_market_data: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return eligible lending rows, latest strategy states, and classifications."""
    requested_date = _normalise_date(as_of_date)
    if not requested_date:
        raise ValueError("as_of_date must be a valid date")
    if min_available_quantity <= 0:
        raise ValueError("min_available_quantity must be greater than zero")
    if lending_data is None or lending_data.empty:
        return (
            pd.DataFrame(columns=lending_data.columns if lending_data is not None else []),
            pd.DataFrame(),
            pd.DataFrame(columns=RESULT_COLUMNS),
        )
    required_columns = {
        "lending_date",
        "market",
        "security_code",
        "ts_code",
        "security_name",
        "available_quantity",
        "margin_ratio",
        "lending_status",
    }
    missing = sorted(required_columns.difference(lending_data.columns))
    if missing:
        raise ValueError(f"lending data is missing columns: {', '.join(missing)}")
    lending_dates = {
        _normalise_date(value)
        for value in lending_data["lending_date"].dropna().unique()
        if _normalise_date(value)
    }
    if lending_dates != {requested_date}:
        raise ValueError(
            f"lending data date must be {requested_date}, found "
            f"{', '.join(sorted(lending_dates)) or 'none'}"
        )

    eligible = lending_data[
        lending_data["available_quantity"].ge(min_available_quantity)
        & lending_data["lending_status"].eq(required_lending_status)
    ].copy()
    eligible = eligible.sort_values(
        ["available_quantity", "ts_code"], ascending=[False, True]
    ).reset_index(drop=True)
    if eligible.empty:
        return eligible, pd.DataFrame(), pd.DataFrame(columns=RESULT_COLUMNS)

    daily = normalise_stock_daily(stock_daily, stock_basic=stock_basic)
    daily = daily[daily["trade_date"].le(requested_date)].copy()
    if daily.empty:
        raise ValueError(f"no stock daily data is available on or before {requested_date}")
    market_data_date = str(daily["trade_date"].max())
    market_data_stale = market_data_date < requested_date
    if market_data_stale and not allow_stale_market_data:
        raise ValueError(
            f"lending/state date is {requested_date}, but latest stock daily data is "
            f"{market_data_date}; update market data or use "
            "--allow-stale-market-data"
        )
    effective_state_date = market_data_date if market_data_stale else requested_date

    daily = daily[daily["trade_date"].le(effective_state_date)]
    history_counts = daily.groupby("ts_code").size()
    daily_codes = set(history_counts.index)
    stock_universe_codes = _stock_universe_codes(stock_basic, daily_codes)
    eligible_codes = set(eligible["ts_code"])
    stock_codes = {
        code
        for code in eligible_codes
        if code in stock_universe_codes
        and code in daily_codes
        and int(history_counts.get(code, 0)) > lookback_days
    }
    if not include_st:
        st_codes = set(
            eligible.loc[
                eligible["security_name"].map(_is_st_name),
                "ts_code",
            ]
        )
        stock_codes.difference_update(st_codes)

    if stock_codes:
        states = calculate_stock_short_states(
            daily,
            as_of_date=effective_state_date,
            lookback_days=lookback_days,
            confirm_drawdown_pct=confirm_drawdown_pct,
            rebound_pct=rebound_pct,
            high_heat_days=high_heat_days,
            trough_heat_days=trough_heat_days,
            stock_codes=stock_codes,
            stock_basic=stock_basic,
            include_st=include_st,
        )
        latest_states = latest_stock_short_states(states)
    else:
        latest_states = pd.DataFrame()

    result = _join_results(
        eligible,
        latest_states,
        history_counts=history_counts,
        daily_codes=daily_codes,
        stock_universe_codes=stock_universe_codes,
        requested_date=requested_date,
        market_data_date=market_data_date,
        market_data_stale=market_data_stale,
        lookback_days=lookback_days,
        include_st=include_st,
    )
    return eligible, latest_states, result


def _join_results(
    eligible: pd.DataFrame,
    latest_states: pd.DataFrame,
    *,
    history_counts: pd.Series,
    daily_codes: set[str],
    stock_universe_codes: set[str],
    requested_date: str,
    market_data_date: str,
    market_data_stale: bool,
    lookback_days: int,
    include_st: bool,
) -> pd.DataFrame:
    state_columns = [
        "ts_code",
        "trade_date",
        "close",
        "state",
        "state_name",
        "state_description",
        "cycle_id",
        "peak_number",
        "heat",
        "reference_high_date",
        "reference_high_close",
        "reference_low_date",
        "reference_low_close",
        "alert_code",
    ]
    if latest_states.empty:
        state_view = pd.DataFrame(columns=state_columns)
    else:
        state_view = latest_states[state_columns].copy()
    result = eligible.merge(state_view, on="ts_code", how="left")
    result = result.rename(columns={"trade_date": "state_date"})
    result.insert(1, "requested_state_date", requested_date)
    result.insert(2, "market_data_date", market_data_date)
    result["history_days"] = result["ts_code"].map(history_counts).fillna(0).astype(int)
    result["market_data_stale"] = bool(market_data_stale)

    classifications = [
        _classify_row(
            row,
            daily_codes=daily_codes,
            stock_universe_codes=stock_universe_codes,
            lookback_days=lookback_days,
            include_st=include_st,
        )
        for row in result.itertuples(index=False)
    ]
    result["instrument_match_status"] = [item[0] for item in classifications]
    result["classification"] = [int(item[1]) for item in classifications]
    result["classification_name"] = [item[1].name for item in classifications]
    result["classification_description"] = [
        CLASSIFICATION_DESCRIPTIONS[item[1]] for item in classifications
    ]
    result["classification_reason"] = [item[2] for item in classifications]

    for column in RESULT_COLUMNS:
        if column not in result.columns:
            result[column] = np.nan
    classification_order = {
        int(PlanTwoClassification.SHORT_SCOPE): 0,
        int(PlanTwoClassification.ALERT): 1,
        int(PlanTwoClassification.NON_SHORT_SCOPE): 2,
        int(PlanTwoClassification.UNCLASSIFIED): 3,
    }
    result["_classification_order"] = result["classification"].map(
        classification_order
    )
    result = result.sort_values(
        [
            "_classification_order",
            "state",
            "peak_number",
            "heat",
            "available_quantity",
            "ts_code",
        ],
        ascending=[True, True, True, False, False, True],
        na_position="last",
    ).drop(columns="_classification_order")
    return result[RESULT_COLUMNS].reset_index(drop=True)


def _classify_row(
    row: Any,
    *,
    daily_codes: set[str],
    stock_universe_codes: set[str],
    lookback_days: int,
    include_st: bool,
) -> tuple[str, PlanTwoClassification, str]:
    if not row.ts_code or row.ts_code not in stock_universe_codes:
        return (
            "NON_STOCK",
            PlanTwoClassification.UNCLASSIFIED,
            "非A股个股标的，不适用策略五",
        )
    if row.ts_code not in daily_codes:
        return (
            "NO_DAILY",
            PlanTwoClassification.UNCLASSIFIED,
            "本地股票日线中不存在该代码",
        )
    if not include_st and _is_st_name(row.security_name):
        return (
            "ST_EXCLUDED",
            PlanTwoClassification.UNCLASSIFIED,
            "默认排除ST股票",
        )
    if int(row.history_days) <= lookback_days:
        return (
            "INSUFFICIENT_HISTORY",
            PlanTwoClassification.UNCLASSIFIED,
            f"历史日线不足{lookback_days + 1}个交易日",
        )
    if pd.isna(row.state):
        return (
            "NO_STRATEGY_STATE",
            PlanTwoClassification.UNCLASSIFIED,
            "未生成策略五最新状态",
        )

    state = StockShortState(int(row.state))
    if state in SHORT_SCOPE_STATES:
        return (
            "MATCHED",
            PlanTwoClassification.SHORT_SCOPE,
            f"策略五状态为{row.state_description}",
        )
    if state in NON_SHORT_SCOPE_STATES:
        return (
            "MATCHED",
            PlanTwoClassification.NON_SHORT_SCOPE,
            f"策略五状态为{row.state_description}",
        )
    if state == StockShortState.INVALID_HIGH_NEW_HIGH:
        return (
            "MATCHED",
            PlanTwoClassification.ALERT,
            "最高点确认后再次创新高，原最高点判断失效",
        )
    return (
        "MATCHED",
        PlanTwoClassification.UNCLASSIFIED,
        f"未配置的策略五状态：{state.name}",
    )


def _is_st_name(name: str) -> bool:
    return "ST" in str(name).upper()


def _stock_universe_codes(
    stock_basic: pd.DataFrame | None,
    fallback_codes: set[str],
) -> set[str]:
    if (
        stock_basic is not None
        and not stock_basic.empty
        and "ts_code" in stock_basic.columns
    ):
        return set(stock_basic["ts_code"].fillna("").astype(str).str.strip())
    return fallback_codes


def _normalise_date(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip().replace("-", "")
    return text[:8] if len(text) >= 8 and text[:8].isdigit() else ""
