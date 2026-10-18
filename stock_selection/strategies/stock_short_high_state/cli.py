"""CLI for stock short-side high-region state classification."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys
from time import time


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_selection.data.market_cap import load_cached_market_caps, merge_market_caps

from stock_selection.screening_schemes.plan_two.lending import (
    DEFAULT_LENDING_ROOT,
    read_lending_file,
    resolve_lending_file,
)
from stock_selection.strategies.stock_short_high_state.backtest import (
    build_short_trade_backtest,
    filter_short_trade_backtest_by_stock_pool,
    split_short_trade_backtest_by_average_market_cap,
)
from stock_selection.strategies.stock_short_high_state.report import (
    DEFAULT_OUTPUT_ROOT,
    build_stock_short_state_report,
    save_short_trade_backtest_report,
    save_stock_short_state_report,
)
from stock_selection.strategies.stock_short_high_state.screen import (
    DEFAULT_CANDIDATE_PULLBACK_PCT,
    DEFAULT_CONFIRM_DRAWDOWN_PCT,
    DEFAULT_HIGH_HEAT_DAYS,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_REBOUND_PCT,
    DEFAULT_TROUGH_CONFIRM_DAYS,
    calculate_stock_short_states,
    read_cached_stock_daily,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Classify close-only high regions and rebound-peak states."
    )
    parser.add_argument(
        "--start-date", default="20240901",
        help="First cache date to load, inclusive (YYYYMMDD or YYYY-MM-DD); "
        "recalculate states from this date, with no earlier history (default: 20240901).",
    )
    parser.add_argument("--date", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--cache-root", default=None)
    parser.add_argument("--stock-code", action="append", default=None)
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
        rebound_pct=args.rebound_pct,
        high_heat_days=args.high_heat_days,
        trough_confirm_days=args.trough_confirm_days,
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
        rebound_pct=args.rebound_pct,
        high_heat_days=args.high_heat_days,
        trough_confirm_days=args.trough_confirm_days,
    )
    if args.write_report:
        backtest = build_short_trade_backtest(states, as_of_date=args.date)
        csv_path  = save_stock_short_state_report(
            report,
            states,
            records_root=args.records_root,
        )
        backtest_path = save_short_trade_backtest_report(
            backtest,
            records_root=args.records_root,
        )
        market_cap_backtests = split_short_trade_backtest_by_average_market_cap(
            backtest
        )
        below_300yi_path = save_short_trade_backtest_report(
            market_cap_backtests["below"],
            records_root=args.records_root,
            filename="short_trade_backtest_avg_mv_below_300yi.md",
        )
        at_least_300yi_path = save_short_trade_backtest_report(
            market_cap_backtests["at_least"],
            records_root=args.records_root,
            filename="short_trade_backtest_avg_mv_at_least_300yi.md",
        )
        lending_path = _resolve_optional_lending_file(args)
        lending_backtest = None
        lending_backtest_path = None
        if lending_path is not None:
            lending_data = read_lending_file(lending_path)
            lending_dates = set(lending_data["lending_date"].dropna().astype(str))
            if lending_dates != {str(args.date)}:
                raise ValueError(
                    "lending data date must match the strategy report date: "
                    f"expected {args.date}, found {', '.join(sorted(lending_dates))}"
                )
            eligible_lending = lending_data[
                lending_data["available_quantity"].gt(0)
                & lending_data["lending_status"].eq("正常")
                & lending_data["ts_code"].ne("")
            ].copy()
            eligible_codes = set(eligible_lending["ts_code"])
            lending_backtest = filter_short_trade_backtest_by_stock_pool(
                backtest,
                eligible_codes,
                pool_label=f"{args.date}可融券股票池",
                pool_date=args.date,
                source_file=str(lending_path),
                selection_rule="融券状态正常且可用数量大于0",
                source_record_count=len(lending_data),
                matched_state_stock_count=len(
                    eligible_codes.intersection(set(states["ts_code"]))
                ),
            )
            lending_backtest_path = save_short_trade_backtest_report(
                lending_backtest,
                records_root=args.records_root,
                filename=f"short_trade_backtest_lendable_{args.date}.md",
            )
        output = {
            "csv": str(csv_path),
            # "json": str(json_path),
            # "markdown": str(markdown_path),
            "short_trade_backtest": str(backtest_path),
            "short_trade_backtest_avg_mv_below_300yi": str(below_300yi_path),
            "short_trade_backtest_avg_mv_at_least_300yi": str(
                at_least_300yi_path
            ),
            "summary": report["summary"],
            "backtest_summary": backtest["summary"],
            "backtest_summary_avg_mv_below_300yi": market_cap_backtests["below"][
                "summary"
            ],
            "backtest_summary_avg_mv_at_least_300yi": market_cap_backtests[
                "at_least"
            ]["summary"],
            "latest_states": report["latest_states"],
        }
        if lending_backtest is not None and lending_backtest_path is not None:
            output["short_trade_backtest_lendable"] = str(lending_backtest_path)
            output["backtest_summary_lendable"] = lending_backtest["summary"]
    # else:
        # output = report
    # print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    end_time = time()
    print(f"读数据花费 {data_cost_time - begin_time}秒，计算状态花费{state_cal_time - data_cost_time}秒,状态merge花了{state_merge_time-state_cal_time}秒， 回撤花了{end_time - state_merge_time}秒")
    return 0


def _resolve_optional_lending_file(args: argparse.Namespace) -> Path | None:
    try:
        return resolve_lending_file(
            as_of_date=args.date,
            lending_file=args.lending_file,
            lending_root=args.lending_root,
        )
    except FileNotFoundError:
        if args.lending_file is not None:
            raise
        return None


if __name__ == "__main__":
    raise SystemExit(main())
