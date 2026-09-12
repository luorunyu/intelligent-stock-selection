"""盘后正式数据管线：确认交易日、采集完整数据包、写 manifest 并提供报告上下文。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from pathlib import Path
import time
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
    """一次盘后数据准备的可落盘清单，记录请求日、实际交易日和接口结果。"""
    requested_date: str
    trade_date: str
    is_requested_date_open: bool
    data_ready_for_requested_date: bool
    cache_root: str
    results: list[dict[str, Any]]
    notes: list[str]
    readiness: dict[str, Any] | None = None


@dataclass(frozen=True)
class LatestAfterCloseBaseline:
    """盘前工作流只读使用的最近一次正式盘后基线。"""

    manifest_path: str | None
    requested_date: str | None
    trade_date: str | None
    data_ready_for_requested_date: bool | None
    cache_root: str
    source: str


@dataclass(frozen=True)
class AfterCloseReportContext:
    """报告阶段使用的数据路径、缺口与日期回退信息。"""
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
    readiness: dict[str, Any] | None = None


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
    required_retries: int = 3,
    required_retry_wait_seconds: float = 60.0,
) -> AfterCloseManifest:
    """采集最近完整盘后数据包；请求日不完整时回退到最近可用交易日。"""

    req_date = requested_date or date.today().strftime("%Y%m%d")
    collector = TushareCollector(cache_root=cache_root)
    open_dates = _open_dates(req_date, lookback_days, cache_root=cache_root)
    if not open_dates:
        raise RuntimeError(f"no open trading days found before {req_date}")

    required = list(DEFAULT_REQUIRED_APIS if required_api_names is None else required_api_names)
    optional = list(DEFAULT_OPTIONAL_APIS if optional_api_names is None else optional_api_names)
    idx_codes = list(DEFAULT_INDEX_CODES if index_codes is None else index_codes)
    notes: list[str] = []
    selected_date: str | None = None
    selected_results: list[CollectionResult] = []
    selected_readiness: dict[str, Any] | None = None

    # 从请求日向前尝试；只有 required 接口全部非空落库的日期才可用于正式报告。
    for candidate in reversed(open_dates):
        retries_for_candidate = required_retries if candidate == req_date else 0
        results, readiness = _collect_candidate_with_required_retry(
            collector,
            candidate,
            api_names=required + [api for api in optional if api not in required],
            required_api_names=required,
            force=force,
            available_only=available_only,
            cache_root=cache_root,
            required_retries=retries_for_candidate,
            required_retry_wait_seconds=required_retry_wait_seconds,
        )
        if _required_ready(candidate, required, results, cache_root=cache_root):
            selected_date = candidate
            selected_results = results
            selected_readiness = readiness
            break
        missing_required = readiness.get("final_missing_required") or []
        if missing_required:
            notes.append(f"{candidate}: required daily bundle not ready ({', '.join(missing_required)})")
        else:
            notes.append(f"{candidate}: required daily bundle not ready")

    if selected_date is None:
        raise RuntimeError(f"no complete daily/daily_basic bundle found before {req_date}")

    # 个股横截面完整后，再补主要宽基指数，供市场环境门控使用。
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
        readiness=selected_readiness,
    )
    # 清单按“请求日期”命名，保留当天是否回退和最终实际交易日的证据。
    write_json(f"after_close_{req_date}.json", asdict(manifest), cache_root=cache_root)
    return manifest


def build_after_close_report_context(
    manifest: AfterCloseManifest | dict[str, Any],
    *,
    cache_root: str | Path | None = None,
    required_dataset_names: list[str] | None = None,
) -> AfterCloseReportContext:
    """从正式 manifest 构建报告上下文，并逐项检查所需缓存是否存在。"""

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
    # 报告生成器只能读取这里列出的正式路径；缺失项必须显式暴露而非猜测数据。
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
        readiness=payload.get("readiness"),
    )


def latest_after_close_manifest(*, cache_root: str | Path | None = None) -> dict[str, Any]:
    """不采集数据，只读取最新盘后 manifest，并附带其真实文件路径。"""

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
    """扫描 daily 与 daily_basic 的共同日期，返回最近完整缓存日。"""

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
    """为盘前任务定位最新正式基线；缺 manifest 时降级为缓存扫描。"""

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
    """采集指定交易日的主要宽基指数，并合并为单一正式缓存表。"""
    api_name = "index_daily_selected"
    from stock_selection.data.tushare_cache import dataset_exists, write_dataset

    if dataset_exists(api_name, trade_date, cache_root=cache_root) and not force:
        return CollectionResult(api_name, "skipped", message="cache exists")

    frames: list[pd.DataFrame] = []
    # 指数接口按代码返回，合并后可避免报告阶段逐指数联网查询。
    for ts_code in index_codes:
        data = call_api("index_daily", {"ts_code": ts_code, "trade_date": trade_date})
        if isinstance(data, pd.DataFrame) and not data.empty:
            frames.append(data)
    if not frames:
        return CollectionResult(api_name, "empty", rows=0)
    combined = pd.concat(frames, ignore_index=True)
    path = write_dataset(combined, api_name, trade_date, cache_root=cache_root)
    return CollectionResult(api_name, "ok", rows=int(combined.shape[0]), path=str(path))


def _collect_candidate_with_required_retry(
    collector: TushareCollector,
    trade_date: str,
    *,
    api_names: list[str],
    required_api_names: list[str],
    force: bool,
    available_only: bool,
    cache_root: str | Path | None,
    required_retries: int,
    required_retry_wait_seconds: float,
) -> tuple[list[CollectionResult], dict[str, Any]]:
    """采集一个候选日并只对缺失必需接口重试，返回结果和就绪性证据。"""
    results = collector.collect_daily(
        trade_date,
        api_names=api_names,
        force=force,
        available_only=available_only,
    )
    by_name = {result.api_name: result for result in results}
    attempts: list[dict[str, Any]] = []
    waited_seconds = 0.0

    attempts.append(
        _readiness_attempt(
            attempt=0,
            trade_date=trade_date,
            required_api_names=required_api_names,
            results=list(by_name.values()),
            cache_root=cache_root,
        )
    )

    # 首次采集后重新读盘确认；盘后数据延迟时才等待并补采缺失接口。
    for attempt in range(1, max(required_retries, 0) + 1):
        missing = _missing_required_apis(trade_date, required_api_names, cache_root=cache_root)
        if not missing:
            break
        if required_retry_wait_seconds > 0:
            time.sleep(required_retry_wait_seconds)
            waited_seconds += required_retry_wait_seconds

        missing = _missing_required_apis(trade_date, required_api_names, cache_root=cache_root)
        if missing:
            retry_results = collector.collect_missing_daily(
                trade_date,
                api_names=missing,
                force=force,
                available_only=available_only,
            )
            by_name.update({result.api_name: result for result in retry_results})

        attempts.append(
            _readiness_attempt(
                attempt=attempt,
                trade_date=trade_date,
                required_api_names=required_api_names,
                results=list(by_name.values()),
                cache_root=cache_root,
            )
        )

    final_missing = _missing_required_apis(trade_date, required_api_names, cache_root=cache_root)
    ordered_results = [by_name[name] for name in api_names if name in by_name]
    readiness = {
        "trade_date": trade_date,
        "required_api_names": required_api_names,
        "required_ready": not final_missing,
        "required_retries": max(required_retries, 0),
        "required_retry_wait_seconds": required_retry_wait_seconds,
        "waited_seconds": waited_seconds,
        "cache_rechecked": len(attempts) > 1,
        "attempts": attempts,
        "final_missing_required": final_missing,
        "fallback_reason": None if not final_missing else f"required APIs still missing: {', '.join(final_missing)}",
    }
    return ordered_results, readiness


def _readiness_attempt(
    *,
    attempt: int,
    trade_date: str,
    required_api_names: list[str],
    results: list[CollectionResult],
    cache_root: str | Path | None,
) -> dict[str, Any]:
    """将某次就绪性检查整理为可写入 manifest 的审计记录。"""
    return {
        "attempt": attempt,
        "ready": not _missing_required_apis(trade_date, required_api_names, cache_root=cache_root),
        "missing_required": _missing_required_apis(trade_date, required_api_names, cache_root=cache_root),
        "results": [
            {
                "api_name": result.api_name,
                "status": result.status,
                "rows": result.rows,
                "message": result.message,
            }
            for result in results
            if result.api_name in required_api_names
        ],
    }


def _missing_required_apis(
    trade_date: str,
    required_api_names: list[str],
    *,
    cache_root: str | Path | None,
) -> list[str]:
    """读取必需缓存并返回缺失或空表的接口名。"""
    missing: list[str] = []
    for api_name in required_api_names:
        try:
            data = read_dataset(api_name, trade_date, cache_root=cache_root)
        except Exception:
            missing.append(api_name)
            continue
        if data.empty:
            missing.append(api_name)
    return missing


def _open_dates(requested_date: str, lookback_days: int, *, cache_root: str | Path | None) -> list[str]:
    """从交易所日历取得请求日前的开市日，供盘后回退逐日尝试。"""
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
    """判断候选交易日的必需数据集是否均已可读且非空。"""
    return not _missing_required_apis(trade_date, required_api_names, cache_root=cache_root)


def _parse_date(value: str) -> date:
    """把 YYYYMMDD 转换为日期对象，供回溯窗口计算。"""
    return date(int(value[:4]), int(value[4:6]), int(value[6:8]))


def _dataset_date(path: Path) -> str | None:
    """从缓存文件名提取合法交易日。"""
    value = path.stem
    return value if len(value) == 8 and value.isdigit() else None


def _enrich_cached_results(
    results: list[CollectionResult],
    trade_date: str,
    *,
    cache_root: str | Path | None,
) -> list[CollectionResult]:
    """为“缓存已存在”的结果补充真实路径和行数，方便 manifest 审计。"""
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
    """按 Parquet 优先、CSV 兼容的顺序寻找某接口缓存文件。"""
    for suffix in [".parquet", ".csv"]:
        path = cache_root / api_name / f"{trade_date}{suffix}"
        if path.exists():
            return path
    return None


def _format_dash_date(value: str) -> str:
    """将 YYYYMMDD 格式化为报告使用的 YYYY-MM-DD。"""
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
