from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from stock_selection.data.tushare_cache import DEFAULT_CACHE_ROOT, metadata_path, read_dataset, write_json
from stock_selection.data.tushare_collector import CollectionResult, TushareCollector
from stock_selection.data.tushare_client import call_api


DEFAULT_REQUIRED_APIS = ["trade_cal", "daily", "daily_basic"]
DEFAULT_OPTIONAL_APIS = [
    "stk_limit",
    "moneyflow",
    "limit_list_d",
    "top_list",
    "margin",
    "margin_detail",
    "sw_daily",
    "stock_basic",
]
DEFAULT_INDEX_CODES = [
    "000001.SH",
    "399001.SZ",
    "399006.SZ",
    "000688.SH",
    "000300.SH",
    "000905.SH",
    "000852.SH",
    "899050.BJ",
]


@dataclass(frozen=True)
class AfterCloseManifest:
    requested_date: str
    trade_date: str
    is_requested_date_open: bool
    data_ready_for_requested_date: bool
    cache_root: str
    results: list[dict[str, Any]]
    notes: list[str]


@dataclass(frozen=True)
class LatestAfterCloseBaseline:
    """Read-only baseline for pre-open workflows."""

    manifest_path: str | None
    requested_date: str | None
    trade_date: str | None
    data_ready_for_requested_date: bool | None
    cache_root: str
    source: str


@dataclass(frozen=True)
class AfterCloseReportContext:
    requested_date: str
    trade_date: str
    report_date: str
    manifest_path: str
    cache_root: str
    data_ready_for_requested_date: bool
    is_requested_date_open: bool
    results: list[dict[str, Any]]
    notes: list[str]
    dataset_paths: dict[str, str]
    missing_datasets: list[str]


def prepare_after_close_data(
    requested_date: str | None = None,
    *,
    cache_root: str | Path | None = None,
    force: bool = False,
    lookback_days: int = 10,
    required_api_names: list[str] | None = None,
    optional_api_names: list[str] | None = None,
    index_codes: list[str] | None = None,
    available_only: bool = False,
) -> AfterCloseManifest:
    """Collect the latest complete after-close Tushare bundle into formal cache."""

    req_date = requested_date or date.today().strftime("%Y%m%d")
    collector = TushareCollector(cache_root=cache_root)
    open_dates = _open_dates(req_date, lookback_days, cache_root=cache_root)
    if not open_dates:
        raise RuntimeError(f"no open trading days found before {req_date}")

    required = list(required_api_names or DEFAULT_REQUIRED_APIS)
    optional = list(optional_api_names or DEFAULT_OPTIONAL_APIS)
    idx_codes = list(index_codes or DEFAULT_INDEX_CODES)
    notes: list[str] = []
    selected_date: str | None = None
    selected_results: list[CollectionResult] = []

    for candidate in reversed(open_dates):
        results = collector.collect_daily(
            candidate,
            api_names=required + [api for api in optional if api not in required],
            force=force,
            available_only=available_only,
        )
        if _required_ready(candidate, required, results, cache_root=cache_root):
            selected_date = candidate
            selected_results = results
            break
        notes.append(f"{candidate}: required daily bundle not ready")

    if selected_date is None:
        raise RuntimeError(f"no complete daily/daily_basic bundle found before {req_date}")

    index_result = collect_index_daily_selected(
        selected_date,
        idx_codes,
        cache_root=cache_root,
        force=force,
    )
    selected_results.append(index_result)

    manifest = AfterCloseManifest(
        requested_date=req_date,
        trade_date=selected_date,
        is_requested_date_open=req_date in open_dates,
        data_ready_for_requested_date=selected_date == req_date,
        cache_root=str(Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT),
        results=[
            asdict(result)
            for result in _enrich_cached_results(selected_results, selected_date, cache_root=cache_root)
        ],
        notes=notes,
    )
    write_json(f"after_close_{req_date}.json", asdict(manifest), cache_root=cache_root)
    return manifest


def build_after_close_report_context(
    manifest: AfterCloseManifest | dict[str, Any],
    *,
    cache_root: str | Path | None = None,
    required_dataset_names: list[str] | None = None,
) -> AfterCloseReportContext:
    """Build the report-stage context from a formal after-close manifest."""

    payload = asdict(manifest) if isinstance(manifest, AfterCloseManifest) else dict(manifest)
    req_date = str(payload["requested_date"])
    trade_date = str(payload["trade_date"])
    root = Path(cache_root) if cache_root is not None else Path(payload.get("cache_root") or DEFAULT_CACHE_ROOT)
    required = required_dataset_names or [
        "trade_cal",
        "daily",
        "daily_basic",
        "stk_limit",
        "moneyflow",
        "limit_list_d",
        "top_list",
        "margin",
        "margin_detail",
        "sw_daily",
        "stock_basic",
        "index_daily_selected",
    ]

    dataset_paths: dict[str, str] = {}
    missing: list[str] = []
    for api_name in required:
        path = _existing_dataset_path(root, api_name, trade_date)
        if path is None:
            missing.append(api_name)
        else:
            dataset_paths[api_name] = str(path)

    manifest_path = payload.get("_manifest_path")
    if not manifest_path:
        manifest_path = str(metadata_path(f"after_close_{req_date}.json", cache_root=cache_root))

    return AfterCloseReportContext(
        requested_date=req_date,
        trade_date=trade_date,
        report_date=_format_dash_date(trade_date),
        manifest_path=manifest_path,
        cache_root=str(root),
        data_ready_for_requested_date=bool(payload.get("data_ready_for_requested_date")),
        is_requested_date_open=bool(payload.get("is_requested_date_open")),
        results=list(payload.get("results") or []),
        notes=list(payload.get("notes") or []),
        dataset_paths=dataset_paths,
        missing_datasets=missing,
    )


def latest_after_close_manifest(*, cache_root: str | Path | None = None) -> dict[str, Any]:
    """Return the newest formal after-close manifest without collecting data."""

    metadata_dir = metadata_path("placeholder", cache_root=cache_root).parent
    manifests = sorted(metadata_dir.glob("after_close_*.json"))
    if not manifests:
        return {}

    latest = manifests[-1]
    import json

    with latest.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    payload["_manifest_path"] = str(latest)
    return payload


def latest_complete_cached_trade_date(*, cache_root: str | Path | None = None) -> str | None:
    """Return the latest cached date where the required daily bundle exists."""

    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    daily_dir = root / "daily"
    daily_basic_dir = root / "daily_basic"
    if not daily_dir.exists() or not daily_basic_dir.exists():
        return None

    daily_dates = {_dataset_date(path) for path in daily_dir.glob("*.*")}
    basic_dates = {_dataset_date(path) for path in daily_basic_dir.glob("*.*")}
    complete = sorted((daily_dates & basic_dates) - {None})
    return complete[-1] if complete else None


def latest_after_close_baseline(*, cache_root: str | Path | None = None) -> LatestAfterCloseBaseline:
    """Resolve the newest formal after-close baseline for pre-open automation."""

    root = str(Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT)
    manifest = latest_after_close_manifest(cache_root=cache_root)
    if manifest:
        return LatestAfterCloseBaseline(
            manifest_path=manifest.get("_manifest_path"),
            requested_date=manifest.get("requested_date"),
            trade_date=manifest.get("trade_date"),
            data_ready_for_requested_date=manifest.get("data_ready_for_requested_date"),
            cache_root=manifest.get("cache_root") or root,
            source="manifest",
        )

    return LatestAfterCloseBaseline(
        manifest_path=None,
        requested_date=None,
        trade_date=latest_complete_cached_trade_date(cache_root=cache_root),
        data_ready_for_requested_date=None,
        cache_root=root,
        source="cache_scan",
    )


def collect_index_daily_selected(
    trade_date: str,
    index_codes: list[str],
    *,
    cache_root: str | Path | None = None,
    force: bool = False,
) -> CollectionResult:
    api_name = "index_daily_selected"
    from stock_selection.data.tushare_cache import dataset_exists, write_dataset

    if dataset_exists(api_name, trade_date, cache_root=cache_root) and not force:
        return CollectionResult(api_name, "skipped", message="cache exists")

    frames: list[pd.DataFrame] = []
    for ts_code in index_codes:
        data = call_api("index_daily", {"ts_code": ts_code, "trade_date": trade_date})
        if isinstance(data, pd.DataFrame) and not data.empty:
            frames.append(data)
    if not frames:
        return CollectionResult(api_name, "empty", rows=0)
    combined = pd.concat(frames, ignore_index=True)
    path = write_dataset(combined, api_name, trade_date, cache_root=cache_root)
    return CollectionResult(api_name, "ok", rows=int(combined.shape[0]), path=str(path))


def _open_dates(requested_date: str, lookback_days: int, *, cache_root: str | Path | None) -> list[str]:
    end = _parse_date(requested_date)
    start = (end - timedelta(days=lookback_days)).strftime("%Y%m%d")
    cal = call_api(
        "trade_cal",
        {"exchange": "SSE", "start_date": start, "end_date": requested_date, "is_open": "1"},
    )
    if cal.empty:
        return []
    return [str(value) for value in cal.sort_values("cal_date")["cal_date"].tolist()]


def _required_ready(
    trade_date: str,
    required_api_names: list[str],
    results: list[CollectionResult],
    *,
    cache_root: str | Path | None,
) -> bool:
    by_name = {result.api_name: result for result in results}
    for api_name in required_api_names:
        result = by_name.get(api_name)
        if result is None or result.status == "failed" or result.status == "empty":
            return False
        try:
            data = read_dataset(api_name, trade_date, cache_root=cache_root)
        except Exception:
            return False
        if data.empty:
            return False
    return True


def _parse_date(value: str) -> date:
    return date(int(value[:4]), int(value[4:6]), int(value[6:8]))


def _dataset_date(path: Path) -> str | None:
    value = path.stem
    return value if len(value) == 8 and value.isdigit() else None


def _enrich_cached_results(
    results: list[CollectionResult],
    trade_date: str,
    *,
    cache_root: str | Path | None,
) -> list[CollectionResult]:
    enriched: list[CollectionResult] = []
    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    for result in results:
        if result.status != "skipped" or result.path:
            enriched.append(result)
            continue
        path = _existing_dataset_path(root, result.api_name, trade_date)
        if path is None:
            enriched.append(result)
            continue
        rows = result.rows
        try:
            rows = int(read_dataset(result.api_name, trade_date, cache_root=cache_root).shape[0])
        except Exception:
            rows = result.rows
        enriched.append(replace(result, rows=rows, path=str(path)))
    return enriched


def _existing_dataset_path(cache_root: Path, api_name: str, trade_date: str) -> Path | None:
    for suffix in [".parquet", ".csv"]:
        path = cache_root / api_name / f"{trade_date}{suffix}"
        if path.exists():
            return path
    return None


def _format_dash_date(value: str) -> str:
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
