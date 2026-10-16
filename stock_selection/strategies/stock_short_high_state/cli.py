"""CLI for stock short-side high-region state classification."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys
from time import time
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_selection.data.market_cap import load_cached_market_caps, merge_market_caps

from stock_selection.screening_schemes.plan_two.lending import (
    DEFAULT_LENDING_ROOT,
)
from stock_selection.strategies.stock_short_high_state.backtest import (
    build_short_trade_backtest,
    build_short_trade_backtest_sections,
)
from stock_selection.strategies.stock_short_high_state.report import (
    DEFAULT_OUTPUT_ROOT,
    build_stock_short_state_report,
    save_short_trade_backtest_sections,
    save_stock_short_state_report,
)
from stock_selection.strategies.stock_short_high_state.screen import (
    DEFAULT_CANDIDATE_PULLBACK_PCT,
    DEFAULT_CONFIRM_DRAWDOWN_PCT,
    DEFAULT_CYCLE_END_DRAWDOWN_PCT,
    DEFAULT_HIGH_HEAT_DAYS,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_REBOUND_PCT,
    DEFAULT_REVERSAL_CONFIRM_DAYS,
    DEFAULT_TROUGH_CONFIRM_DAYS,
    calculate_stock_short_states,
    read_cached_stock_daily,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Classify close-only high regions and rebound-peak states."
    )
    parser.add_argument("--date", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--cache-root", default=None)
    parser.add_argument(
        "--stock-code", action="append", default=None,
        help="Limit to this stock (repeatable); omit to analyse all cached stocks.",
    )
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument(
        "--candidate-pullback-pct",
        type=float,
        default=DEFAULT_CANDIDATE_PULLBACK_PCT,
    )
    parser.add_argument(
        "--confirm-drawdown-pct",
        type=float,
        default=DEFAULT_CONFIRM_DRAWDOWN_PCT,
    )
    parser.add_argument("--rebound-pct", type=float, default=DEFAULT_REBOUND_PCT)
    parser.add_argument(
        "--cycle-end-drawdown-pct", type=float, default=DEFAULT_CYCLE_END_DRAWDOWN_PCT
    )
    parser.add_argument(
        "--reversal-confirm-days", type=int, default=DEFAULT_REVERSAL_CONFIRM_DAYS,
        help="Reversal confirmation bars, including the low day (default: 10).",
    )
    parser.add_argument(
        "--high-heat-days", type=int, default=DEFAULT_HIGH_HEAT_DAYS
    )
    parser.add_argument(
        "--trough-confirm-days",
        "--trough-heat-days",
        dest="trough_confirm_days",
        type=int,
        default=DEFAULT_TROUGH_CONFIRM_DAYS,
    )
    parser.add_argument("--include-st", action="store_true")
    parser.add_argument("--write-report", action="store_true",default=True)
    parser.add_argument("--records-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--lending-file", default=None)
    parser.add_argument("--lending-root", default=str(DEFAULT_LENDING_ROOT))
    return parser.parse_args()


def main() -> int:
    begin_time = time()
    args = parse_args()
    daily, basic = read_cached_stock_daily(
        cache_root=args.cache_root,
        as_of_date=args.date,
    )
    data_cost_time = time()
    states = calculate_stock_short_states(
        daily,
        as_of_date=args.date,
        lookback_days=args.lookback_days,
        candidate_pullback_pct=args.candidate_pullback_pct,
        confirm_drawdown_pct=args.confirm_drawdown_pct,
        cycle_end_drawdown_pct=args.cycle_end_drawdown_pct,
        rebound_pct=args.rebound_pct,
        high_heat_days=args.high_heat_days,
        trough_confirm_days=args.trough_confirm_days,
        reversal_confirm_days=args.reversal_confirm_days,
        stock_codes=args.stock_code,
        stock_basic=basic,
        include_st=args.include_st,
    )
    state_cal_time = time()
    if not states.empty:
        market_caps = load_cached_market_caps(
            start_date=str(states["trade_date"].min()),
            end_date=str(states["trade_date"].max()),
            stock_codes=states["ts_code"].unique(),
            cache_root=args.cache_root,
        )
        states = merge_market_caps(states, market_caps)
    state_merge_time = time()
    report = build_stock_short_state_report(
        states,
        as_of_date=args.date,
        lookback_days=args.lookback_days,
        candidate_pullback_pct=args.candidate_pullback_pct,
        confirm_drawdown_pct=args.confirm_drawdown_pct,
        cycle_end_drawdown_pct=args.cycle_end_drawdown_pct,
        rebound_pct=args.rebound_pct,
        high_heat_days=args.high_heat_days,
        trough_confirm_days=args.trough_confirm_days,
        reversal_confirm_days=args.reversal_confirm_days,
    )
    if args.write_report:
        backtest = build_short_trade_backtest(states, as_of_date=args.date)
        csv_path  = save_stock_short_state_report(
            report,
            states,
            records_root=args.records_root,
        )
        sections = _build_backtest_sections(states, backtest, args=args)
        output = save_short_trade_backtest_sections(
            sections, records_root=args.records_root,
            missing_lending_note=(
                "未找到报告日或更早的融券名单，本次未生成可融券部分。"
                if "lendable" not in sections else None
            ),
        )
        output["csv"] = str(csv_path)
        output["summaries"] = {key: value["summary"] for key, value in sections.items()}
        print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    end_time = time()
    print(f"读数据花费 {data_cost_time - begin_time}秒，计算状态花费{state_cal_time - data_cost_time}秒,状态merge花了{state_merge_time-state_cal_time}秒， 回测报告花了{end_time - state_merge_time}秒")
    return 0


def _build_backtest_sections(
    states: pd.DataFrame, backtest: dict[str, Any], *, args: argparse.Namespace,
) -> dict[str, dict[str, Any]]:
    """Build the four report scopes without splitting reports by return."""
    return build_short_trade_backtest_sections(
        states, backtest, as_of_date=args.date,
        lending_file=args.lending_file, lending_root=args.lending_root,
    )


if __name__ == "__main__":
    raise SystemExit(main())
