"""Read and normalize broker securities-lending availability exports."""

from __future__ import annotations

from io import StringIO
from pathlib import Path
import re
from typing import Any

import pandas as pd


DEFAULT_LENDING_ROOT = Path("data_cache/securities_lending/daily")

SOURCE_COLUMNS = [
    "市场类别",
    "证券代码",
    "证券名称",
    "可用数量",
    "保证金比例(%)",
    "融券状态",
]

OUTPUT_COLUMNS = [
    "lending_date",
    "market",
    "security_code",
    "ts_code",
    "security_name",
    "available_quantity",
    "margin_ratio",
    "lending_status",
]


def resolve_lending_file(
    *,
    as_of_date: str,
    lending_file: str | Path | None = None,
    lending_root: str | Path | None = None,
    allow_previous: bool = False,
) -> Path:
    """Resolve an explicit file or a dated file from the standard directory."""
    target_date = _normalise_date(as_of_date)
    if not target_date:
        raise ValueError("as_of_date must be a valid date")
    if lending_file is not None:
        path = Path(lending_file)
        if not path.exists():
            raise FileNotFoundError(f"lending file does not exist: {path}")
        return path

    root = Path(lending_root) if lending_root is not None else DEFAULT_LENDING_ROOT
    exact_paths = [
        root / f"{target_date}.xls",
        root / f"{target_date}.tsv",
        root / f"{target_date}.txt",
        root / f"{target_date}.csv",
    ]
    for path in exact_paths:
        if path.exists():
            return path

    dated_paths = sorted(
        (
            path
            for path in root.glob("*")
            if path.is_file() and extract_lending_date(path)
        ),
        key=lambda path: (extract_lending_date(path), path.name),
    )
    exact_matches = [
        path for path in dated_paths if extract_lending_date(path) == target_date
    ]
    if exact_matches:
        return exact_matches[-1]
    if allow_previous:
        previous = [
            path for path in dated_paths if extract_lending_date(path) <= target_date
        ]
        if previous:
            return previous[-1]
    raise FileNotFoundError(
        f"no securities-lending file found for {target_date} under {root}"
    )


def read_lending_file(
    path: str | Path,
    *,
    lending_date: str | None = None,
) -> pd.DataFrame:
    """Read the broker's GBK tab-delimited export disguised as an XLS file."""
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"lending file does not exist: {source}")
    raw = source.read_bytes()
    if raw.startswith(b"\xd0\xcf\x11\xe0") or raw.startswith(b"PK\x03\x04"):
        raise ValueError(
            "binary Excel lending files are not supported; export as the broker "
            "tab-delimited text format"
        )
    text = _decode_export(raw)
    data = pd.read_csv(
        StringIO(text),
        sep="\t",
        dtype=str,
        keep_default_na=False,
        engine="python",
    )
    data = data.rename(columns={column: _clean_export_value(column) for column in data})
    source_date = _normalise_date(lending_date) or extract_lending_date(source)
    if not source_date:
        raise ValueError(
            "lending_date is required when the source filename has no YYYYMMDD date"
        )
    return normalise_lending_data(data, lending_date=source_date)


def normalise_lending_data(
    data: pd.DataFrame,
    *,
    lending_date: str,
) -> pd.DataFrame:
    """Normalize broker columns and convert market codes to Tushare codes."""
    source_date = _normalise_date(lending_date)
    if not source_date:
        raise ValueError("lending_date must be a valid date")
    missing = [column for column in SOURCE_COLUMNS if column not in data.columns]
    if missing:
        raise ValueError(f"lending data is missing columns: {', '.join(missing)}")

    cleaned = pd.DataFrame(
        {
            "lending_date": source_date,
            "market": data["市场类别"].map(_clean_export_value),
            "security_code": data["证券代码"].map(_clean_export_value),
            "security_name": data["证券名称"].map(_clean_export_value),
            "available_quantity": pd.to_numeric(
                data["可用数量"].map(_clean_export_value),
                errors="coerce",
            ),
            "margin_ratio": pd.to_numeric(
                data["保证金比例(%)"].map(_clean_export_value),
                errors="coerce",
            ),
            "lending_status": data["融券状态"].map(_clean_export_value),
        }
    )
    cleaned["security_code"] = (
        cleaned["security_code"].fillna("").astype(str).str.strip().str.zfill(6)
    )
    cleaned["ts_code"] = [
        _to_ts_code(market, code)
        for market, code in zip(cleaned["market"], cleaned["security_code"])
    ]
    cleaned = cleaned[
        cleaned["security_code"].str.fullmatch(r"\d{6}", na=False)
        & cleaned["available_quantity"].notna()
        & cleaned["available_quantity"].ge(0)
    ]
    cleaned["available_quantity"] = cleaned["available_quantity"].astype("int64")
    return cleaned[OUTPUT_COLUMNS].drop_duplicates(
        ["market", "security_code"], keep="last"
    ).reset_index(drop=True)


def extract_lending_date(path: str | Path) -> str:
    """Extract the first YYYYMMDD date from a filename."""
    match = re.search(r"(?<!\d)(20\d{6})(?!\d)", Path(path).stem)
    return match.group(1) if match else ""


def _decode_export(raw: bytes) -> str:
    for encoding in ("gb18030", "utf-8-sig"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("unable to decode lending export as GB18030 or UTF-8")


def _clean_export_value(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if text.startswith('="') and text.endswith('"'):
        return text[2:-1].strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1].strip()
    return text


def _to_ts_code(market: str, security_code: str) -> str:
    market_text = str(market).strip().upper()
    if "上海" in market_text:
        return f"{security_code}.SH"
    if "深圳" in market_text:
        return f"{security_code}.SZ"
    return ""


def _normalise_date(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip().replace("-", "")
    return text[:8] if len(text) >= 8 and text[:8].isdigit() else ""
