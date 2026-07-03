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
    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
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
    return dataset_path(api_name, trade_date, static=static, cache_root=cache_root).exists()


def write_dataset(
    data: pd.DataFrame,
    api_name: str,
    trade_date: str | None = None,
    *,
    static: bool = False,
    cache_root: str | Path | None = None,
) -> Path:
    path = dataset_path(api_name, trade_date, static=static, cache_root=cache_root)
    path.parent.mkdir(parents=True, exist_ok=True)
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
    path = dataset_path(api_name, trade_date, static=static, cache_root=cache_root)
    if path.exists():
        return pd.read_parquet(path)

    csv_path = path.with_suffix(".csv")
    if csv_path.exists():
        return pd.read_csv(csv_path)

    return pd.read_parquet(path)


def metadata_path(name: str, *, cache_root: str | Path | None = None) -> Path:
    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    return root / "_metadata" / name


def append_jsonl(name: str, record: dict[str, Any], *, cache_root: str | Path | None = None) -> Path:
    path = metadata_path(name, cache_root=cache_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    enriched = {"recorded_at": datetime.now().isoformat(timespec="seconds"), **record}
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(enriched, ensure_ascii=False, default=str) + "\n")
    return path


def write_json(name: str, payload: dict[str, Any], *, cache_root: str | Path | None = None) -> Path:
    path = metadata_path(name, cache_root=cache_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2, default=str)
    return path


def read_json(name: str, *, cache_root: str | Path | None = None) -> dict[str, Any]:
    path = metadata_path(name, cache_root=cache_root)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)
