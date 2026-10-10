"""Reports for stock short-side high-region states."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from stock_selection.strategies.stock_short_high_state.screen import (
    latest_stock_short_states,
)


DEFAULT_OUTPUT_ROOT = Path(__file__).resolve().parent / "output"


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
        "| 股票 | 日期 | 收盘价 | 枚举值 | 状态 | 波峰序号 | 热度 |",
        "| --- | --- | ---: | ---: | --- | ---: | ---: |",
    ]
    if not report["latest_states"]:
        lines.append("| - | - | - | - | - | - | - |")
    for row in report["latest_states"]:
        heat = "-" if row.get("heat") is None else f"{row['heat']:.2%}"
        lines.append(
            f"| {row['ts_code']} {row.get('stock_name') or ''} | "
            f"{row['trade_date']} | {row['close']:.2f} | {row['state']} | "
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
