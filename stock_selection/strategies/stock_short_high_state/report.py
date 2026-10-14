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
    latest_stock_short_states,
)


DEFAULT_OUTPUT_ROOT = Path(__file__).resolve().parent / "output"

_TRADE_STATUS_LABELS = {
    "COMPLETED": "已完成",
    "OPEN": "持仓中",
    "INVALIDATED": "已失效",
}
_ENTRY_SOURCE_LABELS = {
    "OBSERVED_HIGH_CANDIDATE": "实时候选",
    "RECONSTRUCTED_STATE_START": "候选起点重建",
    "OBSERVED_PEAK_START": "波峰开始",
}


def build_stock_short_state_report(
    states: pd.DataFrame,
    *,
    as_of_date: str,
    lookback_days: int,
    confirm_drawdown_pct: float,
    rebound_pct: float,
    high_heat_days: int,
    trough_heat_days: int,
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
            "confirm_drawdown_pct": confirm_drawdown_pct,
            "rebound_pct": rebound_pct,
            "high_heat_days": high_heat_days,
            "trough_heat_days": trough_heat_days,
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
        f"- 最高点确认回落：{metadata['confirm_drawdown_pct']:.1%}",
        f"- 波峰开启反弹：{metadata['rebound_pct']:.1%}",
        f"- 最高点热度窗口：{metadata['high_heat_days']}个交易日",
        f"- 波谷热度窗口：{metadata['trough_heat_days']}个交易日",
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


def save_stock_short_state_report(
    report: dict[str, Any],
    states: pd.DataFrame,
    *,
    records_root: str | Path = DEFAULT_OUTPUT_ROOT,
) -> tuple[Path, Path, Path]:
    date_text = str(report["metadata"]["as_of_date"])
    directory = Path(records_root) / (
        f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:8]}"
    )
    directory.mkdir(parents=True, exist_ok=True)
    csv_path = directory / "states.csv"
    json_path = directory / "report.json"
    markdown_path = directory / "report.md"
    states.to_csv(csv_path, index=False, encoding="utf-8-sig")
    json_path.write_text(
        json.dumps(_json_safe(report), ensure_ascii=False, indent=2, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_stock_short_state_markdown(report), encoding="utf-8"
    )
    return csv_path, json_path, markdown_path


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
        "- 做空开仓：首次进入 `HIGH_CANDIDATE`，或首次进入某个 `PEAK_n`。连续状态不重复开仓。",
        "- 做空结束：`HIGH_CANDIDATE` 对应首次进入 `TROUGH_WAIT_REBOUND`；`PEAK_n` 对应首次进入同序号的 `PEAK_n_PULLBACK`。",
        "- 已确认周期中的 `HIGH_CANDIDATE` 已被状态算法回填为 `HIGH_CONFIRMED`，报告使用 `state_start_date/state_start_close` 重建当时实际可见的候选开仓点，不把 `HIGH_CONFIRMED` 当作事后开仓信号。",
        "- 新高失效或周期异常切换且未到结束状态的交易标记为失效；截至报告日未到结束状态的交易标记为持仓中。两者均不计入已完成交易的收益和胜率。",
        "- 收益率按 `开仓价 / 平仓价 - 1` 计算。未计手续费、滑点、融券成本、涨跌停限制和实际可融券约束。",
        "- `PEAK_n_PULLBACK` 本身定义为收盘价回到或跌破波峰开仓价，所以正常结束的波峰交易按构造不会产生负收益；波峰胜率是有条件完成样本的统计，必须同时看持仓中、失效数量和回撤，不能单独解释为预测能力。",
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
            f"- 信号总数：{summary['signals']}",
            f"- 已完成：{summary['completed_trades']}",
            f"- 持仓中：{summary['open_trades']}",
            f"- 已失效：{summary['invalidated_trades']}",
            f"- 胜 / 负 / 平：{summary['wins']} / {summary['losses']} / {summary['breakeven']}",
            f"- 胜率：{_format_percent(summary['win_rate'])}",
            f"- 平均 / 中位收益：{_format_percent(summary['average_return_pct'])} / {_format_percent(summary['median_return_pct'])}",
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
            "| 开仓状态 | 信号 | 完成 | 持仓中 | 失效 | 胜率 | 平均收益 | 中位收益 | 最差收益 | 平均持有日 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    if not backtest["by_entry_state"]:
        lines.append("| - | 0 | 0 | 0 | 0 | - | - | - | - | - |")
    for row in backtest["by_entry_state"]:
        lines.append(
            f"| {row['entry_state']} | {row['signals']} | "
            f"{row['completed_trades']} | {row['open_trades']} | "
            f"{row['invalidated_trades']} | {_format_percent(row['win_rate'])} | "
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
        ("信号总数", summary["signals"]),
        ("已完成", summary["completed_trades"]),
        ("持仓中", summary["open_trades"]),
        ("已失效", summary["invalidated_trades"]),
        ("胜 / 负 / 平", f"{summary['wins']} / {summary['losses']} / {summary['breakeven']}"),
        ("胜率", _format_percent(summary["win_rate"])),
        ("平均 / 中位收益", f"{_format_percent(summary['average_return_pct'])} / {_format_percent(summary['median_return_pct'])}"),
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
            row["invalidated_trades"],
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
@media print{{body{{padding:8mm}}th{{position:static}}}}
</style>
</head>
<body>
<main>
<h1>{escape(title)}</h1>
<h2>交易规则</h2>
<ul class="rules">
<li>做空开仓：首次进入 <code>HIGH_CANDIDATE</code>，或首次进入某个 <code>PEAK_n</code>。连续状态不重复开仓。</li>
<li>做空结束：<code>HIGH_CANDIDATE</code> 对应首次进入 <code>TROUGH_WAIT_REBOUND</code>；<code>PEAK_n</code> 对应首次进入同序号的 <code>PEAK_n_PULLBACK</code>。</li>
<li>已确认周期使用 <code>state_start_date/state_start_close</code> 重建当时可见的候选开仓点，不把事后回填的 <code>HIGH_CONFIRMED</code> 当作开仓信号。</li>
<li>失效和持仓中交易不计入已完成交易的收益和胜率。</li>
<li>收益率按“开仓价 / 平仓价 - 1”计算，未计手续费、滑点、融券成本、涨跌停限制和实际可融券约束。</li>
<li><code>PEAK_n_PULLBACK</code> 的完成样本存在条件筛选，需同时看持仓中、失效数量和回撤。</li>
<li>逐笔复利按平仓日期连接已完成交易，不是考虑重叠持仓、仓位和资金占用的真实组合净值。</li>
</ul>
{notes_html}
<h2>总体统计</h2>
<div class="metrics">{summary_html}</div>
<h2>按开仓状态统计</h2>
<table class="state-table">
<thead><tr><th>开仓状态</th><th>信号</th><th>完成</th><th>持仓中</th><th>失效</th><th>胜率</th><th>平均收益</th><th>中位收益</th><th>最差收益</th><th>平均持有日</th></tr></thead>
<tbody>{''.join(state_rows)}</tbody>
</table>
<h2>逐笔交易</h2>
<table class="trade-table">
<thead><tr><th>股票</th><th>周期</th><th>开仓状态</th><th>信号来源</th><th>开仓日</th><th>开仓价</th><th>平均总市值</th><th>市值样本日</th><th>开仓总市值</th><th>开仓流通市值</th><th>结束状态</th><th>结束/估值日</th><th>结束/估值价</th><th>状态</th><th>已实现收益</th><th>浮动收益</th><th>MFE</th><th>MAE</th><th>最大回撤</th><th>持有日</th></tr></thead>
<tbody>{''.join(trade_rows)}</tbody>
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
) -> Path:
    """Save the standalone state-transition short backtest report."""
    date_text = str(backtest["metadata"]["as_of_date"])
    directory = Path(records_root) / (
        f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:8]}"
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


def _format_percent(value: Any) -> str:
    if value is None or pd.isna(value):
        return "-"
    percentage = float(value) * 100.0
    if abs(percentage) >= 1_000_000:
        return f"{percentage:.2e}%"
    return f"{percentage:.2f}%"


def _format_number(value: Any) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.2f}"
