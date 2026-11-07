"""Reports for stock short-side high-region states."""

from __future__ import annotations

from datetime import datetime
from html import escape
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from stock_selection.strategies.stock_short_high_state.industry_state import (
    BLOCK_NAMES, industry_filter_metadata, industry_rule_notes,
)

from stock_selection.strategies.stock_short_high_state.cost_zone import (
    DEFAULT_COST_PEAK_WINDOW_DAYS, BREAKOUT_REASON_LABELS, COST_ZONE_STATUS_LABELS,
    cost_zone_rule_notes, format_cost_peaks,
)
from stock_selection.data.market_cap import format_market_cap
from stock_selection.strategies.stock_short_high_state.screen import (
    BREAKOUT_BASELINE_RULE,
    DEFAULT_CANDIDATE_PULLBACK_PCT,
    DEFAULT_MIN_BREAKOUT_GAIN_PCT,
    latest_stock_short_states,
)
from stock_selection.strategies.stock_short_high_state.stock_pool import (
    SHORTABLE_STAGES,
    SHORTABLE_STOCK_COLUMNS,
)


DEFAULT_OUTPUT_ROOT = Path(__file__).resolve().parent / "output"

_TRADE_STATUS_LABELS = {
    "COMPLETED": "已完成",
    "OPEN": "持仓中",
    "INVALIDATED": "已失效",
}
_ENTRY_SOURCE_LABELS = {
    "CANDIDATE_DRAWDOWN_THRESHOLD": "候选高点回落达阈值",
    "OBSERVED_HIGH_CANDIDATE": "实时候选",
    "OBSERVED_HIGH_CANDIDATE_PULLBACK": "候选回调",
    "RECONSTRUCTED_STATE_START": "候选起点重建",
    "OBSERVED_PEAK_START": "波峰开始",
}


def build_stock_short_state_report(
    states: pd.DataFrame,
    *,
    as_of_date: str,
    lookback_days: int,
    candidate_pullback_pct: float = DEFAULT_CANDIDATE_PULLBACK_PCT,
    min_breakout_gain_pct: float = DEFAULT_MIN_BREAKOUT_GAIN_PCT,
    cost_peak_window_days: int = DEFAULT_COST_PEAK_WINDOW_DAYS,
    confirm_drawdown_pct: float,
    rebound_pct: float,
    high_heat_days: int,
    trough_ma_days: int,
) -> dict[str, Any]:
    latest = latest_stock_short_states(states)
    alerts = (
        states[states["alert_code"].ne("")]
        if states is not None and not states.empty
        else pd.DataFrame(columns=states.columns if states is not None else [])
    )
    state_counts = (
        latest["state_description"].value_counts().sort_index().to_dict()
        if not latest.empty
        else {}
    )
    return {
        "metadata": {
            "report_type": "stock_short_high_state",
            "industry_filter": industry_filter_metadata(states),
            "as_of_date": as_of_date,
            "lookback_days": lookback_days,
            "high_candidate_formula": "close >= max(previous lookback_days closes)",
            "candidate_pullback_rule": "DRAWDOWN_FROM_RUNNING_HIGH_CLOSE",
            "candidate_pullback_pct": candidate_pullback_pct,
            "min_breakout_gain_pct": min_breakout_gain_pct,
            "breakout_baseline": BREAKOUT_BASELINE_RULE,
            "cost_peak_window_days": cost_peak_window_days,
            "confirm_drawdown_pct": confirm_drawdown_pct,
            "rebound_pct": rebound_pct,
            "high_heat_days": high_heat_days,
            "trough_ma_days": trough_ma_days,
            "trough_confirmation_rule": "FIRST_CLOSE_ABOVE_TRAILING_MA_AFTER_LOW",
            "ma_includes_current_close": True,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
        "summary": {
            "state_rows": int(len(states)),
            "stocks": int(latest["ts_code"].nunique()) if not latest.empty else 0,
            "alerts": int(len(alerts)),
            "latest_state_counts": {
                str(key): int(value) for key, value in state_counts.items()
            },
        },
        "latest_states": _records(latest),
        "alerts": _records(alerts),
    }


def render_stock_short_state_markdown(report: dict[str, Any]) -> str:
    metadata = report["metadata"]
    summary = report["summary"]
    lines = [
        f"# 策略五：高位做空状态 - {metadata['as_of_date']}",
        "",
        "## 默认规则",
        "",
        *(f"- {rule}" for rule in industry_rule_notes(metadata)),
        f"- 半年窗口：{metadata['lookback_days']}个交易日",
        f"- 高点候选条件：当日收盘价达到或超过前{metadata['lookback_days']}个交易日最高收盘价（不含当日，包含等于）",
        f"- 候选回调：收盘价从运行最高收盘价回落至少{metadata['candidate_pullback_pct']:.1%}，不要求K线颜色或连续下跌天数",
        f"- {cost_zone_rule_notes(metadata['cost_peak_window_days'])}最低突破涨幅：{metadata['min_breakout_gain_pct']:.1%}",
        f"- 最高点确认回落：{metadata['confirm_drawdown_pct']:.1%}",
        f"- 波峰开启反弹：{metadata['rebound_pct']:.1%}",
        f"- 最高点热度窗口：{metadata['high_heat_days']}个交易日",
        f"- 波谷确认均线：MA{metadata['trough_ma_days']}，包含当日收盘价的最近{metadata['trough_ma_days']}个有效个股交易日收盘价算术平均，必须具备完整窗口",
        "- 波谷确认：候选低点之后，首次收盘价严格高于均线当天确认；更低收盘价更新候选低点。确认后回标最低点至确认日，实际平仓仅使用确认当天收盘价",
        "",
        "## 汇总",
        "",
        f"- 股票数量：{summary['stocks']}",
        f"- 状态记录：{summary['state_rows']}",
        f"- 再创新高告警：{summary['alerts']}",
        "",
        "## 最新状态",
        "",
        "| 股票 | 日期 | 收盘价 | 总市值 | 流通市值 | 枚举值 | 状态 | 波峰序号 | 热度 | 历史峰值 | 成本区 | 峰值均值 | 冻结日期 | 突破涨幅 | 过滤原因 |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | --- | --- | ---: | --- | ---: | --- |",
    ]
    if not report["latest_states"]:
        lines.append("| " + " | ".join(["-"] * 15) + " |")
    for row in report["latest_states"]:
        heat = "-" if row.get("heat") is None else f"{row['heat']:.2%}"
        lines.append(
            f"| {row['ts_code']} {row.get('stock_name') or ''} | "
            f"{row['trade_date']} | {row['close']:.2f} | "
            f"{format_market_cap(row.get('total_mv'))} | "
            f"{format_market_cap(row.get('circ_mv'))} | {row['state']} | "
            f"{row['state_description']} | {row['peak_number']} | {heat} | "
            f"{format_cost_peaks(row.get('cost_peak_points'))} | "
            f"{_format_number(row.get('cost_zone_low'))}～{_format_number(row.get('cost_zone_high'))} | "
            f"{_format_number(row.get('breakout_base_close'))} | {row.get('cost_zone_freeze_date') or '-'} | "
            f"{_format_percent(row.get('breakout_gain_pct'))} | {BREAKOUT_REASON_LABELS.get(row.get('entry_breakout_reason'), '-')} |"
        )
    return "\n".join(lines) + "\n"


def create_stock_short_run_directory(
    *,
    as_of_date: str,
    records_root: str | Path = DEFAULT_OUTPUT_ROOT,
) -> Path:
    """Reserve a distinct directory for one strategy run and its charts."""
    date_text = str(as_of_date)
    directory = Path(records_root) / (
        f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:8]}"
    ) / f"charts_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    directory.mkdir(parents=True, exist_ok=False)
    return directory


def save_stock_short_state_report(
    report: dict[str, Any],
    states: pd.DataFrame,
    *,
    records_root: str | Path = DEFAULT_OUTPUT_ROOT,
    output_directory: str | Path | None = None,
) -> Path:
    directory = (
        Path(output_directory)
        if output_directory is not None
        else create_stock_short_run_directory(
            as_of_date=report["metadata"]["as_of_date"], records_root=records_root,
        )
    )
    directory.mkdir(parents=True, exist_ok=True)
    csv_path = directory / "states.csv"
    states.to_csv(csv_path, index=False, encoding="utf-8-sig")
    return csv_path


def render_short_trade_backtest_markdown(
    backtest: dict[str, Any],
    *,
    wide_report_filename: str | None = None,
) -> str:
    """Render the state-transition short backtest as a standalone report."""
    metadata = backtest["metadata"]
    summary = backtest["summary"]
    market_cap_filter = metadata.get("market_cap_filter")
    stock_pool_filter = metadata.get("stock_pool_filter")
    filter_labels = [
        report_filter["label"]
        for report_filter in (market_cap_filter, stock_pool_filter)
        if report_filter is not None
    ]
    title_suffix = "" if not filter_labels else f" - {' - '.join(filter_labels)}"
    lines = [
        f"# 策略五：状态做空回测{title_suffix} - {metadata['as_of_date']}",
        *(
            [
                "",
                f"> [打开铺满窗口的宽屏 HTML 版](./{wide_report_filename})",
            ]
            if wide_report_filename is not None
            else []
        ),
        "",
        "## 交易规则",
        "",
        *(f"- {rule}" for rule in _entry_rules(metadata)),
        *(f"- {rule}" for rule in _normal_exit_rules(metadata)),
        *(f"- {rule}" for rule in _profit_rebound_exit_rules(metadata)),
        *(f"- {rule}" for rule in _drawdown_stop_rules(metadata)),
        "- 新高失效按当日收盘价强制平仓，原因 HIGH_INVALIDATION_EXIT；周期异常切换按首次发现切换当日收盘价强制平仓，原因 CYCLE_CHANGE_EXIT。均计入已完成交易、收益和胜率；同日回撤止损优先。",
        "- 报告日尚未平仓的交易记为持仓中，不计入已完成收益和胜率。跳过信号数附属于阻止它开仓的持仓；市值分组报告按该持仓的平均市值归组。",
        "- 平均 / 中位收益统计本报告全部已完成交易，包含持平交易；正收益与负收益的平均 / 中位收益分别只统计盈利与亏损交易，沿用胜 / 负 / 平的划分口径（绝对收益不超过1e-12视为持平）。对应组没有交易时显示“-”。",
        "- 收益率按 `1 - 平仓价 / 开仓价` 计算。未计手续费、滑点、融券成本、涨跌停限制和实际可融券约束。",
        "- 所有交易受回撤止损约束，已完成交易包含止损和失效平仓；胜率需结合持仓中数量和回撤解读。",
        "- 逐笔复利和逐笔复利最大回撤按平仓日期依次连接所有已完成交易，只用于比较信号，不是考虑重叠持仓、仓位和资金占用的真实组合净值。",
    ]
    if market_cap_filter is not None:
        lines.extend(
            [
                f"- 本报告分组：{market_cap_filter['label']}。平均总市值为每笔交易从开仓日至结束/估值日所有可用 `total_mv` 的算术平均值。",
                f"- 市值边界：300亿元等于 Tushare `total_mv=3,000,000万元`；本组使用 `{market_cap_filter['comparison']}` 边界。完全缺少市值的交易已排除 {market_cap_filter['missing_market_cap_trades_excluded']} 笔。",
            ]
        )
    if stock_pool_filter is not None:
        matched_state_stocks = stock_pool_filter.get("matched_state_stock_count")
        matched_state_text = (
            "-"
            if matched_state_stocks is None
            else str(int(matched_state_stocks))
        )
        lines.extend(
            [
                f"- 本报告股票池：{stock_pool_filter['label']}，快照日期 `{stock_pool_filter['pool_date']}`，筛选规则为“{stock_pool_filter['selection_rule']}”。",
                f"- 股票池来源：`{stock_pool_filter['source_file']}`；源记录 {stock_pool_filter.get('source_record_count') or '-'} 条，符合条件代码 {stock_pool_filter['eligible_code_count']} 个，其中进入策略五状态数据 {matched_state_text} 只、产生交易信号 {stock_pool_filter['matched_trade_stock_count']} 只。",
                "- 这是按上述券源快照构成的固定股票池，只回答这些股票上的历史状态收益；它不能证明历史开仓日当时有券可借，也没有计入可借数量变化、融券费率和强制归还风险。",
            ]
        )
    lines.extend(
        [
            "",
            "## 总体统计",
            "",
            f"- 实际开仓数：{summary['signals']}",
            *([f"- 空仓时被行业过滤拦截的信号：{summary['industry_blocked_signals']}（原始信号与原因见同批次 industry_blocked_entries.csv）"] if "industry_blocked_signals" in summary else []),
            f"- 因已有持仓跳过的开仓信号：{summary['skipped_while_holding']}",
            f"- 因平仓当日跳过的开仓信号：{summary['skipped_on_exit_day']}",
            f"- 已完成：{summary['completed_trades']}",
            f"- 其中回撤止损平仓：{summary.get('drawdown_stop_trades', 0)}",
            f"- 其中盈利后连续上涨平仓：{summary.get('profit_rebound_exit_trades', 0)}",
            f"- 持仓中：{summary['open_trades']}",
            f"- 其中新高失效平仓：{summary['invalidation_exit_trades']}",
            f"- 其中周期异常切换平仓：{summary['cycle_change_exit_trades']}",
            f"- 其中波谷确认平仓：{summary['trough_exit_trades']}",
            f"- 胜 / 负 / 平：{summary['wins']} / {summary['losses']} / {summary['breakeven']}",
            f"- 胜率：{_format_percent(summary['win_rate'])}",
            f"- 平均 / 中位收益：{_format_percent(summary['average_return_pct'])} / {_format_percent(summary['median_return_pct'])}",
            f"- 正收益平均 / 中位收益：{_format_percent(summary['average_positive_return_pct'])} / {_format_percent(summary['median_positive_return_pct'])}",
            f"- 负收益平均 / 中位收益：{_format_percent(summary['average_negative_return_pct'])} / {_format_percent(summary['median_negative_return_pct'])}",
            f"- 最好 / 最差收益：{_format_percent(summary['best_return_pct'])} / {_format_percent(summary['worst_return_pct'])}",
            f"- 逐笔复利收益：{_format_percent(summary['compounded_return_pct'])}",
            f"- 逐笔复利最大回撤：{_format_percent(summary['sequential_max_drawdown_pct'])}",
            f"- 平均持有交易日：{_format_number(summary['average_holding_trade_days'])}",
            f"- 平均最大有利幅度 MFE：{_format_percent(summary['average_mfe_pct'])}",
            f"- 平均最大不利幅度 MAE：{_format_percent(summary['average_mae_pct'])}",
            f"- 平均 / 最差单笔最大回撤：{_format_percent(summary['average_trade_max_drawdown_pct'])} / {_format_percent(summary['worst_trade_max_drawdown_pct'])}",
            "",
            "## 按开仓状态统计",
            "",
            "| 开仓状态 | 实际开仓 | 完成 | 持仓中 | 失效平仓 | 胜率 | 平均收益 | 中位收益 | 最差收益 | 平均持有日 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    if not backtest["by_entry_state"]:
        lines.append("| - | 0 | 0 | 0 | 0 | - | - | - | - | - |")
    for row in backtest["by_entry_state"]:
        lines.append(
            f"| {row['entry_state']} | {row['signals']} | "
            f"{row['completed_trades']} | {row['open_trades']} | "
            f"{row['invalidation_exit_trades'] + row['cycle_change_exit_trades']} | {_format_percent(row['win_rate'])} | "
            f"{_format_percent(row['average_return_pct'])} | "
            f"{_format_percent(row['median_return_pct'])} | "
            f"{_format_percent(row['worst_return_pct'])} | "
            f"{_format_number(row['average_holding_trade_days'])} |"
        )

    lines.extend(
        [
            "",
            "## 逐笔交易",
            "",
            "| 股票 | 周期 | 开仓状态 | 信号来源 | 开仓日 | 开仓价 | 平均总市值 | 市值样本日 | 开仓总市值 | 开仓流通市值 | 结束状态 | 结束/估值日 | 结束/估值价 | 状态 | 已实现收益 | 浮动收益 | MFE | MAE | 最大回撤 | 持有日 |",
            "| --- | ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    if not backtest["trades"]:
        lines.append(
            "| - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - |"
        )
    for trade in backtest["trades"]:
        status = _TRADE_STATUS_LABELS.get(trade["status"], trade["status"])
        entry_source = _ENTRY_SOURCE_LABELS.get(
            trade["entry_source"], trade["entry_source"]
        )
        lines.append(
            f"| {trade['ts_code']} {trade.get('stock_name') or ''} | "
            f"{trade['cycle_id']} | {trade['entry_state']} | {entry_source} | "
            f"{trade['entry_date']} | {trade['entry_close']:.2f} | "
            f"{format_market_cap(trade.get('average_total_mv'))} | "
            f"{trade.get('market_cap_observation_days', 0)} | "
            f"{format_market_cap(trade.get('entry_total_mv'))} | "
            f"{format_market_cap(trade.get('entry_circ_mv'))} | "
            f"{trade['exit_state']} | {trade['evaluation_date']} | "
            f"{trade['evaluation_close']:.2f} | {status} | "
            f"{_format_percent(trade.get('return_pct'))} | "
            f"{_format_percent(trade.get('mark_return_pct'))} | "
            f"{_format_percent(trade.get('mfe_pct'))} | "
            f"{_format_percent(trade.get('mae_pct'))} | "
            f"{_format_percent(trade.get('max_drawdown_pct'))} | "
            f"{trade['holding_trade_days']} |"
        )
    lines.extend([
        "", "## 开平仓核对", "",
        "逐笔核对开仓信号、结束/估值日的实际状态、波谷确认日期及均线值；实际收盘与模拟止损成交价分别展示。", "",
        "| " + " | ".join(_TRADE_AUDIT_HEADERS) + " |",
        "| " + " | ".join("---" for _ in _TRADE_AUDIT_HEADERS) + " |",
    ])
    for trade in backtest["trades"]:
        lines.append("| " + " | ".join(_trade_audit_values(trade)) + " |")
    if not backtest["trades"]:
        lines.append("| " + " | ".join("-" for _ in _TRADE_AUDIT_HEADERS) + " |")
    return "\n".join(lines) + "\n"


def render_short_trade_backtest_html(backtest: dict[str, Any]) -> str:
    """Render a viewport-wide HTML version of the short backtest report."""
    metadata = backtest["metadata"]
    summary = backtest["summary"]
    market_cap_filter = metadata.get("market_cap_filter")
    stock_pool_filter = metadata.get("stock_pool_filter")
    filter_labels = [
        report_filter["label"]
        for report_filter in (market_cap_filter, stock_pool_filter)
        if report_filter is not None
    ]
    title_suffix = "" if not filter_labels else f" - {' - '.join(filter_labels)}"
    title = f"策略五：状态做空回测{title_suffix} - {metadata['as_of_date']}"

    filter_notes: list[str] = []
    if market_cap_filter is not None:
        filter_notes.extend(
            [
                f"本报告分组：{escape(str(market_cap_filter['label']))}。平均总市值为每笔交易从开仓日至结束/估值日所有可用 <code>total_mv</code> 的算术平均值。",
                f"市值边界：300亿元等于 Tushare <code>total_mv=3,000,000万元</code>；本组使用 <code>{escape(str(market_cap_filter['comparison']))}</code> 边界。完全缺少市值的交易已排除 {int(market_cap_filter['missing_market_cap_trades_excluded'])} 笔。",
            ]
        )
    if stock_pool_filter is not None:
        matched_state_stocks = stock_pool_filter.get("matched_state_stock_count")
        matched_state_text = (
            "-" if matched_state_stocks is None else str(int(matched_state_stocks))
        )
        filter_notes.extend(
            [
                f"本报告股票池：{escape(str(stock_pool_filter['label']))}，快照日期 <code>{escape(str(stock_pool_filter['pool_date']))}</code>，筛选规则为“{escape(str(stock_pool_filter['selection_rule']))}”。",
                f"股票池来源：<code>{escape(str(stock_pool_filter['source_file']))}</code>；源记录 {stock_pool_filter.get('source_record_count') or '-'} 条，符合条件代码 {int(stock_pool_filter['eligible_code_count'])} 个，其中进入策略五状态数据 {matched_state_text} 只、产生交易信号 {int(stock_pool_filter['matched_trade_stock_count'])} 只。",
                "这是按上述券源快照构成的固定股票池，只回答这些股票上的历史状态收益；它不能证明历史开仓日当时有券可借，也没有计入可借数量变化、融券费率和强制归还风险。",
            ]
        )

    summary_items = [
        ("实际开仓数", summary["signals"]),
        *([("空仓时行业拦截信号", summary["industry_blocked_signals"])] if "industry_blocked_signals" in summary else []),
        ("持仓期间跳过信号", summary["skipped_while_holding"]),
        ("平仓当日跳过信号", summary["skipped_on_exit_day"]),
        ("已完成", summary["completed_trades"]),
        ("其中回撤止损", summary.get("drawdown_stop_trades", 0)),
        ("盈利后连续上涨平仓", summary.get("profit_rebound_exit_trades", 0)),
        ("持仓中", summary["open_trades"]),
        ("其中新高失效平仓", summary["invalidation_exit_trades"]),
        ("其中周期切换平仓", summary["cycle_change_exit_trades"]),
        ("波谷确认平仓", summary["trough_exit_trades"]),
        ("胜 / 负 / 平", f"{summary['wins']} / {summary['losses']} / {summary['breakeven']}"),
        ("胜率", _format_percent(summary["win_rate"])),
        ("平均 / 中位收益", f"{_format_percent(summary['average_return_pct'])} / {_format_percent(summary['median_return_pct'])}"),
        ("正收益平均 / 中位收益", f"{_format_percent(summary['average_positive_return_pct'])} / {_format_percent(summary['median_positive_return_pct'])}"),
        ("负收益平均 / 中位收益", f"{_format_percent(summary['average_negative_return_pct'])} / {_format_percent(summary['median_negative_return_pct'])}"),
        ("最好 / 最差收益", f"{_format_percent(summary['best_return_pct'])} / {_format_percent(summary['worst_return_pct'])}"),
        ("逐笔复利收益", _format_percent(summary["compounded_return_pct"])),
        ("逐笔复利最大回撤", _format_percent(summary["sequential_max_drawdown_pct"])),
        ("平均持有交易日", _format_number(summary["average_holding_trade_days"])),
        ("平均 MFE", _format_percent(summary["average_mfe_pct"])),
        ("平均 MAE", _format_percent(summary["average_mae_pct"])),
        ("平均 / 最差单笔最大回撤", f"{_format_percent(summary['average_trade_max_drawdown_pct'])} / {_format_percent(summary['worst_trade_max_drawdown_pct'])}"),
    ]
    summary_html = "".join(
        f'<div class="metric"><span>{escape(str(label))}</span><strong>{escape(str(value))}</strong></div>'
        for label, value in summary_items
    )

    state_rows = []
    for row in backtest["by_entry_state"]:
        values = [
            row["entry_state"],
            row["signals"],
            row["completed_trades"],
            row["open_trades"],
            row["invalidation_exit_trades"] + row["cycle_change_exit_trades"],
            _format_percent(row["win_rate"]),
            _format_percent(row["average_return_pct"]),
            _format_percent(row["median_return_pct"]),
            _format_percent(row["worst_return_pct"]),
            _format_number(row["average_holding_trade_days"]),
        ]
        state_rows.append(
            "<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in values) + "</tr>"
        )
    if not state_rows:
        state_rows.append('<tr><td colspan="10">没有可统计的开仓状态</td></tr>')

    trade_rows = []
    for trade in backtest["trades"]:
        status = _TRADE_STATUS_LABELS.get(trade["status"], trade["status"])
        entry_source = _ENTRY_SOURCE_LABELS.get(
            trade["entry_source"], trade["entry_source"]
        )
        values = [
            f"{trade['ts_code']} {trade.get('stock_name') or ''}",
            trade["cycle_id"],
            trade["entry_state"],
            entry_source,
            trade["entry_date"],
            f"{trade['entry_close']:.2f}",
            format_market_cap(trade.get("average_total_mv")),
            trade.get("market_cap_observation_days", 0),
            format_market_cap(trade.get("entry_total_mv")),
            format_market_cap(trade.get("entry_circ_mv")),
            trade["exit_state"],
            trade["evaluation_date"],
            f"{trade['evaluation_close']:.2f}",
            status,
            _format_percent(trade.get("return_pct")),
            _format_percent(trade.get("mark_return_pct")),
            _format_percent(trade.get("mfe_pct")),
            _format_percent(trade.get("mae_pct")),
            _format_percent(trade.get("max_drawdown_pct")),
            trade["holding_trade_days"],
        ]
        trade_rows.append(
            "<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in values) + "</tr>"
        )
    if not trade_rows:
        trade_rows.append('<tr><td colspan="20">没有逐笔交易</td></tr>')

    notes_html = "".join(f'<p class="note">{note}</p>' for note in filter_notes)
    stop_rules_html = "".join(
        f"<li>{escape(rule)}</li>" for rule in _drawdown_stop_rules(metadata)
    )
    normal_exit_rules_html = "".join(
        f"<li>{escape(rule)}</li>" for rule in _normal_exit_rules(metadata)
    )
    profit_rebound_rules_html = "".join(
        f"<li>{escape(rule)}</li>" for rule in _profit_rebound_exit_rules(metadata)
    )
    entry_rules_html = "".join(f"<li>{escape(rule)}</li>" for rule in _entry_rules(metadata))
    audit_headers_html = "".join(f"<th>{escape(value)}</th>" for value in _TRADE_AUDIT_HEADERS)
    audit_rows_html = "".join(
        "<tr>" + "".join(f"<td>{escape(value)}</td>" for value in _trade_audit_values(trade)) + "</tr>"
        for trade in backtest["trades"]
    ) or f'<tr><td colspan="{len(_TRADE_AUDIT_HEADERS)}">没有逐笔交易</td></tr>'
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>
*{{box-sizing:border-box}}
html,body{{margin:0;width:100%;max-width:none;background:#fff}}
body{{padding:24px 28px;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",Arial,sans-serif;color:#24292f;letter-spacing:0}}
main{{width:100%;max-width:none;margin:0}}
h1{{margin:0 0 22px;font-size:26px}}h2{{margin:28px 0 12px;font-size:20px;color:#075985}}
code{{padding:2px 4px;background:#f3f4f6;border-radius:3px}}
.rules{{margin:0;padding-left:22px;line-height:1.65}}.rules li{{margin:4px 0}}
.note{{margin:8px 0;padding:9px 12px;background:#f8fafc;border-left:3px solid #0e7490;line-height:1.55}}
.metrics{{display:grid;grid-template-columns:repeat(7,minmax(120px,1fr));gap:1px;background:#d0d7de;border:1px solid #d0d7de}}
.metric{{min-width:0;padding:9px 10px;background:#fff}}.metric span{{display:block;color:#57606a;font-size:12px;white-space:nowrap}}.metric strong{{display:block;margin-top:4px;font-size:15px;white-space:nowrap}}
table{{width:100%;max-width:none;border-collapse:collapse}}
th,td{{border:1px solid #c8cdd2;padding:7px 8px;text-align:right;vertical-align:middle;white-space:nowrap}}
th{{background:#e9ecef;font-weight:650;color:#24292f}}tbody tr:nth-child(even){{background:#f7f8fa}}
th:first-child,td:first-child,th:nth-child(3),td:nth-child(3),th:nth-child(4),td:nth-child(4){{text-align:left}}
.state-table{{font-size:13px}}
.trade-table{{table-layout:auto;font-size:10.5px}}
.trade-table th,.trade-table td{{padding:5px 4px}}
.audit-table{{table-layout:fixed;font-size:11px}}
.audit-table th,.audit-table td{{white-space:normal;overflow-wrap:anywhere;padding:5px 4px}}
@media print{{body{{padding:8mm}}th{{position:static}}}}
</style>
</head>
<body>
<main>
<h1>{escape(title)}</h1>
<h2>交易规则</h2>
<ul class="rules">
{entry_rules_html}
{normal_exit_rules_html}
{profit_rebound_rules_html}
{stop_rules_html}
<li>新高失效和周期异常切换按当日收盘价强制平仓，原因分别为 HIGH_INVALIDATION_EXIT、CYCLE_CHANGE_EXIT；均计入已完成收益和胜率，同日回撤止损优先。</li>
<li>持仓中交易不计入已完成交易的收益和胜率。跳过信号附属于阻止它开仓的持仓，市值分组沿用该持仓平均市值。</li>
<li>平均 / 中位收益统计本报告全部已完成交易，包含持平交易；正收益与负收益的平均 / 中位收益分别只统计盈利与亏损交易，沿用胜 / 负 / 平的划分口径（绝对收益不超过1e-12视为持平）。对应组没有交易时显示“-”。</li>
<li>收益率按“1 - 平仓价 / 开仓价”计算，未计手续费、滑点、融券成本、涨跌停限制和实际可融券约束。</li>
<li>所有交易受回撤止损约束，已完成交易包含止损和失效平仓；胜率需结合持仓中数量和回撤解读。</li>
<li>逐笔复利按平仓日期连接已完成交易，不是考虑重叠持仓、仓位和资金占用的真实组合净值。</li>
</ul>
{notes_html}
<h2>总体统计</h2>
<div class="metrics">{summary_html}</div>
<p><a href="industry_blocked_entries.csv">下载本批次行业拦截明细 CSV</a>（含二级行业、当日状态、回撤、反弹与阻止原因；市值分组的拦截数见主报告）</p>
<h2>按开仓状态统计</h2>
<table class="state-table">
<thead><tr><th>开仓状态</th><th>实际开仓</th><th>完成</th><th>持仓中</th><th>失效平仓</th><th>胜率</th><th>平均收益</th><th>中位收益</th><th>最差收益</th><th>平均持有日</th></tr></thead>
<tbody>{''.join(state_rows)}</tbody>
</table>
<h2>逐笔交易</h2>
<table class="trade-table">
<thead><tr><th>股票</th><th>周期</th><th>开仓状态</th><th>信号来源</th><th>开仓日</th><th>开仓价</th><th>平均总市值</th><th>市值样本日</th><th>开仓总市值</th><th>开仓流通市值</th><th>结束状态</th><th>结束/估值日</th><th>结束/估值价</th><th>状态</th><th>已实现收益</th><th>浮动收益</th><th>MFE</th><th>MAE</th><th>最大回撤</th><th>持有日</th></tr></thead>
<tbody>{''.join(trade_rows)}</tbody>
</table>
<h2>开平仓核对</h2>
<p class="note">逐笔核对开仓信号、结束/估值日的实际状态、波谷确认日期及均线值；实际收盘与模拟止损成交价分别展示。</p>
<table class="audit-table">
<thead><tr>{audit_headers_html}</tr></thead>
<tbody>{audit_rows_html}</tbody>
</table>
</main>
</body>
</html>
"""


def save_short_trade_backtest_report(
    backtest: dict[str, Any],
    *,
    records_root: str | Path = DEFAULT_OUTPUT_ROOT,
    filename: str = "short_trade_backtest.md",
    output_directory: str | Path | None = None,
) -> Path:
    """Save reports in the supplied run directory, or reserve a new one."""
    directory = (
        Path(output_directory)
        if output_directory is not None
        else create_stock_short_run_directory(
            as_of_date=backtest["metadata"]["as_of_date"], records_root=records_root,
        )
    )
    directory.mkdir(parents=True, exist_ok=True)
    markdown_path = directory / filename
    html_path = markdown_path.with_suffix(".html")
    markdown_path.write_text(
        render_short_trade_backtest_markdown(
            backtest, wide_report_filename=html_path.name
        ),
        encoding="utf-8",
    )
    html_path.write_text(
        render_short_trade_backtest_html(backtest),
        encoding="utf-8",
    )
    return markdown_path


def _records(data: pd.DataFrame) -> list[dict[str, Any]]:
    return [_json_safe(record) for record in data.to_dict("records")]


def save_shortable_stock_list(
    report: dict[str, Any], *, output_directory: str | Path,
) -> dict[str, Path]:
    """Save the current lendable stock list beside this run's backtest reports."""
    directory = Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = {suffix: directory / f"shortable_stocks.{suffix}" for suffix in ("csv", "md", "html")}
    pd.DataFrame(report["stocks"], columns=SHORTABLE_STOCK_COLUMNS).to_csv(
        paths["csv"], index=False, encoding="utf-8-sig",
    )
    paths["md"].write_text(render_shortable_stocks_markdown(report), encoding="utf-8")
    paths["html"].write_text(render_shortable_stocks_html(report), encoding="utf-8")
    return paths


_SHORTABLE_HEADERS = (
    "股票代码", "名称", "阶段", "阶段说明", "实际状态", "行情日期", "收盘价",
    "高点日期", "本轮高点", "回落幅度", "均线", "均线值", "波峰序号",
    "可融券数量（股）", "实际可融数量（股）", "核验状态", "查询时间", "券源日期",
    "峰值窗口", "已确认峰值（日期、价格、确认日期）", "峰值数量", "成本区下沿", "成本区上沿", "成本均值",
    "成本区状态", "冻结日期", "冻结以来最高收盘价", "突破过滤原因",
    "突破涨幅", "最低突破涨幅", "突破过滤", "行情日开仓信号",
    "二级行业", "行业状态", "行业数据状态", "行业收盘价", "行业窗口最高收盘价", "行业窗口最低收盘价",
    "行业回撤", "行业反弹", "行业过滤", "行业阻止原因",
)


def _shortable_values(row: dict[str, Any]) -> list[str]:
    return [
        row["ts_code"], row["stock_name"], row["stage_label"], row["stage_detail"],
        f"{row['observed_state_name']} ({row['observed_state']})", row["trade_date"],
        _format_number(row["close"]), row["reference_high_date"],
        _format_number(row["reference_high_close"]), _format_percent(row["drawdown_from_high_pct"]),
        f"MA{row['trough_ma_days']}", _format_number(row["trough_ma_close"]),
        str(row["peak_number"]), str(row["lendable_qty"]),
        str(row["actual_sellable_qty"]) if row["actual_sellable_qty"] is not None else "未知",
        {"available": "核验有券", "zero": "核验无券", "error": "查询失败", "unverified": "未核验"}.get(row["verification_status"], row["verification_status"]),
        row["queried_at"] or "-", row["lending_date"],
        _format_number(row.get("cost_peak_window_days"), digits=0), format_cost_peaks(row.get("cost_peak_points")),
        _format_number(row.get("cost_peak_count"), digits=0), _format_number(row.get("cost_zone_low")),
        _format_number(row.get("cost_zone_high")), _format_number(row.get("breakout_base_close")),
        COST_ZONE_STATUS_LABELS.get(row.get("cost_zone_status"), "-"), row.get("cost_zone_freeze_date") or "-",
        _format_number(row.get("cost_zone_high_close")), BREAKOUT_REASON_LABELS.get(row.get("entry_breakout_reason"), "-"),
        _format_percent(row.get("breakout_gain_pct")),
        _format_percent(row.get("min_breakout_gain_pct")),
        "通过" if row.get("entry_breakout_qualified") else "未通过",
        "是" if row["has_entry_signal"] else "否",
        f"{row.get('industry_name') or '-'} {row.get('industry_code') or ''}",
        row.get("industry_state_name") or "-", row.get("industry_data_status") or "-",
        _format_number(row.get("industry_close")), _format_number(row.get("industry_high_close")),
        _format_number(row.get("industry_low_close")), _format_percent(row.get("industry_drawdown_pct")),
        _format_percent(row.get("industry_rebound_pct")),
        "关闭" if not row.get("industry_filter_enabled") else ("通过" if row.get("entry_industry_qualified") else "拦截"),
        BLOCK_NAMES.get(row.get("entry_blocked_reason"), row.get("entry_blocked_reason") or "-"),
    ]


def _shortable_notes(report: dict[str, Any]) -> list[str]:
    metadata = report["metadata"]
    summary = report["summary"]
    excluded = "；".join(f"{reason} {count}只" for reason, count in summary["excluded"].items()) or "无"
    notes = [
        f"报告日期：{metadata['as_of_date']}；券源快照日期：{metadata['pool_date']}。可融券数量与实际可融数量分别列示。",
        f"券源文件：{metadata['source_file']}；筛选规则：{metadata['selection_rule']}。",
        *industry_rule_notes(metadata),
        f"范围内可融券名单证券 {summary['eligible_stocks_in_scope']}只，列入以下阶段 {summary['stocks']}只；未列入：{excluded}。",
        f"候选回落阈值 {_format_percent(metadata['candidate_pullback_pct'])}，高点确认阈值 {_format_percent(metadata['confirm_drawdown_pct'])}。确认回撤、波谷和反弹按当日实际状态划分。",
        "行情日期为各股票最近的有效交易日，可能早于报告日期。阶段使用当日实际状态，不使用事后回标的高低点标签。",
        "开仓信号对应表中行情日期的候选回调事件；是否实际开仓仍由持仓规则判断。",
        cost_zone_rule_notes(metadata.get("cost_peak_window_days", DEFAULT_COST_PEAK_WINDOW_DAYS)),
    ]
    if metadata.get("actual_quantity_verified"):
        notes.append(
            f"按可融券名单计算，不按数量或核验状态筛选；文件内实际数量为0 {summary['zero_actual_quantity']}只，查询失败 {summary['unverified_quantity']}只，均保留参与计算。列表仍按上述阶段展示，查询失败的实际数量显示未知。数量仅代表各行查询时间的结果。"
        )
    else:
        notes.append("此文件未包含实际数量核验结果，名单中的证券均参与计算，包括可融券数量为0的证券；实际可融数量显示未知。")
    if metadata.get("partial_snapshot"):
        notes.append("使用最新查询断点文件，扫描尚未完成；未查询或查询失败的证券不能据此认定有券。")
    return notes


def render_shortable_stocks_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# 策略五：可做空股票状态列表 - {report['metadata']['as_of_date']}",
        "", "[打开彩色 HTML 列表](./shortable_stocks.html) · [下载 CSV](./shortable_stocks.csv)",
        "", *(f"- {note}" for note in _shortable_notes(report)), "",
    ]
    for stage, label in SHORTABLE_STAGES.items():
        rows = [row for row in report["stocks"] if row["stage"] == stage]
        lines.extend([
            f"## {label}（{len(rows)}只）", "",
            "| " + " | ".join(_SHORTABLE_HEADERS) + " |",
            "| " + " | ".join("---" for _ in _SHORTABLE_HEADERS) + " |",
        ])
        for row in rows:
            values = [escape(value).replace("|", "&#124;").replace("\n", " ") for value in _shortable_values(row)]
            lines.append("| " + " | ".join(values) + " |")
        if not rows:
            lines.append("| " + " | ".join("-" for _ in _SHORTABLE_HEADERS) + " |")
        lines.append("")
    return "\n".join(lines)


def render_shortable_stocks_html(report: dict[str, Any]) -> str:
    title = f"策略五：可做空股票状态列表 - {report['metadata']['as_of_date']}"
    notes = "".join(f"<li>{escape(note)}</li>" for note in _shortable_notes(report))
    cards = "".join(
        f'<a class="card {stage}" href="#{stage}">{escape(label)} '
        f'<strong>{report["summary"]["by_stage"][stage]}</strong>只</a>'
        for stage, label in SHORTABLE_STAGES.items()
    )
    headers = "".join(f"<th>{escape(value)}</th>" for value in _SHORTABLE_HEADERS)
    sections = []
    for stage, label in SHORTABLE_STAGES.items():
        rows = [row for row in report["stocks"] if row["stage"] == stage]
        body = "".join(
            '<tr>' + ''.join(f'<td>{escape(value)}</td>' for value in _shortable_values(row)) + '</tr>'
            for row in rows
        ) or f'<tr><td colspan="{len(_SHORTABLE_HEADERS)}">此阶段暂无股票</td></tr>'
        sections.append(
            f'<section id="{stage}" class="{stage}"><h2>{escape(label)}（{len(rows)}只）</h2>'
            f'<div class="table-wrap"><table><thead><tr>{headers}</tr></thead><tbody>{body}</tbody></table></div></section>'
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>
body{{font-family:Arial,"Microsoft YaHei",sans-serif;margin:24px;color:#182230;background:#f8fafc}}
a{{color:#0969da}}.cards{{display:flex;gap:12px;flex-wrap:wrap;margin:24px 0}}
.card{{padding:16px;border:1px solid #cbd5e1;border-radius:8px;text-decoration:none;color:#182230}}
.card strong{{font-size:24px;margin-left:12px}}li{{margin:7px 0}}section{{margin:28px 0}}
section h2{{padding:12px 16px;border-radius:8px 8px 0 0;margin:0}}
.HIGH_NO_PULLBACK.card,.HIGH_NO_PULLBACK h2{{background:#dbeafe}}
.PULLBACK.card,.PULLBACK h2{{background:#fef3c7}}
.CONFIRMED_PULLBACK.card,.CONFIRMED_PULLBACK h2{{background:#fee2e2}}
.BOTTOM_REBOUND.card,.BOTTOM_REBOUND h2{{background:#dcfce7}}
.table-wrap{{overflow-x:auto;background:white}}table{{border-collapse:collapse;width:100%;white-space:nowrap}}
th,td{{border:1px solid #e2e8f0;padding:10px;text-align:left;font-variant-numeric:tabular-nums}}
th{{background:#f1f5f9}}tbody tr:hover{{background:#f8fafc}}
</style>
</head>
<body>
<h1>{escape(title)}</h1>
<p><a href="shortable_stocks.csv">下载 CSV</a> · <a href="shortable_stocks.md">查看 Markdown</a> · <a href="short_trade_backtest.html">本次回测报告</a></p>
<ul>{notes}</ul>
<nav class="cards">{cards}</nav>
{''.join(sections)}
</body>
</html>
"""


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if np.isnan(value) else float(value)
    if pd.isna(value):
        return None
    return value


_TRADE_AUDIT_HEADERS = (
    "股票", "周期/波峰", "开仓日", "结束/估值日", "结束原因",
    "当日实际状态 / 收盘 / 波谷确认日",
    "开仓信号 / 起始日 / 判定高点", "历史峰值 / 成本区及均值 / 冻结日期 / 突破涨幅", "跳过信号数",
    "结束日均线 / 数值",
    "结束日开仓后连续收盘上涨天数",
    "开仓时二级行业 / 状态 / 回撤 / 反弹",
)


def _trade_audit_values(trade: dict[str, Any]) -> list[str]:
    return [
        f"{trade['ts_code']} {trade.get('stock_name') or ''}",
        f"{trade['cycle_id']} / {trade['peak_number']}",
        str(trade["entry_date"]),
        str(trade["evaluation_date"]),
        str(trade["status_reason"]),
        f"{trade.get('evaluation_observed_state') or '-'} / {_format_number(trade.get('evaluation_actual_close'))} / {trade.get('evaluation_trough_confirm_date') or '-'}",
        f"{_ENTRY_SOURCE_LABELS.get(trade['entry_source'], trade['entry_source'])} / {trade.get('entry_pattern_start_date') or '-'} / {_format_number(trade.get('entry_reference_high_close'))}",
        f"{format_cost_peaks(trade.get('entry_cost_peak_points'))} / 区域 {_format_number(trade.get('entry_cost_zone_low'))}～{_format_number(trade.get('entry_cost_zone_high'))} / 历史峰值均值 {_format_number(trade.get('entry_breakout_base_close'))} / 冻结 {trade.get('entry_cost_zone_freeze_date') or '-'} / {_format_percent(trade.get('entry_breakout_gain_pct'))}",
        str(len(trade.get("skipped_entries", []))),
        f"MA{trade['trough_ma_days']} / {_format_number(trade.get('evaluation_trough_ma_close'))}",
        _format_number(trade.get("evaluation_consecutive_up_days"), digits=0),
        f"{trade.get('entry_industry_name') or '-'} {trade.get('entry_industry_code') or ''} / {trade.get('entry_industry_state_name') or '-'} / {_format_percent(trade.get('entry_industry_drawdown_pct'))} / {_format_percent(trade.get('entry_industry_rebound_pct'))}",
    ]


def _entry_rules(metadata: dict[str, Any]) -> list[str]:
    pullback = _format_percent(metadata["entry_rule"]["candidate_pullback_pct"])
    breakout = _format_percent(metadata["entry_rule"].get("min_breakout_gain_pct", DEFAULT_MIN_BREAKOUT_GAIN_PCT))
    entry_states = "、".join(metadata["entry_states"])
    return [
        f"参与做空的开仓状态：{entry_states}。未列入范围的波峰保留状态和事件，但不新开仓，也不计入持仓冲突跳过信号数。",
        *industry_rule_notes(metadata),
        f"候选回调：1 - 当日收盘价 / 本轮运行最高收盘价 至少{pullback}时触发，包含等于；不要求冲高、绿K或连续下跌天数。",
        cost_zone_rule_notes(metadata["entry_rule"].get("cost_peak_window_days", DEFAULT_COST_PEAK_WINDOW_DAYS)) + f"最低突破涨幅：{breakout}。",
        "候选阶段每天按跌幅输出状态：达到候选回调阈值为15，回升到阈值以内为10；每次从阈值以内重新达到阈值时保存新的候选回调信号，同一行情周期内可多次触发。连续停留在回调区间不逐日重复触发；收盘达到或超过候选高点时更新高点。每个实际 PEAK_n 序号只保存首次事件，是否开仓由上述范围决定。",
        "按开仓范围内的独立 entry_signal 在当日收盘开仓，不回填到高点日；同日即使状态转为41，开仓信号仍保留，开仓当天不正常平仓。",
        "每只股票同时只持有一笔；持仓期间或平仓当日的新开仓信号跳过，不延后补开。平仓后可接受同一行情周期内后续新的候选回调信号，无需等待新周期，也不要求先创新高。旧信号不重放，连续回调不因空仓自动重开。不同股票独立处理。",
    ]


def _normal_exit_rules(metadata: dict[str, Any]) -> list[str]:
    rule = metadata["normal_exit_rule"]
    states = " 或 ".join(rule["trough_states"])
    days = rule["trough_ma_days"]
    return [
        f"正常平仓：候选低点之后，首次收盘价严格高于 MA{days} 当天确认波谷；均线为含当日在内最近{days}个有效个股交易日收盘价的算术平均，需完整窗口，等于均线不确认。开仓日之后，当日实际状态为 {states} 且 trade_date 等于 trough_confirm_date，按确认日收盘价平仓。",
        "不设置低点后的最少等待天数或上涨天数条件；更低收盘价更新候选低点。确认后才将低点日至确认日回填为波谷区域，最低点是事后标记，不能按最低点日期或价格平仓。使用 observed_state 和 trough_confirm_date 判断当天实际确认，已确认区域的延续日或回到旧区域均不产生新的确认信号。波峰本身不触发正常平仓；全程保留回撤止损与失效强平。",
    ]


def _profit_rebound_exit_rules(metadata: dict[str, Any]) -> list[str]:
    rule = metadata.get("profit_rebound_exit_rule")
    if rule is None:
        return []
    threshold = _format_percent(rule["min_return_pct"])
    days = rule["consecutive_up_days"]
    return [
        f"独立盈利平仓：当日做空浮盈 1 - 当日收盘价 / 开仓价 严格大于{threshold}，且开仓后连续至少{days}个有效个股交易日 close > 前一天 close，按首次同时满足条件当天的收盘价平仓，结束原因记为 {rule['reason']}。恰好{threshold}不触发；只要求触发当天收益达标。",
        "连续上涨从开仓后的第一天与开仓日收盘价比较开始计数，开仓前的上涨不计入；下跌或平盘清零。此规则独立于波谷状态，即使未确认波谷也可平仓。",
        "同日优先级：回撤止损、周期切换/新高失效强平、独立盈利平仓、波谷确认平仓；独立盈利平仓计入已完成交易、收益和胜率，平仓当日不开新仓。",
    ]


def _drawdown_stop_rules(metadata: dict[str, Any]) -> list[str]:
    threshold = metadata.get("max_drawdown_stop_pct")
    if threshold is None:
        return []
    return [
        f"单笔回撤止损：做空权益（2 - 当日价格 / 开仓价）相对持仓期间峰值回撤达到{_format_percent(threshold)}时强制平仓，结束状态记为 MAX_DRAWDOWN_STOP。同日优先于状态退出或失效。",
        "成交假设：以每日收盘价判断首次触发日期，按该日回撤阈值对应的价格模拟成交；该价格可能不同于当日收盘价，未模拟跳空和无法成交时的超额亏损。",
        "止损计入已完成交易的收益和胜率，持有天数、MFE、MAE、最大回撤及平均市值只统计到止损日；止损后旧信号不重放，同周期后续新的候选回调信号仍可开仓。",
        f"回撤相对权益峰值计算，盈利后也可能触发；{_format_percent(threshold)}约束适用于单笔交易，多笔亏损累积的逐笔复利最大回撤仍可能超过该阈值。",
    ]


def _format_percent(value: Any) -> str:
    if value is None or pd.isna(value):
        return "-"
    percentage = float(value) * 100.0
    if abs(percentage) >= 1_000_000:
        return f"{percentage:.2e}%"
    return f"{percentage:.2f}%"


def _format_number(value: Any, *, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.{digits}f}"
