"""CLI for stock short-side high-region state classification."""

from __future__ import annotations

import argparse
from datetime import date, datetime
import json
from pathlib import Path
import sys
from time import time

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_selection.data.market_cap import load_cached_market_caps, merge_market_caps
from stock_selection.strategies.stock_short_high_state.industry_state import (
    INDUSTRY_COLUMNS, TREND_COLUMNS, IndustryConfig, attach_industry_states, calculate_industry_states, read_industry_inputs,
)

from stock_selection.strategies.stock_short_high_state.backtest import (
    build_short_trade_backtest,
    filter_short_trade_backtest_by_stock_pool,
    sample_stock_trade_codes,
    split_short_trade_backtest_by_average_market_cap,
)
from stock_selection.strategies.stock_short_high_state.report import (
    DEFAULT_OUTPUT_ROOT,
    build_stock_short_state_report,
    create_stock_short_run_directory,
    save_short_trade_backtest_report,
    save_stock_short_state_report,
    save_shortable_stock_list,
)
from stock_selection.strategies.stock_short_high_state.screen import (
    DEFAULT_CANDIDATE_PULLBACK_PCT,
    DEFAULT_COST_PEAK_WINDOW_DAYS,
    DEFAULT_CONFIRM_DRAWDOWN_PCT,
    DEFAULT_HIGH_HEAT_DAYS,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_MIN_BREAKOUT_GAIN_PCT,
    DEFAULT_REBOUND_PCT,
    DEFAULT_TROUGH_MA_DAYS,
    calculate_stock_short_states,
    read_cached_stock_daily,
)
from stock_selection.strategies.stock_short_high_state.visualization import (
    RUN_CODES_FILENAME,
)
from stock_selection.strategies.stock_short_high_state.stock_pool import (
    build_shortable_stock_list,
    read_lending_stock_pool,
    resolve_stock_pool_file,
)


DEFAULT_LENDING_FILE = None
DEFAULT_LENDING_ROOT = ROOT / "data_cache" / "htsc_margin_availability"


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise argparse.ArgumentTypeError("expected True or False")
    return normalized == "true"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Classify high regions, percentage pullbacks, and rebound peaks."
    )
    parser.add_argument(
        "--start-date", default="20240901",
        help="First cache date to load, inclusive (YYYYMMDD or YYYY-MM-DD); "
        "recalculate states from this date, with no earlier history (default: 20240901).",
    )
    parser.add_argument("--date", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--cache-root", default=None)
    parser.add_argument("--stock-code", action="append", default=None)
    parser.add_argument(
        "--all-stocks", type=_parse_bool, default=False, metavar="True|False",
        help="True: calculate all stocks; False: calculate only stocks in the "
        "securities-lending list, including zero or unknown quantities (default). "
        "--stock-code further restricts either selection.",
    )
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument(
        "--cost-peak-window-days", type=int, default=DEFAULT_COST_PEAK_WINDOW_DAYS,
        help="Total local closing-peak window (default: 16 = 8 before + peak + 7 after); "
        "confirmed peaks enter the following day's baseline.",
    )
    parser.add_argument("--industry-filter", type=_parse_bool, default=False, metavar="True|False",
                        help="Require a declining SW L2 industry before opening (default: False); "
                        "available industry data is still calculated for observation.")
    parser.add_argument("--industry-lookback-days", type=int, default=7)
    parser.add_argument("--industry-drawdown-pct", type=float, default=0.06)
    parser.add_argument("--industry-rise-pct", type=float, default=0.06,
                        help="Rebound from the rolling lowest close; >= this threshold takes priority over decline.")
    parser.add_argument("--industry-dictionary", default=None,
                        help="SW dictionary JSON; default: cache-root/static/sw_industry_stock_dictionary.json.")
    parser.add_argument(
        "--min-breakout-gain-pct", type=float, default=DEFAULT_MIN_BREAKOUT_GAIN_PCT,
        help="Minimum gain from the mean of confirmed historical closing peaks, "
        "frozen after a strict breakout until price returns to the original "
        "peak region's upper boundary (default: 0.5 = 50%%; 0 disables the filter).",
    )
    parser.add_argument(
        "--candidate-pullback-pct", type=float,
        default=DEFAULT_CANDIDATE_PULLBACK_PCT,
        help="Close-price pullback from the running candidate high (default: %(default)s).",
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
        "--trough-ma-days",
        "--trough-confirm-days",
        "--trough-heat-days",
        dest="trough_ma_days",
        type=int,
        default=DEFAULT_TROUGH_MA_DAYS,
        help="Trailing close-price MA window for trough confirmation, including "
        "today; confirm on the first later close above the MA (default: %(default)s).",
    )
    parser.add_argument("--include-st", action="store_true")
    parser.add_argument("--write-report", action="store_true",default=True)
    parser.add_argument(
        "--sample-chart-codes",
        action="store_true",
        default=False,
        help="Save up to 30 worst-loss, 30 smallest-gain and 30 largest-gain "
        "stocks for charts; gains are ordered descending and duplicates removed.",
    )
    parser.add_argument("--records-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument(
        "--lending-file", default=DEFAULT_LENDING_FILE,
        help="Explicit HTSC availability/inventory CSV or legacy export; otherwise "
        "use the latest file under --lending-root, including .partial.csv checkpoints.",
    )
    parser.add_argument("--lending-root", default=str(DEFAULT_LENDING_ROOT))
    return parser.parse_args()


def main() -> int:
    begin_time = time()
    args = parse_args()
    industry_config = IndustryConfig(args.industry_lookback_days, args.industry_drawdown_pct, args.industry_rise_pct)
    lending_path = None
    lending_data = None
    lending_date = None
    eligible_codes: set[str] = set()
    if not args.all_stocks or args.write_report:
        lending_path = _resolve_optional_lending_file(args)
        if lending_path is not None:
            lending_data, lending_selection_rule = read_lending_stock_pool(lending_path)
            lending_dates = set(lending_data["lending_date"].dropna().astype(str))
            if len(lending_dates) != 1:
                raise ValueError(
                    "lending data must contain exactly one snapshot date: "
                    f"found {', '.join(sorted(lending_dates)) or 'none'}"
                )
            lending_date = next(iter(lending_dates))
            if lending_date > str(args.date).replace("-", ""):
                raise ValueError(
                    "lending data date must be on or before the strategy report date: "
                    f"report {args.date}, snapshot {lending_date}"
                )
            eligible_lending = lending_data[lending_data["eligible"]]
            eligible_codes = set(eligible_lending["ts_code"])
        elif not args.all_stocks:
            raise FileNotFoundError(
                "--all-stocks False requires a securities-lending file on or before "
                f"{args.date}; specify --lending-file or --lending-root."
            )
    stock_codes = args.stock_code
    if not args.all_stocks:
        stock_codes = sorted(
            eligible_codes if stock_codes is None
            else eligible_codes.intersection(stock_codes)
        )
    daily, basic = read_cached_stock_daily(
        cache_root=args.cache_root,
        start_date=args.start_date,
        as_of_date=args.date,
    )
    if not args.all_stocks:
        daily = daily[daily["ts_code"].isin(stock_codes)]
    data_cost_time = time()
    states = calculate_stock_short_states(
        daily,
        as_of_date=args.date,
        lookback_days=args.lookback_days,
        candidate_pullback_pct=args.candidate_pullback_pct,
        min_breakout_gain_pct=args.min_breakout_gain_pct,
        cost_peak_window_days=args.cost_peak_window_days,
        confirm_drawdown_pct=args.confirm_drawdown_pct,
        rebound_pct=args.rebound_pct,
        high_heat_days=args.high_heat_days,
        trough_ma_days=args.trough_ma_days,
        stock_codes=stock_codes,
        stock_basic=basic,
        include_st=args.include_st,
    )
    industry_states = pd.DataFrame(columns=TREND_COLUMNS)
    if not states.empty:
        try:
            industry_bars, memberships = read_industry_inputs(
                cache_root=args.cache_root, dictionary_path=args.industry_dictionary,
                as_of_date=args.date,
            )
        except FileNotFoundError:
            if args.industry_filter or args.industry_dictionary is not None:
                raise
            states = attach_industry_states(states, enabled=False, config=industry_config)
        else:
            industry_states = calculate_industry_states(industry_bars, config=industry_config)
            states = attach_industry_states(
                states, industry_states, memberships,
                enabled=args.industry_filter, config=industry_config,
            )
            industry_states = industry_states[industry_states["industry_trade_date"].ge(str(states["trade_date"].min()))]
    else:
        states = attach_industry_states(states, enabled=args.industry_filter, config=industry_config)
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
        min_breakout_gain_pct=args.min_breakout_gain_pct,
        cost_peak_window_days=args.cost_peak_window_days,
        confirm_drawdown_pct=args.confirm_drawdown_pct,
        rebound_pct=args.rebound_pct,
        high_heat_days=args.high_heat_days,
        trough_ma_days=args.trough_ma_days,
    )
    if args.write_report:
        backtest = build_short_trade_backtest(states, as_of_date=args.date)
        run_directory = create_stock_short_run_directory(
            as_of_date=args.date, records_root=args.records_root,
        )
        if args.industry_filter or not industry_states.empty:
            industry_states.to_csv(run_directory / "industry_states.csv", index=False, encoding="utf-8-sig")
        pd.DataFrame(backtest["industry_blocked_entries"], columns=["ts_code", "stock_name", "trade_date", "cycle_id", "entry_signal_type", *INDUSTRY_COLUMNS]).to_csv(
            run_directory / "industry_blocked_entries.csv", index=False, encoding="utf-8-sig",
        )
        csv_path  = save_stock_short_state_report(
            report,
            states,
            records_root=args.records_root,
            output_directory=run_directory,
        )
        backtest_path = save_short_trade_backtest_report(
            backtest,
            records_root=args.records_root,
            output_directory=run_directory,
        )
        if args.sample_chart_codes:
            sampled_codes = sample_stock_trade_codes(backtest["trades"])
            if not sampled_codes:
                sampled_codes = sorted(states["ts_code"].unique())
            codes_text = "\n".join(sampled_codes) + "\n"
            snapshot_path = csv_path.parent / (
                f"sampled_chart_codes_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.txt"
            )
            with snapshot_path.open("x", encoding="utf-8") as snapshot:
                snapshot.write(codes_text)
            (run_directory / RUN_CODES_FILENAME).write_text(codes_text, encoding="utf-8")
        market_cap_backtests = split_short_trade_backtest_by_average_market_cap(
            backtest
        )
        below_300yi_path = save_short_trade_backtest_report(
            market_cap_backtests["below"],
            records_root=args.records_root,
            filename="short_trade_backtest_avg_mv_below_300yi.md",
            output_directory=run_directory,
        )
        at_least_300yi_path = save_short_trade_backtest_report(
            market_cap_backtests["at_least"],
            records_root=args.records_root,
            filename="short_trade_backtest_avg_mv_at_least_300yi.md",
            output_directory=run_directory,
        )
        lending_backtest = None
        lending_backtest_path = None
        shortable_paths = None
        if lending_path is not None:
            lending_backtest = filter_short_trade_backtest_by_stock_pool(
                backtest,
                eligible_codes,
                pool_label=f"{lending_date}可融券股票池",
                pool_date=lending_date,
                source_file=str(lending_path),
                selection_rule=lending_selection_rule,
                source_record_count=len(lending_data),
                matched_state_stock_count=len(
                    eligible_codes.intersection(set(states["ts_code"]))
                ),
            )
            lending_backtest_path = save_short_trade_backtest_report(
                lending_backtest,
                records_root=args.records_root,
                filename=f"short_trade_backtest_lendable_{args.date}.md",
                output_directory=run_directory,
            )
            shortable_list = build_shortable_stock_list(
                states, lending_data,
                as_of_date=args.date,
                source_file=lending_path,
                selection_rule=lending_selection_rule,
                candidate_pullback_pct=args.candidate_pullback_pct,
                confirm_drawdown_pct=args.confirm_drawdown_pct,
                stock_codes=args.stock_code,
                stock_basic=basic,
                include_st=args.include_st,
            )
            shortable_paths = save_shortable_stock_list(
                shortable_list, output_directory=run_directory,
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
        if shortable_paths is not None:
            output["shortable_stocks"] = {suffix: str(path) for suffix, path in shortable_paths.items()}
            print(f"可做空股票状态列表：{shortable_paths['html']}（{shortable_list['summary']['stocks']}只）")
        print(f"本次报告与图表目录：{run_directory}")
    # else:
        # output = report
    # print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    end_time = time()
    print(f"读数据花费 {data_cost_time - begin_time}秒，计算状态花费{state_cal_time - data_cost_time}秒,状态merge花了{state_merge_time-state_cal_time}秒， 回测花了{end_time - state_merge_time}秒")
    return 0


def _resolve_optional_lending_file(args: argparse.Namespace) -> Path | None:
    try:
        return resolve_stock_pool_file(
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
