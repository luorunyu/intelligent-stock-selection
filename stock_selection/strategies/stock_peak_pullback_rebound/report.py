"""Reports for historical stock peak-pullback-rebound events."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from stock_selection.data.market_cap import format_market_cap
from stock_selection.strategies.stock_peak_pullback_rebound.screen import (
    latest_peak_pullback_rebound_states,
)


DEFAULT_OUTPUT_ROOT = Path(__file__).resolve().parent / "output"


def build_peak_pullback_rebound_state_report(
    states: pd.DataFrame,
    *,
    as_of_date: str,
    peak_lookback_days: int,
    max_rebound_days: int,
    min_pullback_pct: float,
    min_second_peak_ratio: float,
    rebound_end_drawdown_pct: float,
    rebound_ended_state_days: int,
) -> dict[str, Any]:
    latest = latest_peak_pullback_rebound_states(states)
    alerts = (
        states[states["alert_code"].ne("")]
        if states is not None and not states.empty
        else pd.DataFrame(columns=states.columns if states is not None else [])
    )
    active_latest = latest[latest["state"].notna()] if not latest.empty else latest
    state_counts = (
        active_latest["state_description"].value_counts().sort_index().to_dict()
        if not active_latest.empty
        else {}
    )
    return {
        "metadata": {
            "report_type": "stock_peak_pullback_rebound_state",
            "as_of_date": as_of_date,
            "peak_lookback_days": peak_lookback_days,
            "max_rebound_days": max_rebound_days,
            "min_pullback_pct": min_pullback_pct,
            "min_second_peak_ratio": min_second_peak_ratio,
            "rebound_end_drawdown_pct": rebound_end_drawdown_pct,
            "rebound_ended_state_days": rebound_ended_state_days,
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


def render_peak_pullback_rebound_state_markdown(
    report: dict[str, Any],
) -> str:
    metadata = report["metadata"]
    summary = report["summary"]
    lines = [
        f"# 策略四：高点回落反弹状态 - {metadata['as_of_date']}",
        "",
        "## 默认规则",
        "",
        f"- 高点回看窗口：{metadata['peak_lookback_days']}个交易日",
        f"- 有效回落：{metadata['min_pullback_pct']:.1%}",
        f"- 反弹开启位置：前高的{metadata['min_second_peak_ratio']:.1%}",
        f"- 反弹结束回落：{metadata['rebound_end_drawdown_pct']:.1%}",
        f"- 结束状态保留：{metadata['rebound_ended_state_days']}个交易日",
        "",
        "## 汇总",
        "",
        f"- 股票数量：{summary['stocks']}",
        f"- 逐日状态记录：{summary['state_rows']}",
        f"- 突破前高告警：{summary['alerts']}",
        "",
        "## 最新状态",
        "",
        "| 股票 | 日期 | 收盘价 | 总市值 | 流通市值 | 枚举值 | 状态 | 告警 |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    if not report["latest_states"]:
        lines.append("| - | - | - | - | - | - | - | - |")
    for row in report["latest_states"]:
        state = "-" if row.get("state") is None else str(row["state"])
        description = row.get("state_description") or "-"
        alert = row.get("alert_message") or "-"
        lines.append(
            f"| {row['ts_code']} {row.get('stock_name') or ''} | "
            f"{row['trade_date']} | {row['close']:.2f} | "
            f"{format_market_cap(row.get('total_mv'))} | "
            f"{format_market_cap(row.get('circ_mv'))} | {state} | "
            f"{description} | {alert} |"
        )
    return "\n".join(lines) + "\n"


def save_peak_pullback_rebound_state_report(
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
        render_peak_pullback_rebound_state_markdown(report),
        encoding="utf-8",
    )
    return csv_path, json_path, markdown_path


def build_peak_pullback_rebound_report(
    events: pd.DataFrame,
    *,
    as_of_date: str,
    peak_lookback_days: int,
    peak_lookforward_days: int,
    max_rebound_days: int,
    min_pullback_pct: float,
    min_second_peak_ratio: float,
) -> dict[str, Any]:
    return {
        "metadata": {
            "report_type": "stock_peak_pullback_rebound",
            "as_of_date": as_of_date,
            "peak_lookback_days": peak_lookback_days,
            "peak_lookforward_days": peak_lookforward_days,
            "max_rebound_days": max_rebound_days,
            "min_pullback_pct": min_pullback_pct,
            "min_second_peak_ratio": min_second_peak_ratio,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
        "summary": {
            "events": int(len(events)),
            "stocks": int(events["ts_code"].nunique()) if not events.empty else 0,
        },
        "events": _records(events),
    }


def render_peak_pullback_rebound_markdown(report: dict[str, Any]) -> str:
    metadata = report["metadata"]
    summary = report["summary"]
    lines = [
        f"# 个股高点回落反弹形态 - {metadata['as_of_date']}",
        "",
        "## 规则",
        "",
        f"- 高点确认：前后各{metadata['peak_lookback_days']}个交易日内没有更高收盘价",
        f"- 回落条件：高点后最多{metadata['max_rebound_days']}个交易日内至少回落{metadata['min_pullback_pct']:.1%}",
        f"- 反弹条件：回落低点后，收盘价达到前高的{metadata['min_second_peak_ratio']:.1%}",
        "",
        "## 汇总",
        "",
        f"- 形态数量：{summary['events']}",
        f"- 股票数量：{summary['stocks']}",
        "",
        "## 形态明细",
        "",
        "| 股票 | 最高点 | 回落低点 | 回落幅度 | 次高点 | 次高点/前高 | 间隔 |",
        "| --- | --- | --- | ---: | --- | ---: | ---: |",
    ]
    if not report["events"]:
        lines.append("| - | - | - | - | - | - | - |")
    for row in report["events"]:
        lines.append(
            f"| {row['ts_code']} {row.get('stock_name') or ''} | "
            f"{row['peak_date']} / {row['peak_close']:.2f} | "
            f"{row['pullback_low_date']} / {row['pullback_low_close']:.2f} | "
            f"{row['pullback_pct']:.2%} | "
            f"{row['second_peak_date']} / {row['second_peak_close']:.2f} | "
            f"{row['second_peak_ratio']:.2%} | "
            f"{row['peak_to_second_peak_days']}天 |"
        )
    return "\n".join(lines) + "\n"


def save_peak_pullback_rebound_report(
    report: dict[str, Any],
    events: pd.DataFrame,
    *,
    records_root: str | Path = "analysis_records",
) -> tuple[Path, Path, Path]:
    metadata = report["metadata"]
    date_text = str(metadata["as_of_date"])
    directory = Path(records_root) / "stock_peak_pullback_rebound" / (
        f"{date_text[:4]}-{date_text[4:6]}"
    )
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:8]}-peak-pullback-rebound"
    csv_path = directory / f"{stem}.csv"
    json_path = directory / f"{stem}.json"
    markdown_path = directory / f"{stem}.md"
    events.to_csv(csv_path, index=False, encoding="utf-8-sig")
    json_path.write_text(
        json.dumps(_json_safe(report), ensure_ascii=False, indent=2, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_peak_pullback_rebound_markdown(report), encoding="utf-8"
    )
    return csv_path, json_path, markdown_path


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
