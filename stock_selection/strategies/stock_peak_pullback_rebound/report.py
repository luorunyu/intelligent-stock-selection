"""Reports for historical stock peak-pullback-rebound events."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


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
