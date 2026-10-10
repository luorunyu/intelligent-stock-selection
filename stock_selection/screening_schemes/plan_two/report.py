"""Reports for screening plan two."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def build_plan_two_report(
    eligible_lending: pd.DataFrame,
    latest_states: pd.DataFrame,
    result: pd.DataFrame,
    *,
    as_of_date: str,
    lending_file: str,
    min_available_quantity: int,
    required_lending_status: str,
    lookback_days: int,
    confirm_drawdown_pct: float,
    rebound_pct: float,
    high_heat_days: int,
    trough_heat_days: int,
    include_st: bool,
    allow_stale_market_data: bool,
) -> dict[str, Any]:
    classification_counts = (
        result["classification_description"].value_counts().to_dict()
        if not result.empty
        else {}
    )
    match_counts = (
        result["instrument_match_status"].value_counts().to_dict()
        if not result.empty
        else {}
    )
    market_data_date = (
        str(result["market_data_date"].iloc[0]) if not result.empty else ""
    )
    return {
        "metadata": {
            "report_type": "screening_plan_two",
            "as_of_date": as_of_date,
            "lending_file": lending_file,
            "market_data_date": market_data_date,
            "min_available_quantity": min_available_quantity,
            "required_lending_status": required_lending_status,
            "lookback_days": lookback_days,
            "confirm_drawdown_pct": confirm_drawdown_pct,
            "rebound_pct": rebound_pct,
            "high_heat_days": high_heat_days,
            "trough_heat_days": trough_heat_days,
            "include_st": include_st,
            "allow_stale_market_data": allow_stale_market_data,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
        "summary": {
            "eligible_lending": int(len(eligible_lending)),
            "strategy_states": int(len(latest_states)),
            "result_rows": int(len(result)),
            "classification_counts": {
                str(key): int(value) for key, value in classification_counts.items()
            },
            "instrument_match_counts": {
                str(key): int(value) for key, value in match_counts.items()
            },
            "market_data_stale": bool(
                result["market_data_stale"].any() if not result.empty else False
            ),
        },
        "short_scope": _records(
            result[result["classification_name"].eq("SHORT_SCOPE")]
            if not result.empty
            else result
        ),
        "alerts": _records(
            result[result["classification_name"].eq("ALERT")]
            if not result.empty
            else result
        ),
        "results": _records(result),
    }


def render_plan_two_markdown(report: dict[str, Any]) -> str:
    metadata = report["metadata"]
    summary = report["summary"]
    lines = [
        f"# 筛选方案二：可融券股票状态分类 - {metadata['as_of_date']}",
        "",
        "## 数据",
        "",
        f"- 融券文件：{metadata['lending_file']}",
        f"- 股票行情日期：{metadata['market_data_date'] or '-'}",
        f"- 可融券记录：{summary['eligible_lending']}",
        f"- 行情是否过期：{'是' if summary['market_data_stale'] else '否'}",
        "",
        "## 分类汇总",
        "",
    ]
    if summary["classification_counts"]:
        for name, count in summary["classification_counts"].items():
            lines.append(f"- {name}：{count}")
    else:
        lines.append("- 无结果")
    lines.extend(
        [
            "",
            "## 做空范围",
            "",
            "| 股票 | 可用数量 | 状态 | 波峰序号 | 热度 |",
            "| --- | ---: | --- | ---: | ---: |",
        ]
    )
    if not report["short_scope"]:
        lines.append("| - | - | - | - | - |")
    for row in report["short_scope"]:
        heat = "-" if row.get("heat") is None else f"{row['heat']:.2%}"
        lines.append(
            f"| {row['ts_code']} {row['security_name']} | "
            f"{row['available_quantity']} | {row['state_description']} | "
            f"{row['peak_number']} | {heat} |"
        )
    return "\n".join(lines) + "\n"


def save_plan_two_report(
    report: dict[str, Any],
    result: pd.DataFrame,
    *,
    records_root: str | Path = "analysis_records",
) -> tuple[Path, Path, Path]:
    date_text = str(report["metadata"]["as_of_date"])
    directory = Path(records_root) / "screening_plan_two" / (
        f"{date_text[:4]}-{date_text[4:6]}"
    )
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:8]}-plan-two"
    csv_path = directory / f"{stem}.csv"
    json_path = directory / f"{stem}.json"
    markdown_path = directory / f"{stem}.md"
    result.to_csv(csv_path, index=False, encoding="utf-8-sig")
    json_path.write_text(
        json.dumps(_json_safe(report), ensure_ascii=False, indent=2, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_plan_two_markdown(report), encoding="utf-8")
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
    if isinstance(value, (np.floating, float)):
        return None if pd.isna(value) else float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if value is pd.NA or (not isinstance(value, (str, bool)) and pd.isna(value)):
        return None
    return value
