"""Reports for historical SW L2 stabilization-signal validation."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def build_industry_index_stabilization_report(
    signals: pd.DataFrame,
    *,
    as_of_date: str,
    level: str,
    trend_lookback: int,
    min_decline_days: int,
    min_decline_pct: float,
    min_stable_days: int,
    max_stable_range_points: float,
    forward_window: int,
    target_forward_return: float,
) -> dict[str, Any]:
    """Build a compact effectiveness report from historical signals."""
    status_counts = (
        signals["validation_status"]
        .value_counts()
        .reindex(["success", "failed", "pending"], fill_value=0)
        if not signals.empty
        else pd.Series({"success": 0, "failed": 0, "pending": 0})
    )
    successes = int(status_counts["success"])
    failures = int(status_counts["failed"])
    resolved = successes + failures
    return {
        "metadata": {
            "report_type": "sw_industry_index_stabilization_backtest",
            "as_of_date": as_of_date,
            "industry_level": str(level).strip().upper(),
            "trend_lookback": trend_lookback,
            "min_decline_days": min_decline_days,
            "min_decline_pct": min_decline_pct,
            "min_stable_days": min_stable_days,
            "max_stable_range_points": max_stable_range_points,
            "forward_window": forward_window,
            "target_forward_return": target_forward_return,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
        "summary": {
            "signals": int(len(signals)),
            "industries": int(signals["index_code"].nunique()) if not signals.empty else 0,
            "successes": successes,
            "failures": failures,
            "pending": int(status_counts["pending"]),
            "resolved_signals": resolved,
            "success_rate": successes / resolved if resolved else None,
            "median_max_forward_return": _optional_float(
                signals["max_forward_return"].median()
            )
            if not signals.empty
            else None,
        },
        "signals": _records(signals),
    }


def render_industry_index_stabilization_markdown(report: dict[str, Any]) -> str:
    metadata = report["metadata"]
    summary = report["summary"]
    level = metadata["industry_level"]
    lines = [
        f"# 申万{level}行业指数企稳反转历史验证 - {metadata['as_of_date']}",
        "",
        "## 规则",
        "",
        f"- 下跌时间阈值：{metadata['min_decline_days']}个交易日；或跌幅达到：{metadata['min_decline_pct']:.1%}",
        f"- 企稳要求：连续至少{metadata['min_stable_days']}个交易日，期间全部开盘价和收盘价落在{metadata['max_stable_range_points']:g}点价格带内",
        f"- 成功标准：信号后{metadata['forward_window']}个交易日内，收盘价较信号日收盘价上涨至少{metadata['target_forward_return']:.1%}",
        "",
        "## 验证结果",
        "",
        f"- 历史信号：{summary['signals']}",
        f"- 覆盖行业：{summary['industries']}",
        f"- 成功：{summary['successes']}",
        f"- 失败：{summary['failures']}",
        f"- 待验证：{summary['pending']}",
        f"- 已完成样本成功率：{_format_percent(summary['success_rate'])}",
        f"- 最大后续涨幅中位数：{_format_percent(summary['median_max_forward_return'])}",
        "",
        "## 信号明细",
        "",
        "| 信号日 | 行业 | 下跌天数 | 最大跌幅 | 企稳区间 | 后续最大涨幅 | 状态 | 达标日 |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    if not report["signals"]:
        lines.append("| - | - | - | - | - | - | - | - |")
    for row in report["signals"]:
        lines.append(
            f"| {row['signal_date']} | {row['index_code']} {row.get('name') or ''} | "
            f"{row['decline_days']} | {_format_percent(row['decline_pct'])} | "
            f"{row['stable_range_points']:.2f}点 | "
            f"{_format_percent(row.get('max_forward_return'))} | "
            f"{row['validation_status']} | {row.get('target_hit_date') or '-'} |"
        )
    return "\n".join(lines) + "\n"


def save_industry_index_stabilization_report(
    report: dict[str, Any],
    signals: pd.DataFrame,
    *,
    records_root: str | Path = "analysis_records",
) -> tuple[Path, Path, Path]:
    metadata = report["metadata"]
    date_text = str(metadata["as_of_date"])
    level = str(metadata["industry_level"]).upper()
    directory = Path(records_root) / "industry_index_stabilization" / (
        f"{date_text[:4]}-{date_text[4:6]}"
    )
    directory.mkdir(parents=True, exist_ok=True)
    stem = (
        f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:8]}-"
        f"{level}-stabilization-backtest"
    )
    csv_path = directory / f"{stem}.csv"
    json_path = directory / f"{stem}.json"
    markdown_path = directory / f"{stem}.md"
    signals.to_csv(csv_path, index=False, encoding="utf-8-sig")
    json_path.write_text(
        json.dumps(_json_safe(report), ensure_ascii=False, indent=2, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_industry_index_stabilization_markdown(report), encoding="utf-8"
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


def _optional_float(value: Any) -> float | None:
    return None if value is None or pd.isna(value) else float(value)


def _format_percent(value: Any) -> str:
    return "-" if value is None or pd.isna(value) else f"{float(value):.2%}"
