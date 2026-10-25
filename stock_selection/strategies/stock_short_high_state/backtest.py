"""Backtest short trades formed from strategy-five state transitions."""

from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
import pandas as pd

from stock_selection.strategies.stock_short_high_state.screen import (
    DEFAULT_CANDIDATE_PULLBACK_PCT,
    DEFAULT_TROUGH_CONFIRM_DAYS,
    StockShortState,
)


@dataclass(frozen=True)
class ShortTradeBacktestConfig:
    candidate_entry_state: StockShortState = (
        StockShortState.HIGH_CANDIDATE_PULLBACK
    )
    trough_exit_states: frozenset[StockShortState] = frozenset(
        {
            StockShortState.TROUGH_REGION,
        }
    )
    peak_entry_states: frozenset[StockShortState] = frozenset()
    invalidation_state: StockShortState = StockShortState.INVALID_HIGH_NEW_HIGH
    max_drawdown_pct: float = 0.20
    profit_rebound_min_return_pct: float = 0.05
    profit_rebound_up_days: int = 3

    def __post_init__(self) -> None:
        if not np.isfinite(self.max_drawdown_pct) or not 0 < self.max_drawdown_pct < 1:
            raise ValueError("max_drawdown_pct must be a finite number between 0 and 1")
        if (
            not np.isfinite(self.profit_rebound_min_return_pct)
            or not 0 < self.profit_rebound_min_return_pct < 1
        ):
            raise ValueError("profit_rebound_min_return_pct must be finite and between 0 and 1")
        if (
            isinstance(self.profit_rebound_up_days, bool)
            or not isinstance(self.profit_rebound_up_days, (int, np.integer))
            or self.profit_rebound_up_days < 1
        ):
            raise ValueError("profit_rebound_up_days must be a positive integer")


BACKTEST_CONFIG = ShortTradeBacktestConfig()
ENTRY_STATES = {
    int(BACKTEST_CONFIG.candidate_entry_state),
    *(int(state) for state in BACKTEST_CONFIG.peak_entry_states),
}
PEAK_ENTRY_STATES = {int(state) for state in BACKTEST_CONFIG.peak_entry_states}
# State files retain events for every peak; config selects which can open trades.
VALID_ENTRY_SIGNAL_STATES = {
    0,
    int(StockShortState.HIGH_CANDIDATE_PULLBACK),
    int(StockShortState.PEAK_1),
    int(StockShortState.PEAK_2),
    int(StockShortState.PEAK_3),
    int(StockShortState.PEAK_N),
}
INVALID_STATE = int(BACKTEST_CONFIG.invalidation_state)
DEFAULT_MARKET_CAP_THRESHOLD = 3_000_000.0
MAX_DRAWDOWN_STOP = "MAX_DRAWDOWN_STOP"
PROFIT_REBOUND_EXIT = "PROFIT_REBOUND_EXIT"
TROUGH_CONFIRM_EXIT = "TROUGH_CONFIRM_EXIT"
HIGH_INVALIDATION_EXIT = "HIGH_INVALIDATION_EXIT"
CYCLE_CHANGE_EXIT = "CYCLE_CHANGE_EXIT"


def build_short_trade_backtest(
    states: pd.DataFrame,
    *,
    as_of_date: str,
    config: ShortTradeBacktestConfig = BACKTEST_CONFIG,
) -> dict[str, Any]:
    """Build state-transition short trades and aggregate performance metrics."""
    data = _normalise_states(states)
    data = data[data["trade_date"].le(_valid_date(as_of_date))]
    trades: list[dict[str, Any]] = []
    if not data.empty:
        for _, stock_rows in data.groupby("ts_code", sort=True):
            stock_rows = stock_rows.sort_values("trade_date").reset_index(drop=True)
            trades.extend(_stock_trades(stock_rows, config=config))

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
            "exit_states": [
                *(state.name for state in sorted(config.trough_exit_states, key=int)),
                PROFIT_REBOUND_EXIT,
                MAX_DRAWDOWN_STOP,
                config.invalidation_state.name,
                CYCLE_CHANGE_EXIT,
            ],
            "normal_exit_rule": {
                "state_source": "observed_state",
                "confirmation_rule": "ON_TROUGH_CONFIRM_DATE_ONLY",
                "trough_confirm_days": (
                    int(data["trough_confirm_days"].iloc[0])
                    if not data.empty else DEFAULT_TROUGH_CONFIRM_DAYS
                ),
                "trough_min_up_days": (
                    int(data["trough_min_up_days"].iloc[0])
                    if not data.empty else max(1, DEFAULT_TROUGH_CONFIRM_DAYS - 2)
                ),
                "trough_up_rule": "LATEST_OBSERVATION_WINDOW_CLOSE_GT_PREVIOUS_CLOSE",
                "trough_states": [state.name for state in sorted(config.trough_exit_states, key=int)],
            },
            "profit_rebound_exit_rule": {
                "reason": PROFIT_REBOUND_EXIT,
                "min_return_pct": float(config.profit_rebound_min_return_pct),
                "return_comparison": ">",
                "consecutive_up_days": int(config.profit_rebound_up_days),
                "up_rule": "CLOSE_GT_PREVIOUS_CLOSE_AFTER_ENTRY",
                "return_formula": "1 - current_close / entry_close",
                "requires_trough_state": False,
                "execution": "CURRENT_CLOSE",
                "priority": "AFTER_STOP_AND_INVALIDATION_BEFORE_TROUGH_EXIT",
            },
            "entry_rule": {
                "candidate_pullback_pct": (
                    float(data["candidate_pullback_pct"].iloc[0])
                    if not data.empty else DEFAULT_CANDIDATE_PULLBACK_PCT
                ),
                "candidate_formula": "1 - close / running_high_close >= candidate_pullback_pct",
                "signal_source": "entry_signal",
                "one_candidate_signal_per_cycle": False,
                "candidate_signal_rule": "EACH_CROSSING_INTO_PULLBACK",
                "candidate_rearm_rule": "DRAWDOWN_BELOW_CANDIDATE_PULLBACK_PCT",
            },
            "position_rule": "ONE_POSITION_PER_STOCK_NO_SAME_DAY_REENTRY",
            "invalidation_execution": "CURRENT_CLOSE_FORCE_EXIT_INCLUDED_IN_RETURNS",
            "invalidation_state": config.invalidation_state.name,
            "return_formula": "1 - exit_close / entry_close",
            "equity_formula": "2 - close / entry_close",
            "max_drawdown_stop_pct": float(config.max_drawdown_pct),
            "stop_execution": "DAILY_CLOSE_TRIGGER_THRESHOLD_PRICE_FILL",
        },
        "summary": _summarise_trades(trades),
        "by_entry_state": _summarise_by_entry_state(trades),
        "trades": trades,
    }


def sum_evaluated_returns_by_stock(
    trades: list[dict[str, Any]],
) -> dict[str, float]:
    returns_by_stock: dict[str, float] = {}
    for trade in trades:
        value = (
            trade.get("return_pct")
            if trade.get("status") == "COMPLETED"
            else trade.get("mark_return_pct")
        )
        if value is None or not np.isfinite(float(value)):
            continue
        code = str(trade["ts_code"])
        returns_by_stock[code] = returns_by_stock.get(code, 0.0) + float(value)
    return returns_by_stock


def sample_stock_trade_codes(
    trades: list[dict[str, Any]],
    *,
    count_per_group: int = 30,
) -> list[str]:
    """Sample worst losses and both ends of gains, with gains descending."""
    returns_by_stock = sum_evaluated_returns_by_stock(trades)
    ranked = sorted(returns_by_stock.items(), key=lambda item: (item[1], item[0]))
    negative = [code for code, value in ranked if value < 0][:count_per_group]
    positive_ranked = [(code, value) for code, value in ranked if value > 0]
    smallest_positive = [code for code, _ in positive_ranked[:count_per_group]]
    largest_positive = [
        code for code, _ in sorted(
            positive_ranked, key=lambda item: (-item[1], item[0]),
        )[:count_per_group]
    ]
    positive = sorted(
        set(smallest_positive + largest_positive),
        key=lambda code: (-returns_by_stock[code], code),
    )
    return negative + positive


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


def _stock_trades(
    stock_rows: pd.DataFrame,
    *,
    config: ShortTradeBacktestConfig,
) -> list[dict[str, Any]]:
    """Process observable signals and one open position per stock chronologically."""
    trades: list[dict[str, Any]] = []
    rows = stock_rows.to_dict("records")
    seen_peak_signals: set[tuple[int, int]] = set()
    entry_states = {int(config.candidate_entry_state), *(int(state) for state in config.peak_entry_states)}
    trough_states = {int(state) for state in config.trough_exit_states}
    entry_position: int | None = None
    running_equity_peak = 1.0
    consecutive_up_days = 0
    skipped_entries: list[dict[str, Any]] = []

    for position, row in enumerate(rows):
        cycle_id = int(row["cycle_id"])
        observed_state = int(row["observed_state"])
        signal = int(row["entry_signal"])
        has_signal = signal in entry_states and cycle_id > 0
        if has_signal and signal != int(config.candidate_entry_state):
            signal_key = (cycle_id, int(row["peak_number"]))
            has_signal = signal_key not in seen_peak_signals
            if has_signal:
                seen_peak_signals.add(signal_key)

        if entry_position is None:
            if has_signal:
                entry_position = position
                running_equity_peak = 1.0
                consecutive_up_days = 0
                skipped_entries = []
            continue

        entry_row = rows[entry_position]
        entry_cycle = int(entry_row["cycle_id"])
        current_close = float(row["close"])
        current_return = 1.0 - current_close / float(entry_row["close"])
        # Count only close-to-close changes after this trade's entry.
        consecutive_up_days = (
            consecutive_up_days + 1
            if current_close > float(rows[position - 1]["close"]) else 0
        )
        equity = 1.0 + current_return
        running_equity_peak = max(running_equity_peak, equity)
        drawdown = 1.0 - equity / running_equity_peak
        reason = ""
        exit_state = str(row["observed_state_name"])
        execution_close = float(row["close"])
        stop_trigger_close = None
        if drawdown + 1e-12 >= config.max_drawdown_pct:
            reason = MAX_DRAWDOWN_STOP
            exit_state = MAX_DRAWDOWN_STOP
            stop_trigger_close = execution_close
            stop_equity = running_equity_peak * (1.0 - config.max_drawdown_pct)
            execution_close = float(entry_row["close"]) * (2.0 - stop_equity)
        elif cycle_id != entry_cycle:
            reason = CYCLE_CHANGE_EXIT
            exit_state = CYCLE_CHANGE_EXIT
        elif observed_state == int(config.invalidation_state):
            reason = HIGH_INVALIDATION_EXIT
        elif (
            current_return > config.profit_rebound_min_return_pct + 1e-12
            and consecutive_up_days >= config.profit_rebound_up_days
        ):
            reason = PROFIT_REBOUND_EXIT
            exit_state = PROFIT_REBOUND_EXIT
        elif (
            observed_state in trough_states
            and row["trough_confirm_date"] == row["trade_date"]
        ):
            # Only today's confirmed trough can exit, never backfilled lows
            # or a later return to an already confirmed region.
            reason = TROUGH_CONFIRM_EXIT

        if has_signal:
            skipped_entries.append({
                "ts_code": str(row["ts_code"]),
                "stock_name": str(row.get("stock_name") or ""),
                "trade_date": str(row["trade_date"]),
                "cycle_id": cycle_id,
                "entry_state": StockShortState(signal).name,
                "peak_number": int(row["peak_number"]),
                "entry_signal_type": str(row["entry_signal_type"]),
                "blocking_entry_date": str(entry_row["trade_date"]),
                "reason": "EXIT_DAY" if reason else "POSITION_OPEN",
            })
        if reason:
            trades.append(_build_trade(
                stock_rows, entry_position=entry_position, evaluation_position=position,
                status_reason=reason, exit_state=exit_state,
                execution_close=execution_close, stop_trigger_close=stop_trigger_close,
                skipped_entries=skipped_entries,
                consecutive_up_days=consecutive_up_days,
            ))
            entry_position = None

    if entry_position is not None:
        trades.append(_build_trade(
            stock_rows, entry_position=entry_position, evaluation_position=len(rows) - 1,
            status_reason="AS_OF_DATE", exit_state=str(rows[-1]["observed_state_name"]),
            execution_close=float(rows[-1]["close"]), stop_trigger_close=None,
            skipped_entries=skipped_entries,
            consecutive_up_days=consecutive_up_days,
        ))
    return trades


def _build_trade(
    stock_rows: pd.DataFrame,
    *,
    entry_position: int,
    evaluation_position: int,
    status_reason: str,
    exit_state: str,
    execution_close: float,
    stop_trigger_close: float | None,
    skipped_entries: list[dict[str, Any]],
    consecutive_up_days: int,
) -> dict[str, Any]:
    """Measure the executed holding path; stops replace only the endpoint price."""
    entry_row = stock_rows.iloc[entry_position]
    evaluation_row = stock_rows.iloc[evaluation_position]
    entry_date = str(entry_row["trade_date"])
    entry_close = float(entry_row["close"])
    evaluation_date = str(evaluation_row["trade_date"])
    trough_confirm_date = _valid_date(evaluation_row["trough_confirm_date"])
    known_trough_confirm_date = (
        trough_confirm_date
        if trough_confirm_date and trough_confirm_date <= evaluation_date else None
    )
    status = "OPEN" if status_reason == "AS_OF_DATE" else "COMPLETED"
    path = stock_rows.iloc[entry_position:evaluation_position + 1]
    path_closes = path["close"].to_numpy(dtype=float, copy=True)
    path_closes[-1] = execution_close
    equity = 2.0 - path_closes / entry_close
    running_max = np.maximum.accumulate(equity)
    drawdowns = 1.0 - equity / running_max
    mark_return = 1.0 - execution_close / entry_close
    signal = int(entry_row["entry_signal"])
    peak_number = 0 if signal == int(StockShortState.HIGH_CANDIDATE_PULLBACK) else int(entry_row["peak_number"])
    total_market_caps = pd.to_numeric(path["total_mv"], errors="coerce")
    total_market_caps = total_market_caps[
        total_market_caps.notna() & total_market_caps.gt(0)
    ]
    return {
        "ts_code": str(stock_rows.iloc[0]["ts_code"]),
        "stock_name": str(stock_rows.iloc[-1].get("stock_name", "") or ""),
        "cycle_id": int(entry_row["cycle_id"]),
        "peak_number": peak_number,
        "entry_state": StockShortState(signal).name if peak_number == 0 else f"PEAK_{peak_number}",
        "entry_source": str(entry_row["entry_signal_type"]),
        "entry_pattern_start_date": _valid_date(entry_row["entry_pattern_start_date"]),
        "entry_reference_high_close": _optional_number(entry_row["entry_reference_high_close"]),
        "entry_candidate_pullback_pct": float(entry_row["candidate_pullback_pct"]),
        "entry_date": entry_date,
        "entry_close": float(entry_close),
        "entry_total_mv": _optional_number(entry_row.get("total_mv")),
        "entry_circ_mv": _optional_number(entry_row.get("circ_mv")),
        "average_total_mv": (
            float(total_market_caps.mean()) if not total_market_caps.empty else None
        ),
        "market_cap_observation_days": int(len(total_market_caps)),
        "exit_state": exit_state,
        "exit_date": evaluation_date if status == "COMPLETED" else None,
        "exit_close": execution_close if status == "COMPLETED" else None,
        "status": status,
        "status_reason": status_reason,
        "evaluation_date": evaluation_date,
        "evaluation_close": execution_close,
        "stop_trigger_close": stop_trigger_close,
        "evaluation_observed_state": str(evaluation_row["observed_state_name"]),
        "evaluation_trough_confirm_date": known_trough_confirm_date,
        "evaluation_trough_up_days": (
            int(evaluation_row["trough_up_days"])
            if int(evaluation_row["observed_state"]) == int(StockShortState.TROUGH_CANDIDATE)
            or known_trough_confirm_date == evaluation_date else None
        ),
        "trough_min_up_days": int(evaluation_row["trough_min_up_days"]),
        "evaluation_actual_close": float(evaluation_row["close"]),
        "evaluation_consecutive_up_days": int(consecutive_up_days),
        "skipped_entries": list(skipped_entries),
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
    positive_returns = returns[returns > 1e-12]
    negative_returns = returns[returns < -1e-12]
    wins = int(len(positive_returns))
    losses = int(len(negative_returns))
    breakeven = int(len(returns) - wins - losses)
    return {
        "signals": len(trades),
        "skipped_entry_signals": sum(len(trade.get("skipped_entries", [])) for trade in trades),
        "skipped_while_holding": sum(
            item["reason"] == "POSITION_OPEN"
            for trade in trades for item in trade.get("skipped_entries", [])
        ),
        "skipped_on_exit_day": sum(
            item["reason"] == "EXIT_DAY"
            for trade in trades for item in trade.get("skipped_entries", [])
        ),
        "completed_trades": len(completed),
        "drawdown_stop_trades": sum(
            trade.get("status_reason") == MAX_DRAWDOWN_STOP for trade in completed
        ),
        "profit_rebound_exit_trades": sum(
            trade.get("status_reason") == PROFIT_REBOUND_EXIT for trade in completed
        ),
        "invalidation_exit_trades": sum(
            trade.get("status_reason") == HIGH_INVALIDATION_EXIT for trade in completed
        ),
        "cycle_change_exit_trades": sum(
            trade.get("status_reason") == CYCLE_CHANGE_EXIT for trade in completed
        ),
        "trough_exit_trades": sum(
            trade.get("status_reason") == TROUGH_CONFIRM_EXIT for trade in completed
        ),
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
        "average_positive_return_pct": _array_stat(positive_returns, np.mean),
        "median_positive_return_pct": _array_stat(positive_returns, np.median),
        "average_negative_return_pct": _array_stat(negative_returns, np.mean),
        "median_negative_return_pct": _array_stat(negative_returns, np.median),
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
    required = {
        "ts_code", "trade_date", "close", "state", "cycle_id", "peak_number",
        "observed_state", "observed_state_name", "trough_confirm_date",
        "open", "high", "entry_signal", "entry_signal_type",
        "entry_pattern_start_date", "entry_reference_high_close", "candidate_pullback_pct",
        "trough_confirm_days", "trough_min_up_days", "trough_up_days",
    }
    if states is None or states.empty:
        return pd.DataFrame(columns=sorted(required))
    missing = sorted(required.difference(states.columns))
    if missing:
        raise ValueError(
            f"states is missing required columns: {', '.join(missing)}. "
            "请先重新运行策略五 cli.py 生成新版 states.csv，再生成报告或画图；"
            "旧文件无法还原当前跌幅规则、波谷上涨天数条件或当日实际状态。"
        )
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
    for column in (
        "trade_date", "state_start_date",
        "entry_pattern_start_date", "trough_confirm_date",
    ):
        data[column] = data[column].map(_valid_date)
    data["entry_signal_type"] = data["entry_signal_type"].fillna("").astype(str)
    for column in (
        "close", "open", "high", "state", "cycle_id", "peak_number",
        "observed_state", "entry_signal",
        "entry_reference_high_close", "candidate_pullback_pct",
        "trough_confirm_days", "trough_min_up_days", "trough_up_days",
    ):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    if (
        ~data["observed_state"].isin({int(state) for state in StockShortState})
        | ~data["entry_signal"].isin(VALID_ENTRY_SIGNAL_STATES)
        | ~np.isfinite(data["candidate_pullback_pct"])
        | data["candidate_pullback_pct"].le(0)
        | data["candidate_pullback_pct"].ge(1)
        | ~np.isfinite(data["trough_confirm_days"])
        | data["trough_confirm_days"].lt(2)
        | data["trough_confirm_days"].mod(1).ne(0)
        | data["trough_min_up_days"].ne((data["trough_confirm_days"] - 2).clip(lower=1))
        | ~np.isfinite(data["trough_up_days"])
        | data["trough_up_days"].lt(0)
        | data["trough_up_days"].gt(data["trough_confirm_days"] - 1)
        | data["trough_up_days"].mod(1).ne(0)
    ).any():
        raise ValueError("实际状态、开仓信号或窗口无效，请重新运行策略五 CLI 生成 states.csv")
    if data["candidate_pullback_pct"].nunique() > 1:
        raise ValueError("states.csv 混用了不同候选回调阈值，请重新运行策略五 CLI")
    if data["trough_confirm_days"].nunique() > 1:
        raise ValueError("states.csv 混用了不同波谷确认窗口，请重新运行策略五 CLI")
    confirmed_today = (
        data["observed_state"].eq(int(StockShortState.TROUGH_REGION))
        & data["trade_date"].eq(data["trough_confirm_date"])
    )
    if (confirmed_today & data["trough_up_days"].lt(data["trough_min_up_days"])).any():
        raise ValueError("波谷确认日未满足上涨天数条件，请重新运行策略五 CLI 生成 states.csv")
    if data["entry_signal_type"].isin({"SPIKE_THEN_GREEN", "THREE_GREEN_DAYS"}).any():
        raise ValueError("states.csv 仍是绿K形态规则，请重新运行策略五 CLI 生成跌幅规则数据")
    data["observed_state"] = data["observed_state"].astype(int)
    data["observed_state_name"] = data["observed_state"].map(
        {int(state): state.name for state in StockShortState}
    )
    data = data[
        data["ts_code"].ne("")
        & data["trade_date"].ne("")
        & data["close"].gt(0)
        & data["state"].isin({int(state) for state in StockShortState})
        & data["cycle_id"].notna()
        & data["peak_number"].notna()
    ].copy()
    data[["state", "cycle_id", "peak_number", "entry_signal"]] = data[
        ["state", "cycle_id", "peak_number", "entry_signal"]
    ].astype(int)
    return data.sort_values(["ts_code", "trade_date"]).drop_duplicates(
        ["ts_code", "trade_date"], keep="last"
    )


def _valid_date(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip().replace("-", "")
    return text[:8] if len(text) >= 8 and text[:8].isdigit() else ""


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
