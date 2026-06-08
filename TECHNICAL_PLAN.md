# Minimal Technical Plan

This project is intentionally small at this stage. The goal is to keep a clean
framework that supports one strategy first, then add new indicators and
strategies only after the basic flow is stable.

## Current Scope

1. Validate long-form daily OHLCV data.
2. Detect whether a stock has a simple price cycle.
3. Classify the current cycle position as `bottom`, `middle`, or `top`.
4. Run historical backtests by calling the strategy as of each historical date.
5. Build overall and per-symbol backtest reports.
6. Visualize daily K-line charts for a configured stock list and date range,
   with optional buy/sell markers.

## Data Format

The input price frame uses one row per stock per trading day:

- `date`
- `symbol`
- `open`
- `high`
- `low`
- `close`
- `volume`

## Modules

- `stock_selection.core`: data contracts and validation.
- `stock_selection.methods.periodicity`: cycle detection and position
  classification.
- `stock_selection.strategies.cycle_position`: the only current strategy. It
  supports both screening with `select()` and trading signals with
  `generate_signals()`.
- `stock_selection.backtesting`: signal-driven historical backtest.
- `stock_selection.visualization`: Plotly K-line charts and trade markers.

## Backtest Flow

Backtest inputs:

- `price_df`: daily OHLCV data.
- `symbols`: stock pool to test.
- `strategy`: strategy object that emits `buy`, `sell`, or `hold`.
- `start_date`: first date to evaluate trades.
- `end_date`: optional last date; defaults to the last date in the data.

Current strategy rules:

- Buy when a stock is periodic and at `bottom`.
- Sell when a held stock reaches `top`.
- Sell when a held stock is no longer periodic.
- Optional engine exits: `stop_loss`, `take_profit`, `max_holding_days`.

Backtest outputs:

- `equity_curve`: daily account value, cash, market value, and position count.
- `trades`: every buy/sell record with price, reason, holding days, and return.
- `positions`: daily open positions and unrealized return.
- `reports["overall"]`: one-row summary of strategy and stock-pool results.
- `reports["per_symbol"]`: per-stock price-path, drawdown, trade, and holding
  period summary.

## Extension Rule

Add new indicators under `methods` and new strategies under `strategies` only
when the existing cycle-position flow is understood and tested.
