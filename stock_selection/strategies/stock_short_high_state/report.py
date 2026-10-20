"""Reports for stock short-side high-region states."""

from __future__ import annotations

from datetime import datetime
from html import escape
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from stock_selection.data.market_cap import format_market_cap
from stock_selection.strategies.stock_short_high_state.screen import (
    DEFAULT_CANDIDATE_PULLBACK_PCT,
    latest_stock_short_states,
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
    confirm_drawdown_pct: float,
    rebound_pct: float,
    high_heat_days: int,
    trough_confirm_days: int,
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
            "as_of_date": as_of_date,
            "lookback_days": lookback_days,
            "high_candidate_formula": "close >= max(previous lookback_days closes)",
            "candidate_pullback_rule": "DRAWDOWN_FROM_RUNNING_HIGH_CLOSE",
            "candidate_pullback_pct": candidate_pullback_pct,
            "confirm_drawdown_pct": confirm_drawdown_pct,
            "rebound_pct": rebound_pct,
            "high_heat_days": high_heat_days,
            "trough_confirm_days": trough_confirm_days,
            "trough_min_up_days": max(1, trough_confirm_days - 2),
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
        f"- 半年窗口：{metadata['lookback_days']}个交易日",
        f"- 高点候选条件：当日收盘价达到或超过前{metadata['lookback_days']}个交易日最高收盘价（不含当日，包含等于）",
        f"- 候选回调：收盘价从运行最高收盘价回落至少{metadata['candidate_pullback_pct']:.1%}，不要求K线颜色或连续下跌天数",
        f"- 最高点确认回落：{metadata['confirm_drawdown_pct']:.1%}",
        f"- 波峰开启反弹：{metadata['rebound_pct']:.1%}",
        f"- 最高点热度窗口：{metadata['high_heat_days']}个交易日",
        f"- 波谷确认窗口：{metadata['trough_confirm_days']}个交易日",
        f"- 波谷企稳：低点后至少观察{metadata['trough_confirm_days'] - 1}个交易日，期间不创新低，最近观察窗口至少{metadata['trough_min_up_days']}天 close > 前一天 close；平盘不算上涨，不满足则保持候选并滚动观察",
        "",
        "## 汇总",
        "",
        f"- 股票数量：{summary['stocks']}",
        f"- 状态记录：{summary['state_rows']}",
        f"- 再创新高告警：{summary['alerts']}",
        "",
        "## 最新状态",
        "",
        "| 股票 | 日期 | 收盘价 | 总市值 | 流通市值 | 枚举值 | 状态 | 波峰序号 | 热度 |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: |",
    ]
    if not report["latest_states"]:
        lines.append("| - | - | - | - | - | - | - | - | - |")
    for row in report["latest_states"]:
        heat = "-" if row.get("heat") is None else f"{row['heat']:.2%}"
        lines.append(
            f"| {row['ts_code']} {row.get('stock_name') or ''} | "
            f"{row['trade_date']} | {row['close']:.2f} | "
            f"{format_market_cap(row.get('total_mv'))} | "
            f"{format_market_cap(row.get('circ_mv'))} | {row['state']} | "
            f"{row['state_description']} | {row['peak_number']} | {heat} |"
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
                "- 这是按报告日融券快照构成的固定股票池，只回答这些股票上的历史状态收益；它不能证明历史开仓日当时有券可借，也没有计入可借数量变化、融券费率和强制归还风险。",
            ]
        )
    lines.extend(
        [
            "",
            "## 总体统计",
            "",
            f"- 实际开仓数：{summary['signals']}",
            f"- 因已有持仓跳过的开仓信号：{summary['skipped_while_holding']}",
            f"- 因平仓当日跳过的开仓信号：{summary['skipped_on_exit_day']}",
            f"- 已完成：{summary['completed_trades']}",
            f"- 其中回撤止损平仓：{summary.get('drawdown_stop_trades', 0)}",
            f"- 其中盈利后连续上涨平仓：{summary.get('profit_rebound_exit_trades', 0)}",
            f"- 持仓中：{summary['open_trades']}",
            f"- 其中新高失效平仓：{summary['invalidation_exit_trades']}",
            f"- 其中周期异常切换平仓：{summary['cycle_change_exit_trades']}",
            f"- 其中第一波峰后取消参考价过滤的波谷平仓：{summary['trough_exits_after_first_peak']}",
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
        "", "## 平仓参考核对", "",
        "每笔列出结束/估值日实际可知的参考值；止损时偏离使用触发日实际收盘价，不使用模拟成交价。参考状态 AVAILABLE 表示可用，NO_VOLUME_EXPANSION 表示未找到放量，INSUFFICIENT_LOOKBACK 表示历史不足，NO_REFERENCE_HIGH 表示缺少有效参考高点。", "",
        "| " + " | ".join(_REFERENCE_AUDIT_HEADERS) + " |",
        "| " + " | ".join("---" for _ in _REFERENCE_AUDIT_HEADERS) + " |",
    ])
    for trade in backtest["trades"]:
        lines.append("| " + " | ".join(_reference_audit_values(trade)) + " |")
    if not backtest["trades"]:
        lines.append("| " + " | ".join("-" for _ in _REFERENCE_AUDIT_HEADERS) + " |")
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
                "这是按报告日融券快照构成的固定股票池，只回答这些股票上的历史状态收益；它不能证明历史开仓日当时有券可借，也没有计入可借数量变化、融券费率和强制归还风险。",
            ]
        )

    summary_items = [
        ("实际开仓数", summary["signals"]),
        ("持仓期间跳过信号", summary["skipped_while_holding"]),
        ("平仓当日跳过信号", summary["skipped_on_exit_day"]),
        ("已完成", summary["completed_trades"]),
        ("其中回撤止损", summary.get("drawdown_stop_trades", 0)),
        ("盈利后连续上涨平仓", summary.get("profit_rebound_exit_trades", 0)),
        ("持仓中", summary["open_trades"]),
        ("其中新高失效平仓", summary["invalidation_exit_trades"]),
        ("其中周期切换平仓", summary["cycle_change_exit_trades"]),
        ("第一波峰后波谷平仓", summary["trough_exits_after_first_peak"]),
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
    audit_headers_html = "".join(f"<th>{escape(value)}</th>" for value in _REFERENCE_AUDIT_HEADERS)
    audit_rows_html = "".join(
        "<tr>" + "".join(f"<td>{escape(value)}</td>" for value in _reference_audit_values(trade)) + "</tr>"
        for trade in backtest["trades"]
    ) or f'<tr><td colspan="{len(_REFERENCE_AUDIT_HEADERS)}">没有逐笔交易</td></tr>'
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
<h2>平仓参考核对</h2>
<p class="note">每笔列出结束/估值日实际可知的参考值；止损时偏离使用触发日实际收盘价。AVAILABLE：可用；NO_VOLUME_EXPANSION：无符合条件的放量；INSUFFICIENT_LOOKBACK：历史不足；NO_REFERENCE_HIGH：缺少有效参考高点。</p>
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


_REFERENCE_AUDIT_HEADERS = (
    "股票", "周期/波峰", "开仓日", "结束/估值日", "结束原因",
    "当日实际状态 / 收盘 / 波谷确认日", "参考可用性", "参考高点 / 回看天数",
    "窗口低点 / 收盘", "放量三日", "三日成交量", "第三/第一量比",
    "价格起点日期 / 参考价", "绝对偏离",
    "开仓信号 / 参考起始日 / 判定高点", "参考价过滤 / 取消日", "跳过信号数",
    "结束日波谷观察上涨天数 / 最少",
    "结束日开仓后连续收盘上涨天数",
)


def _reference_audit_values(trade: dict[str, Any]) -> list[str]:
    return [
        f"{trade['ts_code']} {trade.get('stock_name') or ''}",
        f"{trade['cycle_id']} / {trade['peak_number']}",
        str(trade["entry_date"]),
        str(trade["evaluation_date"]),
        str(trade["status_reason"]),
        f"{trade.get('evaluation_observed_state') or '-'} / {_format_number(trade.get('evaluation_actual_close'))} / {trade.get('evaluation_trough_confirm_date') or '-'}",
        str(trade.get("price_origin_status") or "-"),
        f"{trade.get('origin_high_date') or '-'} / {_format_number(trade.get('origin_high_close'))} / {trade.get('origin_lookback_days') or '-'}日",
        f"{trade.get('origin_window_low_date') or '-'} / {_format_number(trade.get('origin_window_low_close'))}",
        " / ".join(str(trade.get(key) or "-") for key in ("volume_start_date", "volume_middle_date", "volume_end_date")),
        " / ".join(_format_number(trade.get(key)) for key in ("volume_first", "volume_second", "volume_third")),
        _format_number(trade.get("volume_expansion_ratio")),
        f"{trade.get('price_origin_date') or '-'} / {_format_number(trade.get('price_origin_close'))}",
        _format_percent(trade.get("price_origin_gap_pct")),
        f"{_ENTRY_SOURCE_LABELS.get(trade['entry_source'], trade['entry_source'])} / {trade.get('entry_pattern_start_date') or '-'} / {_format_number(trade.get('entry_reference_high_close'))}",
        f"{'仍启用' if trade.get('price_origin_filter_required', True) else '已取消'} / {trade.get('price_origin_filter_disabled_date') or '-'}",
        str(len(trade.get("skipped_entries", []))),
        f"{_format_number(trade.get('evaluation_trough_up_days'), digits=0)} / {trade.get('trough_min_up_days', '-')}",
        _format_number(trade.get("evaluation_consecutive_up_days"), digits=0),
    ]


def _entry_rules(metadata: dict[str, Any]) -> list[str]:
    pullback = _format_percent(metadata["entry_rule"]["candidate_pullback_pct"])
    entry_states = "、".join(metadata["entry_states"])
    return [
        f"参与做空的开仓状态：{entry_states}。未列入范围的波峰保留状态和事件，但不新开仓，也不计入持仓冲突跳过信号数。",
        f"候选回调：1 - 当日收盘价 / 本轮运行最高收盘价 至少{pullback}时触发，包含等于；不要求冲高、绿K或连续下跌天数。",
        "候选阶段每天按跌幅输出状态：达到候选回调阈值为15，回升到阈值以内为10；每次从阈值以内重新达到阈值时保存新的候选回调信号，同一行情周期内可多次触发。连续停留在回调区间不逐日重复触发；收盘达到或超过候选高点时更新高点。每个实际 PEAK_n 序号只保存首次事件，是否开仓由上述范围决定。",
        "按开仓范围内的独立 entry_signal 在当日收盘开仓，不回填到高点日；同日即使状态转为41，开仓信号仍保留，开仓当天不正常平仓。",
        "每只股票同时只持有一笔；持仓期间或平仓当日的新开仓信号跳过，不延后补开。平仓后可接受同一行情周期内后续新的候选回调信号，无需等待新周期，也不要求先创新高。旧信号不重放，连续回调不因空仓自动重开。不同股票独立处理。",
    ]


def _normal_exit_rules(metadata: dict[str, Any]) -> list[str]:
    rule = metadata["normal_exit_rule"]
    states = " 或 ".join(rule["trough_states"])
    multiple = f"{rule['volume_expansion_multiple']:g}"
    gap = _format_percent(rule["max_price_origin_gap_pct"])
    days = rule["trough_confirm_days"]
    min_up_days = rule["trough_min_up_days"]
    return [
        f"正常平仓：开仓日之后，当日实际状态为 {states} 且 trade_date 等于 trough_confirm_date，按确认日收盘价平仓。低点后至少观察{days - 1}个有效个股交易日、期间不创新低，最近{days - 1}天至少{min_up_days}天收盘价高于前一天；平盘不算上涨。最早含低点第{days}天确认，不满足则保持候选、滚动观察，新低重新计时。TROUGH_CANDIDATE 不正常平仓。",
        f"本周期第一波峰实际出现前，正常平仓还要求 abs(确认日收盘价 / 行情价格起点 - 1) 不超过{gap}（包含等于）；确认日价格条件不满足则继续持仓，等待后续新的波谷确认日或强制平仓。",
        f"价格起点：以当日已知候选高点为终点，向前取 lookback_days 个个股交易日（不含高点日）的最低收盘价，重复最低取最后一次；在该低点至高点之间寻找连续三天 0 < V1 < V2 < V3 且 V3/V1 至少{multiple}倍的放量，取最后一组第三天收盘价作为参考。三天必须全部位于该区间。",
        "候选高点更新时重算参考，高点确认后本周期固定；使用 observed_state 和 trough_confirm_date 判断当天实际确认，历史回填的低点日、已确认区域的延续日或反弹后回到旧区域均不作为新的确认日。",
        f"第一波峰前绝对偏离超过{gap}，或缺少参考时继续持仓，低于参考价超过该比例也不正常平仓。实际出现 PEAK_1 后，本周期永久取消价格过滤，后续新的波谷确认日可直接平仓，即使参考缺失。波峰本身不触发正常平仓，新周期重新启用过滤；全程保留回撤止损与失效强平。",
    ]


def _profit_rebound_exit_rules(metadata: dict[str, Any]) -> list[str]:
    rule = metadata.get("profit_rebound_exit_rule")
    if rule is None:
        return []
    threshold = _format_percent(rule["min_return_pct"])
    days = rule["consecutive_up_days"]
    return [
        f"独立盈利平仓：当日做空浮盈 1 - 当日收盘价 / 开仓价 严格大于{threshold}，且开仓后连续至少{days}个有效个股交易日 close > 前一天 close，按首次同时满足条件当天的收盘价平仓，结束原因记为 {rule['reason']}。恰好{threshold}不触发；只要求触发当天收益达标。",
        "连续上涨从开仓后的第一天与开仓日收盘价比较开始计数，开仓前的上涨不计入；下跌或平盘清零。此规则独立于波谷状态和放量参考价，即使未确认波谷、缺少参考价或价格偏离超过上限也可平仓。",
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
