"""Strategy validation helpers built on top of the signal backtest engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from typing import Any

import pandas as pd

from stock_selection.backtesting.engine import SignalBacktestEngine
from stock_selection.core.schema import DATE_COL, SYMBOL_COL
from stock_selection.core.types import BacktestResult
from stock_selection.core.validation import normalize_price_frame


@dataclass
class ParameterRun:
    """One backtest run for one parameter combination."""

    params: dict[str, Any]
    result: BacktestResult


@dataclass
class TrainValidationTestResult:
    """Result of train/validation/test parameter validation."""

    best_params: dict[str, Any]
    train_results: pd.DataFrame
    validation_result: BacktestResult
    test_result: BacktestResult
    summary: pd.DataFrame
    runs: dict[str, BacktestResult] = field(default_factory=dict)


@dataclass
class WalkForwardResult:
    """Result of rolling sample-out validation."""

    summary: pd.DataFrame
    train_results: pd.DataFrame
    runs: list[dict[str, Any]] = field(default_factory=list)


def run_parameter_grid(
    price_df: pd.DataFrame,
    symbols: list[str],
    strategy_cls,
    param_grid: dict[str, list[Any] | tuple[Any, ...]],
    start_date: str | pd.Timestamp,
    end_date: str | pd.Timestamp | None = None,
    fixed_strategy_params: dict[str, Any] | None = None,
    engine_kwargs: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, list[ParameterRun]]:
    """Backtest every combination in ``param_grid`` and summarize metrics.

    ``strategy_cls`` is instantiated once per parameter set. Parameters in
    ``fixed_strategy_params`` are applied to every run and can be overridden by
    concrete grid values.
    """

    fixed_strategy_params = fixed_strategy_params or {}
    engine_kwargs = engine_kwargs or {}
    rows: list[dict[str, Any]] = []
    runs: list[ParameterRun] = []

    for params in _parameter_combinations(param_grid):
        strategy_params = {**fixed_strategy_params, **params}
        strategy = strategy_cls(**strategy_params)
        result = SignalBacktestEngine(strategy, **engine_kwargs).run(
            price_df,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
        )
        rows.append(_summary_row(params, result))
        runs.append(ParameterRun(params=params, result=result))

    return pd.DataFrame(rows), runs


def run_train_validation_test(
    price_df: pd.DataFrame,
    symbols: list[str],
    strategy_cls,
    param_grid: dict[str, list[Any] | tuple[Any, ...]],
    train_period: tuple[str | pd.Timestamp, str | pd.Timestamp],
    validation_period: tuple[str | pd.Timestamp, str | pd.Timestamp],
    test_period: tuple[str | pd.Timestamp, str | pd.Timestamp],
    optimize_metric: str = "total_return",
    maximize: bool = True,
    fixed_strategy_params: dict[str, Any] | None = None,
    engine_kwargs: dict[str, Any] | None = None,
) -> TrainValidationTestResult:
    """Select parameters on the train period, then evaluate validation/test."""

    train_results, train_runs = run_parameter_grid(
        price_df,
        symbols,
        strategy_cls,
        param_grid,
        start_date=train_period[0],
        end_date=train_period[1],
        fixed_strategy_params=fixed_strategy_params,
        engine_kwargs=engine_kwargs,
    )
    best_params = _best_params(train_results, optimize_metric, maximize)
    validation_result = _run_single(
        price_df,
        symbols,
        strategy_cls,
        best_params,
        validation_period,
        fixed_strategy_params,
        engine_kwargs,
    )
    test_result = _run_single(
        price_df,
        symbols,
        strategy_cls,
        best_params,
        test_period,
        fixed_strategy_params,
        engine_kwargs,
    )
    train_best = next(run.result for run in train_runs if run.params == best_params)
    summary = pd.DataFrame(
        [
            _summary_row(best_params, train_best, split="train"),
            _summary_row(best_params, validation_result, split="validation"),
            _summary_row(best_params, test_result, split="test"),
        ]
    )
    return TrainValidationTestResult(
        best_params=best_params,
        train_results=train_results,
        validation_result=validation_result,
        test_result=test_result,
        summary=summary,
        runs={"train": train_best, "validation": validation_result, "test": test_result},
    )


def run_walk_forward(
    price_df: pd.DataFrame,
    symbols: list[str],
    strategy_cls,
    param_grid: dict[str, list[Any] | tuple[Any, ...]],
    train_periods: int,
    test_periods: int,
    start_date: str | pd.Timestamp | None = None,
    end_date: str | pd.Timestamp | None = None,
    optimize_metric: str = "total_return",
    maximize: bool = True,
    fixed_strategy_params: dict[str, Any] | None = None,
    engine_kwargs: dict[str, Any] | None = None,
) -> WalkForwardResult:
    """Run rolling train-then-test validation over trading-date windows."""

    dates = _trading_dates(price_df, symbols, start_date, end_date)
    rows: list[dict[str, Any]] = []
    train_rows: list[pd.DataFrame] = []
    runs: list[dict[str, Any]] = []
    fold = 1
    offset = 0

    while offset + train_periods + test_periods <= len(dates):
        train_start = dates[offset]
        train_end = dates[offset + train_periods - 1]
        test_start = dates[offset + train_periods]
        test_end = dates[offset + train_periods + test_periods - 1]

        train_results, _ = run_parameter_grid(
            price_df,
            symbols,
            strategy_cls,
            param_grid,
            start_date=train_start,
            end_date=train_end,
            fixed_strategy_params=fixed_strategy_params,
            engine_kwargs=engine_kwargs,
        )
        train_results = train_results.copy()
        train_results.insert(0, "fold", fold)
        train_rows.append(train_results)

        best_params = _best_params(train_results, optimize_metric, maximize)
        test_result = _run_single(
            price_df,
            symbols,
            strategy_cls,
            best_params,
            (test_start, test_end),
            fixed_strategy_params,
            engine_kwargs,
        )
        row = _summary_row(best_params, test_result, fold=fold, split="test")
        row.update(
            {
                "train_start": train_start,
                "train_end": train_end,
                "test_start": test_start,
                "test_end": test_end,
            }
        )
        rows.append(row)
        runs.append({"fold": fold, "best_params": best_params, "test_result": test_result})

        fold += 1
        offset += test_periods

    return WalkForwardResult(
        summary=pd.DataFrame(rows),
        train_results=pd.concat(train_rows, ignore_index=True) if train_rows else pd.DataFrame(),
        runs=runs,
    )


def _run_single(
    price_df: pd.DataFrame,
    symbols: list[str],
    strategy_cls,
    params: dict[str, Any],
    period: tuple[str | pd.Timestamp, str | pd.Timestamp],
    fixed_strategy_params: dict[str, Any] | None,
    engine_kwargs: dict[str, Any] | None,
) -> BacktestResult:
    strategy_params = {**(fixed_strategy_params or {}), **params}
    strategy = strategy_cls(**strategy_params)
    return SignalBacktestEngine(strategy, **(engine_kwargs or {})).run(
        price_df,
        symbols=symbols,
        start_date=period[0],
        end_date=period[1],
    )


def _parameter_combinations(param_grid: dict[str, list[Any] | tuple[Any, ...]]) -> list[dict[str, Any]]:
    if not param_grid:
        return [{}]
    keys = list(param_grid)
    values = [list(param_grid[key]) for key in keys]
    if any(len(value) == 0 for value in values):
        raise ValueError("param_grid values must not be empty")
    return [dict(zip(keys, combo)) for combo in product(*values)]


def _summary_row(
    params: dict[str, Any],
    result: BacktestResult,
    **extra: Any,
) -> dict[str, Any]:
    row = {**extra, **params}
    row.update(result.metrics)
    row["trade_count"] = float(len(result.trades)) if result.trades is not None else 0.0
    row["start_date"] = result.metadata.get("start_date")
    row["end_date"] = result.metadata.get("end_date")
    return row


def _best_params(results: pd.DataFrame, optimize_metric: str, maximize: bool) -> dict[str, Any]:
    if results.empty:
        raise ValueError("no parameter runs were produced")
    if optimize_metric not in results.columns:
        raise ValueError(f"optimize_metric {optimize_metric!r} is not available")
    metric = results[optimize_metric].astype(float)
    idx = int(metric.idxmax() if maximize else metric.idxmin())
    metric_columns = {
        "total_return",
        "annual_return",
        "annual_volatility",
        "max_drawdown",
        "sharpe",
        "calmar",
        "trade_count",
        "closed_trade_count",
        "win_rate",
        "avg_trade_return",
        "best_trade_return",
        "worst_trade_return",
        "start_date",
        "end_date",
        "train_start",
        "train_end",
        "test_start",
        "test_end",
        "fold",
        "split",
    }
    return {key: results.loc[idx, key] for key in results.columns if key not in metric_columns}


def _trading_dates(
    price_df: pd.DataFrame,
    symbols: list[str],
    start_date: str | pd.Timestamp | None,
    end_date: str | pd.Timestamp | None,
) -> list[pd.Timestamp]:
    data = normalize_price_frame(price_df)
    symbols = [str(symbol) for symbol in symbols]
    data = data[data[SYMBOL_COL].isin(symbols)]
    if start_date is not None:
        data = data[data[DATE_COL] >= pd.Timestamp(start_date)]
    if end_date is not None:
        data = data[data[DATE_COL] <= pd.Timestamp(end_date)]
    return data[DATE_COL].dropna().drop_duplicates().sort_values().tolist()
