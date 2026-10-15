"""Backtest short trades formed from strategy-five state transitions."""

from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
import pandas as pd

from stock_selection.strategies.stock_short_high_state.screen import (
    StockShortState,
)


@dataclass(frozen=True)
class ShortTradeBacktestConfig:
    candidate_entry_state: StockShortState = (
        StockShortState.HIGH_CANDIDATE_PULLBACK
    )
    candidate_exit_states: frozenset[StockShortState] = frozenset(
        {
            StockShortState.TROUGH_CANDIDATE,
            StockShortState.TROUGH_REGION,
        }
    )
    candidate_exit_label: str = "TROUGH_CANDIDATE_OR_REGION"
    peak_entry_states: frozenset[StockShortState] = frozenset(
        {
            StockShortState.PEAK_1,
            StockShortState.PEAK_2,
            StockShortState.PEAK_3,
            StockShortState.PEAK_N,
        }
    )
    peak_exit_state: StockShortState = StockShortState.TROUGH_REGION
    invalidation_state: StockShortState = StockShortState.INVALID_HIGH_NEW_HIGH


BACKTEST_CONFIG = ShortTradeBacktestConfig()
ENTRY_STATES = {
    int(BACKTEST_CONFIG.candidate_entry_state),
    *(int(state) for state in BACKTEST_CONFIG.peak_entry_states),
}
PEAK_ENTRY_STATES = {int(state) for state in BACKTEST_CONFIG.peak_entry_states}
INVALID_STATE = int(BACKTEST_CONFIG.invalidation_state)
DEFAULT_MARKET_CAP_THRESHOLD = 3_000_000.0


def build_short_trade_backtest(
    states: pd.DataFrame,
    *,
    as_of_date: str,
    config: ShortTradeBacktestConfig = BACKTEST_CONFIG,
) -> dict[str, Any]:
    """Build state-transition short trades and aggregate performance metrics."""
    data = _normalise_states(states)
    trades: list[dict[str, Any]] = []
    if not data.empty:
        for _, stock_rows in data.groupby("ts_code", sort=True):
            stock_rows = stock_rows.sort_values("trade_date").reset_index(drop=True)
            trades.extend(_candidate_trades(stock_rows, config=config))
            trades.extend(_peak_trades(stock_rows, config=config))

    trades.sort(
        key=lambda trade: (
            trade["entry_date"],
            trade["ts_code"],
            trade["cycle_id"],
            trade["peak_number"],
        )
    )
    return {
        "metadata": {
            "report_type": "stock_short_state_trade_backtest",
            "as_of_date": str(as_of_date),
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "entry_states": [
                config.candidate_entry_state.name,
                *(state.name for state in sorted(
                    config.peak_entry_states,
                    key=lambda state: int(state),
                )),
            ],
            "exit_states": list(
                dict.fromkeys(
                    [
                        *(
                            state.name
                            for state in sorted(
                                config.candidate_exit_states,
                                key=lambda state: int(state),
                            )
                        ),
                        config.peak_exit_state.name,
                    ]
                )
            ),
            "invalidation_state": config.invalidation_state.name,
            "return_formula": "1 - exit_close / entry_close",
        },
        "summary": _summarise_trades(trades),
        "by_entry_state": _summarise_by_entry_state(trades),
        "trades": trades,
    }


def split_short_trade_backtest_by_average_market_cap(
    backtest: dict[str, Any],
    *,
    threshold_total_mv: float = DEFAULT_MARKET_CAP_THRESHOLD,
) -> dict[str, dict[str, Any]]:
    """Split trades below and at/above an average total-market-cap threshold."""
    if not np.isfinite(threshold_total_mv) or threshold_total_mv <= 0:
        raise ValueError("threshold_total_mv must be a positive finite number")

    trades = list(backtest.get("trades", []))
    missing_count = sum(
        _positive_number(trade.get("average_total_mv")) is None for trade in trades
    )
    threshold_yi = threshold_total_mv / 10_000.0
    threshold_text = f"{threshold_yi:g}亿元"
    segments = {
        "below": {
            "comparison": "<",
            "label": f"平均总市值低于{threshold_text}",
            "trades": [
                trade
                for trade in trades
                if (
                    (average_mv := _positive_number(trade.get("average_total_mv")))
                    is not None
                    and average_mv < threshold_total_mv
                )
            ],
        },
        "at_least": {
            "comparison": ">=",
            "label": f"平均总市值{threshold_text}及以上",
            "trades": [
                trade
                for trade in trades
                if (
                    (average_mv := _positive_number(trade.get("average_total_mv")))
                    is not None
                    and average_mv >= threshold_total_mv
                )
            ],
        },
    }
    result: dict[str, dict[str, Any]] = {}
    for key, segment in segments.items():
        selected = segment["trades"]
        result[key] = {
            "metadata": {
                **backtest.get("metadata", {}),
                "report_type": "stock_short_state_trade_backtest_market_cap",
                "market_cap_filter": {
                    "metric": "average_total_mv",
                    "threshold_total_mv": float(threshold_total_mv),
                    "comparison": segment["comparison"],
                    "label": segment["label"],
                    "missing_market_cap_trades_excluded": int(missing_count),
                },
            },
            "summary": _summarise_trades(selected),
            "by_entry_state": _summarise_by_entry_state(selected),
            "trades": selected,
        }
    return result


def filter_short_trade_backtest_by_stock_pool(
    backtest: dict[str, Any],
    stock_codes: Iterable[str],
    *,
    pool_label: str,
    pool_date: str,
    source_file: str,
    selection_rule: str,
    source_record_count: int | None = None,
    matched_state_stock_count: int | None = None,
) -> dict[str, Any]:
    """Filter an existing backtest to a fixed stock-universe snapshot."""
    codes = {str(code).strip() for code in stock_codes if str(code).strip()}
    trades = [
        trade for trade in backtest.get("trades", []) if trade.get("ts_code") in codes
    ]
    matched_trade_codes = {trade["ts_code"] for trade in trades}
    return {
        "metadata": {
            **backtest.get("metadata", {}),
            "report_type": "stock_short_state_trade_backtest_stock_pool",
            "stock_pool_filter": {
                "label": str(pool_label),
                "pool_date": str(pool_date),
                "source_file": str(source_file),
                "selection_rule": str(selection_rule),
                "source_record_count": source_record_count,
                "eligible_code_count": len(codes),
                "matched_state_stock_count": matched_state_stock_count,
                "matched_trade_stock_count": len(matched_trade_codes),
                "excluded_trade_count": len(backtest.get("trades", []))
                - len(trades),
            },
        },
        "summary": _summarise_trades(trades),
        "by_entry_state": _summarise_by_entry_state(trades),
        "trades": trades,
    }


def _candidate_trades(
    stock_rows: pd.DataFrame,
    *,
    config: ShortTradeBacktestConfig,
) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    for cycle_id, cycle_rows in stock_rows[stock_rows["cycle_id"].gt(0)].groupby(
        "cycle_id", sort=True
    ):
        candidate_rows = cycle_rows[
            cycle_rows["state"].eq(int(config.candidate_entry_state))
        ].sort_values("trade_date")
        if candidate_rows.empty:
            continue

        entry_row = candidate_rows.iloc[0]
        entry_date = str(entry_row["trade_date"])
        entry_close = float(entry_row["close"])

        exits = cycle_rows[
            cycle_rows["state"].isin(
                [int(state) for state in config.candidate_exit_states]
            )
            & cycle_rows["trade_date"].ge(entry_date)
        ].sort_values("trade_date")
        invalidations = cycle_rows[
            cycle_rows["state"].eq(int(config.invalidation_state))
            & cycle_rows["trade_date"].ge(entry_date)
        ].sort_values("trade_date")
        trades.append(
            _build_trade(
                stock_rows,
                cycle_rows=cycle_rows,
                cycle_id=int(cycle_id),
                peak_number=0,
                entry_state=config.candidate_entry_state.name,
                entry_source="OBSERVED_HIGH_CANDIDATE_PULLBACK",
                entry_date=entry_date,
                entry_close=entry_close,
                exit_state=config.candidate_exit_label,
                exits=exits,
                invalidations=invalidations,
            )
        )
    return trades


def _peak_trades(
    stock_rows: pd.DataFrame,
    *,
    config: ShortTradeBacktestConfig,
) -> list[dict[str, Any]]:
    peak_rows = stock_rows[
        stock_rows["state"].isin(
            [int(state) for state in config.peak_entry_states]
        )
    ]
    trades: list[dict[str, Any]] = []
    for (cycle_id, peak_number), rows in peak_rows.groupby(
        ["cycle_id", "peak_number"], sort=True
    ):
        if int(cycle_id) <= 0 or int(peak_number) <= 0:
            continue
        signal = rows.sort_values("trade_date").iloc[0]
        entry_date = _valid_date(signal.get("state_start_date")) or str(
            signal["trade_date"]
        )
        entry_close = _positive_number(signal.get("state_start_close"))
        if entry_close is None:
            entry_close = float(signal["close"])
        cycle_rows = stock_rows[stock_rows["cycle_id"].eq(int(cycle_id))]
        exits = cycle_rows[
            cycle_rows["state"].eq(int(config.peak_exit_state))
            & cycle_rows["peak_number"].eq(int(peak_number))
            & cycle_rows["trade_date"].ge(entry_date)
        ].sort_values("trade_date")
        invalidations = cycle_rows[
            cycle_rows["state"].eq(int(config.invalidation_state))
            & cycle_rows["trade_date"].ge(entry_date)
        ].sort_values("trade_date")
        trades.append(
            _build_trade(
                stock_rows,
                cycle_rows=cycle_rows,
                cycle_id=int(cycle_id),
                peak_number=int(peak_number),
                entry_state=f"PEAK_{int(peak_number)}",
                entry_source="OBSERVED_PEAK_START",
                entry_date=entry_date,
                entry_close=entry_close,
                exit_state=config.peak_exit_state.name,
                exits=exits,
                invalidations=invalidations,
            )
        )
    return trades


def _build_trade(
    stock_rows: pd.DataFrame,
    *,
    cycle_rows: pd.DataFrame,
    cycle_id: int,
    peak_number: int,
    entry_state: str,
    entry_source: str,
    entry_date: str,
    entry_close: float,
    exit_state: str,
    exits: pd.DataFrame,
    invalidations: pd.DataFrame,
) -> dict[str, Any]:
    exit_row = exits.iloc[0] if not exits.empty else None
    invalid_row = invalidations.iloc[0] if not invalidations.empty else None
    if exit_row is not None and (
        invalid_row is None
        or str(exit_row["trade_date"]) <= str(invalid_row["trade_date"])
    ):
        status = "COMPLETED"
        status_reason = "EXIT_STATE_REACHED"
        evaluation_row = exit_row
    elif invalid_row is not None:
        status = "INVALIDATED"
        status_reason = "CONFIRMED_HIGH_BROKEN"
        evaluation_row = invalid_row
    elif int(stock_rows["cycle_id"].max()) > cycle_id:
        status = "INVALIDATED"
        status_reason = "CYCLE_CHANGED_WITHOUT_EXIT"
        evaluation_row = cycle_rows.sort_values("trade_date").iloc[-1]
    else:
        status = "OPEN"
        status_reason = "AS_OF_DATE"
        evaluation_row = stock_rows.sort_values("trade_date").iloc[-1]

    evaluation_date = str(evaluation_row["trade_date"])
    evaluation_close = float(evaluation_row["close"])
    path = stock_rows[
        stock_rows["trade_date"].between(entry_date, evaluation_date)
    ].sort_values("trade_date")
    path_closes = path["close"].to_numpy(dtype=float)
    if path.empty or str(path.iloc[0]["trade_date"]) != entry_date:
        path_closes = np.insert(path_closes, 0, entry_close)
    elif not np.isclose(path_closes[0], entry_close):
        path_closes[0] = entry_close
    if len(path_closes) == 0:
        path_closes = np.array([entry_close, evaluation_close], dtype=float)

    mark_return = 1.0 - evaluation_close / entry_close
    equity = 2.0 - path_closes / entry_close
    running_max = np.maximum.accumulate(equity)
    drawdowns = 1.0 - equity / running_max
    entry_row = _row_for_date(stock_rows, entry_date)
    total_market_caps = pd.to_numeric(path["total_mv"], errors="coerce")
    total_market_caps = total_market_caps[
        total_market_caps.notna() & total_market_caps.gt(0)
    ]
    return {
        "ts_code": str(stock_rows.iloc[0]["ts_code"]),
        "stock_name": str(stock_rows.iloc[-1].get("stock_name", "") or ""),
        "cycle_id": cycle_id,
        "peak_number": peak_number,
        "entry_state": entry_state,
        "entry_source": entry_source,
        "entry_date": entry_date,
        "entry_close": float(entry_close),
        "entry_total_mv": _optional_number(entry_row.get("total_mv")),
        "entry_circ_mv": _optional_number(entry_row.get("circ_mv")),
        "average_total_mv": (
            float(total_market_caps.mean()) if not total_market_caps.empty else None
        ),
        "market_cap_observation_days": int(len(total_market_caps)),
        "exit_state": (
            str(exit_row["state_name"])
            if status == "COMPLETED" and exit_row is not None
            else exit_state
        ),
        "exit_date": evaluation_date if status == "COMPLETED" else None,
        "exit_close": evaluation_close if status == "COMPLETED" else None,
        "status": status,
        "status_reason": status_reason,
        "evaluation_date": evaluation_date,
        "evaluation_close": evaluation_close,
        "return_pct": float(mark_return) if status == "COMPLETED" else None,
        "mark_return_pct": float(mark_return),
        "holding_trade_days": max(len(path_closes) - 1, 0),
        "mfe_pct": float(1.0 - np.min(path_closes) / entry_close),
        "mae_pct": float(np.max(path_closes) / entry_close - 1.0),
        "max_drawdown_pct": float(np.max(drawdowns)),
    }


def _summarise_trades(trades: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [trade for trade in trades if trade["status"] == "COMPLETED"]
    returns = np.array([trade["return_pct"] for trade in completed], dtype=float)
    wins = int(np.sum(returns > 1e-12))
    losses = int(np.sum(returns < -1e-12))
    breakeven = int(len(returns) - wins - losses)
    return {
        "signals": len(trades),
        "completed_trades": len(completed),
        "open_trades": sum(trade["status"] == "OPEN" for trade in trades),
        "invalidated_trades": sum(
            trade["status"] == "INVALIDATED" for trade in trades
        ),
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": _optional_stat(wins / len(completed) if completed else None),
        "average_return_pct": _array_stat(returns, np.mean),
        "median_return_pct": _array_stat(returns, np.median),
        "best_return_pct": _array_stat(returns, np.max),
        "worst_return_pct": _array_stat(returns, np.min),
        "compounded_return_pct": _compounded_return(returns),
        "sequential_max_drawdown_pct": _sequential_max_drawdown(completed),
        "average_holding_trade_days": _mean_trade_value(
            completed, "holding_trade_days"
        ),
        "average_mfe_pct": _mean_trade_value(completed, "mfe_pct"),
        "average_mae_pct": _mean_trade_value(completed, "mae_pct"),
        "average_trade_max_drawdown_pct": _mean_trade_value(
            completed, "max_drawdown_pct"
        ),
        "worst_trade_max_drawdown_pct": _max_trade_value(
            completed, "max_drawdown_pct"
        ),
    }


def _summarise_by_entry_state(
    trades: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    states = sorted(
        {trade["entry_state"] for trade in trades},
        key=_entry_state_sort_key,
    )
    for entry_state in states:
        selected = [trade for trade in trades if trade["entry_state"] == entry_state]
        summary = _summarise_trades(selected)
        summaries.append({"entry_state": entry_state, **summary})
    return summaries


def _normalise_states(states: pd.DataFrame) -> pd.DataFrame:
    required = {"ts_code", "trade_date", "close", "state", "cycle_id", "peak_number"}
    if states is None or states.empty:
        return pd.DataFrame(columns=sorted(required))
    missing = sorted(required.difference(states.columns))
    if missing:
        raise ValueError(f"states is missing required columns: {', '.join(missing)}")
    data = states.copy()
    for column, default in (
        ("stock_name", ""),
        ("state_start_date", ""),
        ("state_start_close", np.nan),
        ("total_mv", np.nan),
        ("circ_mv", np.nan),
    ):
        if column not in data.columns:
            data[column] = default
    data["ts_code"] = data["ts_code"].fillna("").astype(str).str.strip()
    data["trade_date"] = data["trade_date"].fillna("").astype(str).str.strip()
    for column in ("close", "state", "cycle_id", "peak_number"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data[
        data["ts_code"].ne("")
        & data["trade_date"].ne("")
        & data["close"].gt(0)
        & data["state"].isin({int(state) for state in StockShortState})
        & data["cycle_id"].notna()
        & data["peak_number"].notna()
    ].copy()
    data[["state", "cycle_id", "peak_number"]] = data[
        ["state", "cycle_id", "peak_number"]
    ].astype(int)
    return data.sort_values(["ts_code", "trade_date"]).drop_duplicates(
        ["ts_code", "trade_date"], keep="last"
    )


def _row_for_date(rows: pd.DataFrame, trade_date: str) -> pd.Series:
    matched = rows[rows["trade_date"].eq(trade_date)]
    return matched.iloc[0] if not matched.empty else rows.iloc[0]


def _valid_date(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    return text if text and text.lower() != "nan" else ""


def _positive_number(value: Any) -> float | None:
    number = _optional_number(value)
    return number if number is not None and number > 0 else None


def _optional_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _optional_stat(value: Any) -> float | None:
    return _optional_number(value)


def _array_stat(values: np.ndarray, function: Any) -> float | None:
    return float(function(values)) if len(values) else None


def _mean_trade_value(trades: list[dict[str, Any]], key: str) -> float | None:
    if not trades:
        return None
    return float(np.mean([float(trade[key]) for trade in trades]))


def _max_trade_value(trades: list[dict[str, Any]], key: str) -> float | None:
    if not trades:
        return None
    return float(np.max([float(trade[key]) for trade in trades]))


def _compounded_return(returns: np.ndarray) -> float | None:
    if not len(returns):
        return None
    log_return = float(np.sum(np.log1p(returns)))
    if log_return > np.log(np.finfo(float).max):
        return None
    return float(np.expm1(log_return))


def _sequential_max_drawdown(trades: list[dict[str, Any]]) -> float | None:
    if not trades:
        return None
    ordered = sorted(
        trades,
        key=lambda trade: (
            trade["exit_date"],
            trade["entry_date"],
            trade["ts_code"],
        ),
    )
    returns = np.array([trade["return_pct"] for trade in ordered], dtype=float)
    log_equity = np.concatenate(([0.0], np.cumsum(np.log1p(returns))))
    running_max = np.maximum.accumulate(log_equity)
    return float(np.max(1.0 - np.exp(log_equity - running_max)))


def _entry_state_sort_key(value: str) -> tuple[int, int]:
    if value == "HIGH_CANDIDATE_PULLBACK":
        return (0, 0)
    try:
        return (1, int(value.removeprefix("PEAK_")))
    except ValueError:
        return (2, 0)
