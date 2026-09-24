"""策略二：个股与行业同方向比例报告。"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from stock_selection.strategies.stock_industry_direction.screen import (
    screen_stock_industry_direction,
)


def build_stock_industry_direction_report(
    metrics: pd.DataFrame,
    *,
    as_of_date: str,
    level: str,
    window: int,
    min_same_direction_ratio: float | None = None,
    max_results: int | None = None,
    industry_codes: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """构建报告字典；完整结果另存 CSV，JSON 只保留筛选结果。"""
    selected = screen_stock_industry_direction(
        metrics,
        min_same_direction_ratio=min_same_direction_ratio,
    )
    total_selected = len(selected)
    if max_results is not None:
        selected = selected.head(max_results)
    valid = metrics[metrics["data_quality"].eq("ok")]
    quality_counts = metrics["data_quality"].value_counts(dropna=False).rename_axis("status").reset_index(name="count")
    return {
        "metadata": {
            "report_type": "stock_industry_direction",
            "as_of_date": as_of_date,
            "industry_level": level,
            "industry_codes": list(industry_codes) if industry_codes else None,
            "window": int(window),
            "min_same_direction_ratio": min_same_direction_ratio,
            "zero_return_rule": "exclude dates where either stock or industry return is zero",
            "membership_scope": "current SW2021 membership",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
        "summary": {
            "membership_rows": int(len(metrics)),
            "valid_rows": int(len(valid)),
            "valid_stocks": int(valid["ts_code"].nunique()),
            "mean_same_direction_ratio": _optional_float(valid["same_direction_ratio"].mean()),
            "median_same_direction_ratio": _optional_float(valid["same_direction_ratio"].median()),
            "selected_rows_before_limit": int(total_selected),
            "returned_rows": int(len(selected)),
            "quality_counts": _records(quality_counts),
        },
        "results": _records(selected),
    }


def save_stock_industry_direction_report(
    report: dict[str, Any],
    metrics: pd.DataFrame,
    *,
    records_root: str | Path = "analysis_records",
) -> tuple[Path, Path, Path]:
    """保存完整 CSV、结构化 JSON 和便于阅读的 Markdown。"""
    metadata = report["metadata"]
    date_text = str(metadata["as_of_date"])
    formatted_date = f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:8]}"
    level = str(metadata["industry_level"]).upper()
    industry_codes = metadata.get("industry_codes") or []
    scope = f"-{str(industry_codes[0]).replace('.', '-')}" if len(industry_codes) == 1 else ""
    if len(industry_codes) > 1:
        scope = f"-{len(industry_codes)}-industries"
    directory = Path(records_root) / "stock_industry_direction" / f"{date_text[:4]}-{date_text[4:6]}"
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{formatted_date}-{level}{scope}-stock-industry-direction"
    csv_path = directory / f"{stem}.csv"
    json_path = directory / f"{stem}.json"
    markdown_path = directory / f"{stem}.md"
    metrics.to_csv(csv_path, index=False, encoding="utf-8-sig")
    json_path.write_text(
        json.dumps(_json_safe(report), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_stock_industry_direction_markdown(report), encoding="utf-8")
    return csv_path, json_path, markdown_path


def render_stock_industry_direction_markdown(report: dict[str, Any]) -> str:
    """渲染同方向比例报告。"""
    metadata = report["metadata"]
    summary = report["summary"]
    threshold = metadata["min_same_direction_ratio"]
    lines = [
        f"# 个股与申万{metadata['industry_level']}行业同方向比例 - {metadata['as_of_date']}",
        "",
        "## 计算方法",
        "",
        f"- 窗口：最近 {metadata['window']} 个已对齐交易日。",
        "- 个股和行业同涨或同跌，计为同方向。",
        "- 任意一方涨跌幅为零，计为中性日，不进入比例分母。",
        "- 同方向比例 = 同方向天数 /（同方向天数 + 反方向天数）。",
        "",
        "## 概览",
        "",
        f"- 当前成分关系：{summary['membership_rows']}",
        f"- 有效关系：{summary['valid_rows']}",
        f"- 有效股票：{summary['valid_stocks']}",
        f"- 平均同方向比例：{_format_percent(summary['mean_same_direction_ratio'])}",
        f"- 中位同方向比例：{_format_percent(summary['median_same_direction_ratio'])}",
        f"- 最低筛选比例：{_format_percent(threshold) if threshold is not None else '不限'}",
        "",
        "## 数据质量",
        "",
        "| 状态 | 数量 |",
        "| --- | ---: |",
    ]
    for item in summary["quality_counts"]:
        lines.append(f"| {item['status']} | {item['count']} |")
    lines.extend(
        [
            "",
            f"## 筛选结果（共 {summary['selected_rows_before_limit']} 条，展示 {summary['returned_rows']} 条）",
            "",
            "| 股票 | 行业 | 同方向比例 | 同方向 | 同涨 | 同跌 | 反方向 | 中性 |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    if not report["results"]:
        lines.append("| - | - | - | - | - | - | - | - |")
    for row in report["results"]:
        lines.append(
            f"| {row['ts_code']} {row.get('stock_name') or ''} | "
            f"{row['industry_code']} {row.get('industry_name') or ''} | "
            f"{_format_percent(row.get('same_direction_ratio'))} | "
            f"{row.get('same_direction_days', '-')} | {row.get('same_up_days', '-')} | "
            f"{row.get('same_down_days', '-')} | {row.get('opposite_direction_days', '-')} | "
            f"{row.get('neutral_days', '-')} |"
        )
    return "\n".join(lines) + "\n"


def _records(data: pd.DataFrame) -> list[dict[str, Any]]:
    return [_json_safe(record) for record in data.to_dict("records")]


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if pd.isna(value) else float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if value is pd.NA or (not isinstance(value, (str, bool)) and pd.isna(value)):
        return None
    return value


def _optional_float(value: Any) -> float | None:
    return None if value is None or pd.isna(value) else float(value)


def _format_percent(value: Any) -> str:
    return "-" if value is None or pd.isna(value) else f"{float(value):.1%}"
