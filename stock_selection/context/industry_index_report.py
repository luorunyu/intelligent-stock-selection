"""申万行业指数初筛的分级报告构建与保存。"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

import pandas as pd


REPORT_METRIC_COLUMNS = [
    "latest_trade_date",
    "current_close",
    "window",
    "window_start_date",
    "window_low_date",
    "window_low_close",
    "days_since_low",
    "rise_from_low_pct",
    "up_days_after_low",
    "up_pct_sum_after_low",
    "down_days_after_low",
    "down_pct_sum_after_low",
    "flat_days_after_low",
    "data_quality",
]


def mark_qualified_industries(
    metrics: pd.DataFrame,
    *,
    min_rise_from_low: float | None = None,
    min_days_since_low: int | None = None,
    min_up_days: int | None = None,
) -> pd.DataFrame:
    """按照统一阈值给每个行业指数增加 qualified 标记。"""
    result = metrics.copy()
    qualified = result["data_quality"].eq("ok")
    if min_rise_from_low is not None:
        qualified &= result["rise_from_low_pct"].ge(min_rise_from_low)
    if min_days_since_low is not None:
        qualified &= result["days_since_low"].ge(min_days_since_low)
    if min_up_days is not None:
        qualified &= result["up_days_after_low"].ge(min_up_days)
    result["qualified"] = qualified.fillna(False).astype(bool)
    return result


def build_industry_index_report(
    metrics: pd.DataFrame,
    hierarchy: dict[str, Any],
    *,
    as_of_date: str,
    window: int,
    min_rise_from_low: float | None = None,
    min_days_since_low: int | None = None,
    min_up_days: int | None = None,
) -> dict[str, Any]:
    """构建一级到三级的递归行业初筛报告。"""
    marked = mark_qualified_industries(
        metrics,
        min_rise_from_low=min_rise_from_low,
        min_days_since_low=min_days_since_low,
        min_up_days=min_up_days,
    )
    metric_by_code = marked.drop_duplicates("index_code").set_index("index_code").to_dict("index")
    level_counts = {
        "level1": {"total": 0, "qualified": 0},
        "level2": {"total": 0, "qualified": 0},
        "level3": {"total": 0, "qualified": 0},
    }
    level1_nodes: list[dict[str, Any]] = []

    for code, node in hierarchy.items():
        built = _build_level1_node(code, node, metric_by_code, level_counts)
        if built["qualified"] or built["children_summary"]["qualified_descendants"] > 0:
            level1_nodes.append(built)

    return {
        "metadata": {
            "report_type": "sw_industry_index_screen",
            "as_of_date": as_of_date,
            "window": window,
            "filters": {
                "min_rise_from_low": min_rise_from_low,
                "min_days_since_low": min_days_since_low,
                "min_up_days": min_up_days,
            },
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
        "summary": level_counts,
        "level1": level1_nodes,
    }


def render_industry_index_report_markdown(report: dict[str, Any]) -> str:
    """将结构化行业报告渲染为适合人工阅读的 Markdown。"""
    metadata = report["metadata"]
    summary = report["summary"]
    lines = [
        f"# 申万行业指数近月低点初筛报告 - {metadata['as_of_date']}",
        "",
        "## 筛选条件",
        f"- 截止日期：{metadata['as_of_date']}",
        f"- 统计窗口：最近{metadata['window']}个交易日",
        f"- 相对最低点最小涨幅：{_format_filter(metadata['filters']['min_rise_from_low'], 'pct')}",
        f"- 最低点后最少交易日：{_format_filter(metadata['filters']['min_days_since_low'], 'days')}",
        f"- 最低点后最少上涨天数：{_format_filter(metadata['filters']['min_up_days'], 'days')}",
        "",
        "## 总览",
        "",
        "| 层级 | 行业总数 | 满足条件数量 | 满足比例 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for level, label in (("level1", "一级行业"), ("level2", "二级行业"), ("level3", "三级行业")):
        total = summary[level]["total"]
        qualified = summary[level]["qualified"]
        ratio = qualified / total if total else 0
        lines.append(f"| {label} | {total} | {qualified} | {ratio:.1%} |")

    lines.extend(["", "## 一级行业详情", ""])
    if not report["level1"]:
        lines.append("没有一级行业自身或其下级行业满足筛选条件。")
        return "\n".join(lines) + "\n"

    for node in report["level1"]:
        lines.extend(_render_level1(node))
    return "\n".join(lines) + "\n"


def save_industry_index_report(
    report: dict[str, Any],
    *,
    records_root: str | Path = "analysis_records",
) -> tuple[Path, Path]:
    """保存 JSON 和 Markdown 两种报告文件。"""
    date_text = str(report["metadata"]["as_of_date"])
    formatted_date = f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:8]}"
    directory = Path(records_root) / "sector_analysis" / f"{date_text[:4]}-{date_text[4:6]}"
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{formatted_date}-industry-index-screen.json"
    markdown_path = directory / f"{formatted_date}-industry-index-screen.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    markdown_path.write_text(render_industry_index_report_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def load_industry_hierarchy(dictionary_path: str | Path) -> dict[str, Any]:
    """读取行业大字典中的嵌套一级树。"""
    path = Path(dictionary_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    hierarchy = payload.get("level1")
    if not isinstance(hierarchy, dict):
        raise ValueError("industry dictionary does not contain a nested level1 tree")
    return hierarchy


def _build_level1_node(
    code: str,
    node: dict[str, Any],
    metric_by_code: dict[str, dict[str, Any]],
    counts: dict[str, dict[str, int]],
) -> dict[str, Any]:
    return _build_node(code, node, "level1", metric_by_code, counts)


def _build_node(
    code: str,
    node: dict[str, Any],
    level: str,
    metric_by_code: dict[str, dict[str, Any]],
    counts: dict[str, dict[str, int]],
) -> dict[str, Any]:
    metric = metric_by_code.get(code, {})
    qualified = bool(metric.get("qualified", False))
    counts[level]["total"] += 1
    if qualified:
        counts[level]["qualified"] += 1

    all_children = []
    for child_code, child_node in (node.get("children") or {}).items():
        child_level = _next_level(level)
        child = _build_node(child_code, child_node, child_level, metric_by_code, counts)
        all_children.append(child)

    children = [
        child
        for child in all_children
        if child["qualified"] or child["children_summary"]["qualified_descendants"] > 0
    ]

    descendant_counts = {
        "level2": {"total": 0, "qualified": 0},
        "level3": {"total": 0, "qualified": 0},
    }
    for child in all_children:
        child_level = child["level"]
        descendant_counts[child_level]["total"] += 1
        descendant_counts[child_level]["qualified"] += int(child["qualified"])
        for descendant_level, values in child["children_summary"]["descendant_counts"].items():
            descendant_counts[descendant_level]["total"] += values["total"]
            descendant_counts[descendant_level]["qualified"] += values["qualified"]
    qualified_descendants = sum(values["qualified"] for values in descendant_counts.values())
    total_descendants = sum(values["total"] for values in descendant_counts.values())
    result = {
        "index_code": code,
        "name": node.get("name", ""),
        "level": level,
        "qualified": qualified,
        "metrics": _select_metrics(metric),
        "children_summary": {
            "direct_children_total": len(node.get("children") or {}),
            "direct_children_qualified": sum(int(child["qualified"]) for child in all_children),
            "qualified_descendants": qualified_descendants,
            "total_descendants": total_descendants,
            "descendant_counts": descendant_counts,
        },
    }
    if children:
        result[_child_key(level)] = children
    return result


def _select_metrics(metric: dict[str, Any]) -> dict[str, Any]:
    return {column: metric.get(column) for column in REPORT_METRIC_COLUMNS if column in metric}


def _next_level(level: str) -> str:
    return {"level1": "level2", "level2": "level3"}[level]


def _child_key(level: str) -> str:
    return {"level1": "level2", "level2": "level3"}[level]


def _render_level1(node: dict[str, Any]) -> list[str]:
    lines = _render_node_heading(node, 3)
    lines.extend(_render_metric_table([node]))
    summary = node["children_summary"]
    lines.extend(
        [
            "",
            f"- 二级行业：满足 {summary['descendant_counts']['level2']['qualified']} / {summary['descendant_counts']['level2']['total']}",
            f"- 三级行业：满足 {summary['descendant_counts']['level3']['qualified']} / {summary['descendant_counts']['level3']['total']}",
            "",
        ]
    )
    for level2 in node.get("level2", []):
        lines.extend(_render_level2(level2))
    return lines


def _render_level2(node: dict[str, Any]) -> list[str]:
    lines = _render_node_heading(node, 4)
    lines.extend(_render_metric_table([node]))
    summary = node["children_summary"]
    lines.extend(
        [
            "",
            f"- 三级行业：满足 {summary['direct_children_qualified']} / {summary['direct_children_total']}",
            "",
        ]
    )
    for level3 in node.get("level3", []):
        lines.extend(_render_node_heading(level3, 5))
        lines.extend(_render_metric_table([level3]))
    return lines


def _render_node_heading(node: dict[str, Any], depth: int) -> list[str]:
    status = "满足" if node["qualified"] else "未满足"
    return [f"{'#' * depth} {node['index_code']} {node['name']}（{status}）", ""]


def _render_metric_table(nodes: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| 行业代码 | 行业名称 | 低点日期 | 低点后天数 | 相对低点涨幅 | 上涨天数 | 上涨累计 | 下跌天数 | 下跌累计 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for node in nodes:
        metrics = node["metrics"]
        lines.append(
            "| {index_code} | {name} | {low_date} | {days} | {rise} | {up_days} | {up_sum} | {down_days} | {down_sum} |".format(
                index_code=node["index_code"],
                name=node["name"],
                low_date=metrics.get("window_low_date", "-") or "-",
                days=_format_number(metrics.get("days_since_low")),
                rise=_format_percent(metrics.get("rise_from_low_pct")),
                up_days=_format_number(metrics.get("up_days_after_low")),
                up_sum=_format_percent(metrics.get("up_pct_sum_after_low")),
                down_days=_format_number(metrics.get("down_days_after_low")),
                down_sum=_format_percent(metrics.get("down_pct_sum_after_low")),
            )
        )
    return lines


def _format_filter(value: Any, kind: str) -> str:
    if value is None:
        return "未设置"
    if kind == "pct":
        return _format_percent(value)
    return f"{value}天"


def _format_percent(value: Any) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.2%}"


def _format_number(value: Any) -> str:
    if value is None or pd.isna(value):
        return "-"
    return str(int(value))
