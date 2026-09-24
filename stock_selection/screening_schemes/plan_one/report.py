"""筛选方案一的行业-个股分组报告。"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def build_plan_one_report(
    qualified_industries: pd.DataFrame,
    stock_metrics: pd.DataFrame,
    selected: pd.DataFrame,
    *,
    as_of_date: str,
    industry_window: int,
    min_rise_from_low: float,
    min_days_since_low: int,
    min_up_days: int,
    stock_window: int,
    min_same_direction_ratio: float,
) -> dict[str, Any]:
    """构建按策略一行业排名分组的方案一报告。"""
    selected_codes = set(selected["industry_code"]) if not selected.empty else set()
    no_stock = qualified_industries[
        ~qualified_industries["index_code"].isin(selected_codes)
    ]
    quality_counts = (
        stock_metrics["data_quality"].value_counts(dropna=False).rename_axis("status").reset_index(name="count")
        if not stock_metrics.empty
        else pd.DataFrame(columns=["status", "count"])
    )
    return {
        "metadata": {
            "report_type": "screening_plan_one",
            "as_of_date": as_of_date,
            "industry_level": "L2",
            "membership_scope": "current SW2021 membership",
            "industry_ranking": [
                "rise_from_low_pct desc",
                "up_days_after_low desc",
                "days_since_low desc",
                "industry_code asc",
            ],
            "filters": {
                "strategy_one": {
                    "window": industry_window,
                    "min_rise_from_low": min_rise_from_low,
                    "min_days_since_low": min_days_since_low,
                    "min_up_days": min_up_days,
                },
                "strategy_two": {
                    "window": stock_window,
                    "min_same_direction_ratio": min_same_direction_ratio,
                },
            },
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
        "summary": {
            "qualified_l2_industries": int(len(qualified_industries)),
            "industries_with_selected_stocks": int(selected["industry_code"].nunique()) if not selected.empty else 0,
            "industries_without_selected_stocks": _industry_references(no_stock),
            "candidate_membership_rows": int(len(stock_metrics)),
            "selected_stock_rows": int(len(selected)),
            "selected_unique_stocks": int(selected["ts_code"].nunique()) if not selected.empty else 0,
            "strategy_two_quality_counts": _records(quality_counts),
        },
        "industries": _build_industry_groups(qualified_industries, selected),
        "disclaimer": "仅作研究筛选，不构成投资建议。",
    }


def save_plan_one_report(
    report: dict[str, Any],
    selected: pd.DataFrame,
    *,
    records_root: str | Path = "analysis_records",
) -> tuple[Path, Path, Path]:
    """保存最终平铺 CSV、嵌套 JSON 和行业-个股 Markdown。"""
    date_text = str(report["metadata"]["as_of_date"])
    formatted_date = f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:8]}"
    directory = Path(records_root) / "screening_schemes" / "plan_one" / f"{date_text[:4]}-{date_text[4:6]}"
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{formatted_date}-screening-plan-one"
    csv_path = directory / f"{stem}.csv"
    json_path = directory / f"{stem}.json"
    markdown_path = directory / f"{stem}.md"
    selected.to_csv(csv_path, index=False, encoding="utf-8-sig")
    json_path.write_text(
        json.dumps(_json_safe(report), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_plan_one_markdown(report), encoding="utf-8")
    return csv_path, json_path, markdown_path


def render_plan_one_markdown(report: dict[str, Any]) -> str:
    """渲染以行业为一级、个股为二级的方案一结果。"""
    metadata = report["metadata"]
    summary = report["summary"]
    strategy_one = metadata["filters"]["strategy_one"]
    strategy_two = metadata["filters"]["strategy_two"]
    lines = [
        f"# 筛选方案一 - {metadata['as_of_date']}",
        "",
        "## 筛选流程",
        "",
        "1. 策略一筛选满足条件的申万 L2 行业。",
        "2. 策略二只计算这些行业当前成分股与所属行业的同方向比例。",
        "3. 保留达到同方向比例阈值的股票，并按行业分组展示。",
        "",
        "## 参数",
        "",
        f"- 策略一窗口：{strategy_one['window']} 个交易日",
        f"- 行业相对低点最小涨幅：{strategy_one['min_rise_from_low']:.1%}",
        f"- 行业低点后最少交易日：{strategy_one['min_days_since_low']}",
        f"- 行业低点后最少上涨日：{strategy_one['min_up_days']}",
        f"- 策略二窗口：{strategy_two['window']} 个交易日",
        f"- 个股最低同方向比例：{strategy_two['min_same_direction_ratio']:.1%}",
        "",
        "## 汇总",
        "",
        f"- 策略一合格 L2 行业：{summary['qualified_l2_industries']}",
        f"- 有股票通过策略二的行业：{summary['industries_with_selected_stocks']}",
        f"- 最终股票记录：{summary['selected_stock_rows']}",
        f"- 最终唯一股票：{summary['selected_unique_stocks']}",
        "",
        "## 策略二数据质量",
        "",
        "| 状态 | 数量 |",
        "| --- | ---: |",
    ]
    for item in summary["strategy_two_quality_counts"]:
        lines.append(f"| {item['status']} | {item['count']} |")
    lines.extend(
        [
        "",
        "## 行业与个股",
        "",
        ]
    )
    if not report["industries"]:
        lines.append("没有股票同时满足策略一和策略二。")
    for industry in report["industries"]:
        lines.extend(
            [
                f"### {industry['industry_rank']}. {industry['industry_code']} {industry['industry_name']}",
                "",
                f"- 相对窗口低点涨幅：{_format_percent(industry['rise_from_low_pct'])}",
                f"- 窗口低点日期：{industry['window_low_date']}",
                f"- 低点后交易日：{industry['days_since_low']}",
                f"- 低点后上涨日：{industry['up_days_after_low']}",
                f"- 通过策略二股票：{industry['selected_stock_count']}",
                "",
                "| 股票 | 同方向比例 | 同方向 | 同涨 | 同跌 | 反方向 | 中性 | 有方向样本 |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for stock in industry["stocks"]:
            lines.append(
                f"| {stock['ts_code']} {stock['stock_name']} | "
                f"{_format_percent(stock['same_direction_ratio'])} | "
                f"{stock['same_direction_days']} | {stock['same_up_days']} | "
                f"{stock['same_down_days']} | {stock['opposite_direction_days']} | "
                f"{stock['neutral_days']} | {stock['direction_observations']} |"
            )
        lines.append("")

    no_stock = summary["industries_without_selected_stocks"]
    if no_stock:
        lines.extend(["## 无个股通过的合格行业", ""])
        lines.extend(f"- {item['industry_code']} {item['industry_name']}" for item in no_stock)
        lines.append("")
    lines.extend(["> 仅作研究筛选，不构成投资建议。", ""])
    return "\n".join(lines)


def _build_industry_groups(
    qualified_industries: pd.DataFrame,
    selected: pd.DataFrame,
) -> list[dict[str, Any]]:
    if selected.empty:
        return []
    groups = []
    for industry in qualified_industries.itertuples(index=False):
        stocks = selected[selected["industry_code"].eq(industry.index_code)]
        if stocks.empty:
            continue
        groups.append(
            {
                "industry_rank": int(industry.industry_rank),
                "industry_code": industry.index_code,
                "industry_name": industry.name,
                "rise_from_low_pct": float(industry.rise_from_low_pct),
                "window_low_date": industry.window_low_date,
                "days_since_low": int(industry.days_since_low),
                "up_days_after_low": int(industry.up_days_after_low),
                "selected_stock_count": int(len(stocks)),
                "stocks": _records(
                    stocks[
                        [
                            "ts_code",
                            "stock_name",
                            "same_direction_ratio",
                            "same_direction_days",
                            "same_up_days",
                            "same_down_days",
                            "opposite_direction_days",
                            "neutral_days",
                            "direction_observations",
                        ]
                    ]
                ),
            }
        )
    return groups


def _industry_references(data: pd.DataFrame) -> list[dict[str, Any]]:
    if data.empty:
        return []
    return [
        {"industry_code": row.index_code, "industry_name": row.name}
        for row in data.itertuples(index=False)
    ]


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


def _format_percent(value: Any) -> str:
    return "-" if value is None or pd.isna(value) else f"{float(value):.2%}"
