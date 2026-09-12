"""正式 Tushare 缓存的统一读写入口。

日频数据按接口/交易日归档，静态数据单独归档，运行元数据放在 ``_metadata``。
"""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_CACHE_ROOT = Path(os.getenv("TUSHARE_CACHE_ROOT", "data_cache/tushare"))


def dataset_path(
    api_name: str,
    trade_date: str | None = None,
    *,
    static: bool = False,
    cache_root: str | Path | None = None,
) -> Path:
    """根据接口、交易日和静态标记计算唯一的正式缓存路径。"""
    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    # 静态表不属于某个交易日，避免与日频横截面混在同一目录。
    if static:
        return root / "static" / f"{api_name}.parquet"
    if not trade_date:
        raise ValueError("trade_date is required for non-static datasets")
    return root / api_name / f"{trade_date}.parquet"


def dataset_exists(
    api_name: str,
    trade_date: str | None = None,
    *,
    static: bool = False,
    cache_root: str | Path | None = None,
) -> bool:
    """检查目标正式缓存是否已存在，不触发读取。"""
    return dataset_path(api_name, trade_date, static=static, cache_root=cache_root).exists()


def write_dataset(
    data: pd.DataFrame,
    api_name: str,
    trade_date: str | None = None,
    *,
    static: bool = False,
    cache_root: str | Path | None = None,
) -> Path:
    """将 DataFrame 写为 Parquet，并在缺少 Parquet 引擎时给出明确错误。"""
    path = dataset_path(api_name, trade_date, static=static, cache_root=cache_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Parquet 既保留类型又适合大横截面数据；不静默降级以免产生未知格式。
    try:
        data.to_parquet(path, index=False)
    except ImportError as exc:
        raise RuntimeError(
            "Parquet caching requires pyarrow or fastparquet. Install the data "
            'extra with `pip install -e ".[data]"`.'
        ) from exc
    return path


def read_dataset(
    api_name: str,
    trade_date: str | None = None,
    *,
    static: bool = False,
    cache_root: str | Path | None = None,
) -> pd.DataFrame:
    """优先读取 Parquet；兼容历史 CSV 回退文件。"""
    path = dataset_path(api_name, trade_date, static=static, cache_root=cache_root)
    if path.exists():
        return pd.read_parquet(path)

    # 老自动化可能留下 CSV，因此只在 Parquet 缺失时兼容读取。
    csv_path = path.with_suffix(".csv")
    if csv_path.exists():
        return pd.read_csv(csv_path)

    return pd.read_parquet(path)


def metadata_path(name: str, *, cache_root: str | Path | None = None) -> Path:
    """返回运行清单、调用日志和权限结果使用的元数据路径。"""
    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    return root / "_metadata" / name


def append_jsonl(name: str, record: dict[str, Any], *, cache_root: str | Path | None = None) -> Path:
    """追加一条带记录时间的 JSONL 审计日志。"""
    path = metadata_path(name, cache_root=cache_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    enriched = {"recorded_at": datetime.now().isoformat(timespec="seconds"), **record}
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(enriched, ensure_ascii=False, default=str) + "\n")
    return path


def write_json(name: str, payload: dict[str, Any], *, cache_root: str | Path | None = None) -> Path:
    """覆盖写入结构化元数据 JSON，例如盘后 manifest。"""
    path = metadata_path(name, cache_root=cache_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2, default=str)
    return path


def read_json(name: str, *, cache_root: str | Path | None = None) -> dict[str, Any]:
    """读取元数据 JSON；文件不存在时返回空字典，便于首次运行。"""
    path = metadata_path(name, cache_root=cache_root)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)
