"""策略二：个股与行业同方向比例报告。"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from stock_selection.strategies.stock_industry_direction.screen import (
    OUTPUT_COLUMNS,
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
    regression_quality_counts = (
        metrics["regression_quality"]
        .value_counts(dropna=False)
        .rename_axis("status")
        .reset_index(name="count")
        if "regression_quality" in metrics
        else pd.DataFrame(columns=["status", "count"])
    )
    correlations = valid.get("return_correlation", pd.Series(dtype=float))
    betas = valid.get("industry_beta", pd.Series(dtype=float))
    r_squared = valid.get("industry_r_squared", pd.Series(dtype=float))
    return {
        "metadata": {
            "report_type": "stock_industry_direction",
            "as_of_date": as_of_date,
            "industry_level": level,
            "industry_codes": list(industry_codes) if industry_codes else None,
            "window": int(window),
            "min_same_direction_ratio": min_same_direction_ratio,
            "zero_return_rule": "exclude dates where either stock or industry return is zero",
            "regression_zero_return_rule": "retain zero returns",
            "regression_model": "stock_return = alpha + beta * industry_return + error",
            "membership_scope": "current SW2021 membership",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
        "summary": {
            "membership_rows": int(len(metrics)),
            "valid_rows": int(len(valid)),
            "valid_stocks": int(valid["ts_code"].nunique()),
            "mean_same_direction_ratio": _optional_float(valid["same_direction_ratio"].mean()),
            "median_same_direction_ratio": _optional_float(valid["same_direction_ratio"].median()),
            "mean_return_correlation": _optional_float(correlations.mean()),
            "median_industry_beta": _optional_float(betas.median()),
            "mean_industry_r_squared": _optional_float(r_squared.mean()),
            "selected_rows_before_limit": int(total_selected),
            "returned_rows": int(len(selected)),
            "quality_counts": _records(quality_counts),
            "regression_quality_counts": _records(regression_quality_counts),
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


def save_stock_industry_direction_history(
    history: pd.DataFrame,
    *,
    end_date: str,
    level: str,
    window: int,
    industry_codes: tuple[str, ...] | None = None,
    records_root: str | Path = "analysis_records",
) -> tuple[Path, Path]:
    """保存完整历史滚动长表和可审计元数据。"""
    parquet_path, metadata_path, _ = save_stock_industry_direction_history_batches(
        (history,),
        end_date=end_date,
        level=level,
        window=window,
        industry_codes=industry_codes,
        records_root=records_root,
    )
    return parquet_path, metadata_path


def save_stock_industry_direction_history_batches(
    batches: Iterable[pd.DataFrame],
    *,
    end_date: str,
    level: str,
    window: int,
    industry_codes: tuple[str, ...] | None = None,
    records_root: str | Path = "analysis_records",
) -> tuple[Path, Path, dict[str, Any]]:
    """分批写入单个 Parquet 历史长表，避免数百万行同时驻留内存。"""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError(
            "Historical Parquet output requires pyarrow; install the data extra."
        ) from exc

    date_text = str(end_date).replace("-", "")[:8]
    formatted_date = f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:8]}"
    normalised_level = str(level).upper()
    scope = _industry_scope(industry_codes)
    directory = Path(records_root) / "stock_industry_direction" / "history"
    directory.mkdir(parents=True, exist_ok=True)
    stem = (
        f"through-{formatted_date}-{normalised_level}{scope}-"
        f"window-{int(window)}-stock-industry-direction-history"
    )
    parquet_path = directory / f"{stem}.parquet"
    metadata_path = directory / f"{stem}.json"
    summary: dict[str, Any] = {
        "rows": 0,
        "valid_rows": 0,
        "first_output_date": None,
        "last_output_date": None,
        "dates": set(),
        "stocks": set(),
        "relationships": set(),
    }
    writer = None
    try:
        for batch in batches:
            if batch is None or batch.empty:
                continue
            ordered = batch.reindex(columns=OUTPUT_COLUMNS).sort_values(
                ["as_of_date", "industry_code", "ts_code"]
            )
            table = pa.Table.from_pandas(ordered, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(parquet_path, table.schema, compression="snappy")
            writer.write_table(table)
            _update_history_summary(summary, ordered)
    finally:
        if writer is not None:
            writer.close()
    if writer is None:
        pd.DataFrame(columns=OUTPUT_COLUMNS).to_parquet(parquet_path, index=False)

    public_summary = {
        "rows": int(summary["rows"]),
        "valid_rows": int(summary["valid_rows"]),
        "dates": int(len(summary["dates"])),
        "stocks": int(len(summary["stocks"])),
        "relationships": int(len(summary["relationships"])),
        "first_output_date": summary["first_output_date"],
        "last_output_date": summary["last_output_date"],
    }
    metadata = {
        "report_type": "stock_industry_direction_history",
        "end_date": date_text,
        "first_output_date": public_summary["first_output_date"],
        "last_output_date": public_summary["last_output_date"],
        "industry_level": normalised_level,
        "industry_codes": list(industry_codes) if industry_codes else None,
        "window": int(window),
        "rows": public_summary["rows"],
        "valid_rows": public_summary["valid_rows"],
        "dates": public_summary["dates"],
        "stocks": public_summary["stocks"],
        "relationships": public_summary["relationships"],
        "return_source": "Tushare daily.pct_chg and sw_daily.pct_change/pct_chg",
        "window_rule": "latest N aligned non-null stock and industry daily returns",
        "direction_zero_return_rule": "exclude from direction ratio denominator",
        "regression_zero_return_rule": "retain in correlation, beta, and R-squared",
        "regression_model": "stock_return = alpha + beta * industry_return + error",
        "membership_scope": "current SW2021 membership",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    metadata_path.write_text(
        json.dumps(_json_safe(metadata), ensure_ascii=False, indent=2, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    return parquet_path, metadata_path, public_summary


def summarize_stock_industry_direction_history_batches(
    batches: Iterable[pd.DataFrame],
) -> dict[str, Any]:
    """消费历史分批数据并返回轻量汇总，不保留全量长表。"""
    summary: dict[str, Any] = {
        "rows": 0,
        "valid_rows": 0,
        "first_output_date": None,
        "last_output_date": None,
        "dates": set(),
        "stocks": set(),
        "relationships": set(),
    }
    for batch in batches:
        if batch is not None and not batch.empty:
            _update_history_summary(summary, batch)
    return {
        "rows": int(summary["rows"]),
        "valid_rows": int(summary["valid_rows"]),
        "dates": int(len(summary["dates"])),
        "stocks": int(len(summary["stocks"])),
        "relationships": int(len(summary["relationships"])),
        "first_output_date": summary["first_output_date"],
        "last_output_date": summary["last_output_date"],
    }


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
        "- 相关系数、Beta 和 R² 用收益率计算，并保留零收益日。",
        "",
        "## 概览",
        "",
        f"- 当前成分关系：{summary['membership_rows']}",
        f"- 有效关系：{summary['valid_rows']}",
        f"- 有效股票：{summary['valid_stocks']}",
        f"- 平均同方向比例：{_format_percent(summary['mean_same_direction_ratio'])}",
        f"- 中位同方向比例：{_format_percent(summary['median_same_direction_ratio'])}",
        f"- 平均收益率相关系数：{_format_number(summary['mean_return_correlation'])}",
        f"- 中位行业 Beta：{_format_number(summary['median_industry_beta'])}",
        f"- 平均 R²：{_format_number(summary['mean_industry_r_squared'])}",
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
            "| 股票 | 行业 | 同方向比例 | 同方向 | 同涨 | 同跌 | 反方向 | 中性 | 相关系数 | Beta | R² |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    if not report["results"]:
        lines.append("| - | - | - | - | - | - | - | - | - | - | - |")
    for row in report["results"]:
        lines.append(
            f"| {row['ts_code']} {row.get('stock_name') or ''} | "
            f"{row['industry_code']} {row.get('industry_name') or ''} "
            f"(L1 {row.get('l1_code') or '-'} {row.get('l1_name') or ''}; "
            f"L2 {row.get('l2_code') or '-'} {row.get('l2_name') or ''}; "
            f"L3 {row.get('l3_code') or '-'} {row.get('l3_name') or ''}) | "
            f"{_format_percent(row.get('same_direction_ratio'))} | "
            f"{row.get('same_direction_days', '-')} | {row.get('same_up_days', '-')} | "
            f"{row.get('same_down_days', '-')} | {row.get('opposite_direction_days', '-')} | "
            f"{row.get('neutral_days', '-')} | "
            f"{_format_number(row.get('return_correlation'))} | "
            f"{_format_number(row.get('industry_beta'))} | "
            f"{_format_number(row.get('industry_r_squared'))} |"
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


def _format_number(value: Any) -> str:
    return "-" if value is None or pd.isna(value) else f"{float(value):.4f}"


def _industry_scope(industry_codes: tuple[str, ...] | None) -> str:
    codes = industry_codes or ()
    if len(codes) == 1:
        return f"-{str(codes[0]).replace('.', '-')}"
    if len(codes) > 1:
        return f"-{len(codes)}-industries"
    return ""


def _update_history_summary(summary: dict[str, Any], batch: pd.DataFrame) -> None:
    summary["rows"] += int(len(batch))
    summary["valid_rows"] += int(batch["data_quality"].eq("ok").sum())
    batch_first = str(batch["as_of_date"].min())
    batch_last = str(batch["as_of_date"].max())
    if summary["first_output_date"] is None or batch_first < summary["first_output_date"]:
        summary["first_output_date"] = batch_first
    if summary["last_output_date"] is None or batch_last > summary["last_output_date"]:
        summary["last_output_date"] = batch_last
    summary["dates"].update(batch["as_of_date"].astype(str).unique())
    summary["stocks"].update(batch["ts_code"].astype(str).unique())
    summary["relationships"].update(
        batch[["ts_code", "industry_code"]].drop_duplicates().itertuples(
            index=False, name=None
        )
    )
