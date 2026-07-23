from __future__ import annotations

from pathlib import Path

import pandas as pd

from stock_selection.data.after_close import (
    AfterCloseManifest,
    build_after_close_report_context,
    latest_after_close_baseline,
    prepare_after_close_data,
)
from stock_selection.data.tushare_collector import TushareCollector
from stock_selection.data.tushare_limiter import RateLimiter
from stock_selection.data.tushare_registry import default_params, get_spec, iter_specs


def test_rate_limiter_waits_until_window_has_capacity():
    now = {"value": 0.0}
    sleeps: list[float] = []

    def clock() -> float:
        return now["value"]

    def sleeper(seconds: float) -> None:
        sleeps.append(seconds)
        now["value"] += seconds

    limiter = RateLimiter(max_calls=2, window_seconds=10.0, clock=clock, sleeper=sleeper)

    limiter.acquire()
    limiter.acquire()
    limiter.acquire()

    assert sleeps == [10.0]
    assert limiter.remaining() == 1


def test_registry_defaults_use_trade_date_for_daily_apis():
    daily_names = [spec.name for spec in iter_specs("daily", enabled_only=True)]

    assert "daily" in daily_names
    assert default_params("daily", "20260702") == {"trade_date": "20260702"}
    assert get_spec("stock_basic").cache_mode == "static"


def test_collector_skips_existing_cache(tmp_path: Path, monkeypatch):
    existing = tmp_path / "daily" / "20260702.parquet"
    existing.parent.mkdir(parents=True)
    existing.write_text("cached", encoding="utf-8")
    calls: list[tuple[str, dict[str, str]]] = []

    def caller(api_name: str, params: dict[str, str]) -> pd.DataFrame:
        calls.append((api_name, params))
        return pd.DataFrame({"ts_code": ["000001.SZ"]})

    collector = TushareCollector(cache_root=tmp_path, caller=caller)
    results = collector.collect_daily("20260702", api_names=["daily"])

    assert results[0].status == "skipped"
    assert calls == []


def test_collector_writes_dataset_through_cache_layer(tmp_path: Path, monkeypatch):
    written: list[tuple[str, str | None, bool]] = []

    def fake_write_dataset(data, api_name, trade_date=None, *, static=False, cache_root=None):
        written.append((api_name, trade_date, static))
        return Path(cache_root) / api_name / f"{trade_date}.parquet"

    monkeypatch.setattr("stock_selection.data.tushare_collector.write_dataset", fake_write_dataset)

    def caller(api_name: str, params: dict[str, str]) -> pd.DataFrame:
        assert params == {"trade_date": "20260702"}
        return pd.DataFrame({"ts_code": ["000001.SZ"], "close": [10.0]})

    collector = TushareCollector(cache_root=tmp_path, caller=caller)
    results = collector.collect_daily("20260702", api_names=["daily"])

    assert results[0].status == "ok"
    assert results[0].rows == 1
    assert written == [("daily", "20260702", False)]


def test_prepare_after_close_falls_back_when_requested_daily_not_ready(tmp_path: Path, monkeypatch):
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_call_api(api_name: str, params: dict[str, str]):
        calls.append((api_name, params))
        if api_name == "trade_cal":
            return pd.DataFrame(
                {
                    "cal_date": ["20260702", "20260703"],
                    "is_open": [1, 1],
                }
            )
        if api_name == "index_daily":
            return pd.DataFrame(
                {
                    "ts_code": [params["ts_code"]],
                    "trade_date": [params["trade_date"]],
                    "close": [1.0],
                }
            )
        raise AssertionError(f"unexpected call_api: {api_name}")

    def fake_caller(api_name: str, params: dict[str, str]):
        trade_date = params.get("trade_date") or params.get("start_date")
        if api_name == "trade_cal":
            return pd.DataFrame({"cal_date": [trade_date], "is_open": [1]})
        if trade_date == "20260703" and api_name in {"daily", "daily_basic"}:
            return pd.DataFrame()
        return pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": [trade_date]})

    monkeypatch.setattr("stock_selection.data.after_close.call_api", fake_call_api)
    monkeypatch.setattr(
        "stock_selection.data.after_close.TushareCollector",
        lambda cache_root=None: TushareCollector(cache_root=cache_root, caller=fake_caller),
    )

    manifest = prepare_after_close_data(
        "20260703",
        cache_root=tmp_path,
        required_api_names=["trade_cal", "daily", "daily_basic"],
        optional_api_names=[],
        index_codes=["000001.SH"],
        required_retries=0,
    )

    assert manifest.requested_date == "20260703"
    assert manifest.trade_date == "20260702"
    assert manifest.is_requested_date_open is True
    assert manifest.data_ready_for_requested_date is False
    assert (tmp_path / "daily" / "20260702.parquet").exists()
    assert (tmp_path / "daily_basic" / "20260702.parquet").exists()
    assert (tmp_path / "index_daily_selected" / "20260702.parquet").exists()
    assert (tmp_path / "_metadata" / "after_close_20260703.json").exists()


def test_prepare_after_close_retries_required_api_before_fallback(tmp_path: Path, monkeypatch):
    calls: list[tuple[str, str]] = []
    daily_basic_attempts = {"count": 0}

    def fake_call_api(api_name: str, params: dict[str, str]):
        if api_name == "trade_cal":
            return pd.DataFrame({"cal_date": ["20260716"], "is_open": [1]})
        if api_name == "index_daily":
            return pd.DataFrame(
                {
                    "ts_code": [params["ts_code"]],
                    "trade_date": [params["trade_date"]],
                    "close": [1.0],
                }
            )
        raise AssertionError(f"unexpected call_api: {api_name}")

    def fake_caller(api_name: str, params: dict[str, str]):
        trade_date = params.get("trade_date") or params.get("start_date")
        calls.append((api_name, str(trade_date)))
        if api_name == "trade_cal":
            return pd.DataFrame({"cal_date": [trade_date], "is_open": [1]})
        if api_name == "daily_basic":
            daily_basic_attempts["count"] += 1
            if daily_basic_attempts["count"] == 1:
                raise TimeoutError("read timeout")
        return pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": [trade_date]})

    monkeypatch.setattr("stock_selection.data.after_close.call_api", fake_call_api)
    monkeypatch.setattr(
        "stock_selection.data.after_close.TushareCollector",
        lambda cache_root=None: TushareCollector(cache_root=cache_root, caller=fake_caller),
    )

    manifest = prepare_after_close_data(
        "20260716",
        cache_root=tmp_path,
        required_api_names=["trade_cal", "daily", "daily_basic"],
        optional_api_names=[],
        index_codes=["000001.SH"],
        required_retries=1,
        required_retry_wait_seconds=0,
    )

    assert manifest.trade_date == "20260716"
    assert manifest.data_ready_for_requested_date is True
    assert (tmp_path / "daily_basic" / "20260716.parquet").exists()
    assert daily_basic_attempts["count"] == 2
    assert manifest.readiness
    assert manifest.readiness["required_ready"] is True
    assert manifest.readiness["final_missing_required"] == []
    assert len(manifest.readiness["attempts"]) == 2


def test_prepare_after_close_rechecks_cache_before_required_retry(tmp_path: Path, monkeypatch):
    daily_basic_attempts = {"count": 0}

    def fake_call_api(api_name: str, params: dict[str, str]):
        if api_name == "trade_cal":
            return pd.DataFrame({"cal_date": ["20260716"], "is_open": [1]})
        if api_name == "index_daily":
            return pd.DataFrame(
                {
                    "ts_code": [params["ts_code"]],
                    "trade_date": [params["trade_date"]],
                    "close": [1.0],
                }
            )
        raise AssertionError(f"unexpected call_api: {api_name}")

    def fake_caller(api_name: str, params: dict[str, str]):
        trade_date = params.get("trade_date") or params.get("start_date")
        if api_name == "trade_cal":
            return pd.DataFrame({"cal_date": [trade_date], "is_open": [1]})
        if api_name == "daily_basic":
            daily_basic_attempts["count"] += 1
            path = tmp_path / "daily_basic" / f"{trade_date}.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": [trade_date]}).to_parquet(path, index=False)
            raise TimeoutError("read timeout after concurrent cache write")
        return pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": [trade_date]})

    monkeypatch.setattr("stock_selection.data.after_close.call_api", fake_call_api)
    monkeypatch.setattr(
        "stock_selection.data.after_close.TushareCollector",
        lambda cache_root=None: TushareCollector(cache_root=cache_root, caller=fake_caller),
    )

    manifest = prepare_after_close_data(
        "20260716",
        cache_root=tmp_path,
        required_api_names=["trade_cal", "daily", "daily_basic"],
        optional_api_names=[],
        index_codes=["000001.SH"],
        required_retries=2,
        required_retry_wait_seconds=0,
    )

    assert manifest.trade_date == "20260716"
    assert manifest.data_ready_for_requested_date is True
    assert daily_basic_attempts["count"] == 1
    assert manifest.readiness
    assert manifest.readiness["required_ready"] is True
    assert manifest.readiness["attempts"][0]["ready"] is True


def test_latest_after_close_baseline_reads_newest_manifest(tmp_path: Path):
    metadata = tmp_path / "_metadata"
    metadata.mkdir()
    (metadata / "after_close_20260702.json").write_text(
        '{"requested_date":"20260702","trade_date":"20260702","data_ready_for_requested_date":true}',
        encoding="utf-8",
    )
    (metadata / "after_close_20260703.json").write_text(
        '{"requested_date":"20260703","trade_date":"20260702","data_ready_for_requested_date":false}',
        encoding="utf-8",
    )

    baseline = latest_after_close_baseline(cache_root=tmp_path)

    assert baseline.source == "manifest"
    assert baseline.requested_date == "20260703"
    assert baseline.trade_date == "20260702"
    assert baseline.data_ready_for_requested_date is False
    assert baseline.manifest_path == str(metadata / "after_close_20260703.json")


def test_latest_after_close_baseline_scans_cache_without_manifest(tmp_path: Path):
    for api_name in ["daily", "daily_basic"]:
        for trade_date in ["20260701", "20260702"]:
            path = tmp_path / api_name / f"{trade_date}.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("cached", encoding="utf-8")

    baseline = latest_after_close_baseline(cache_root=tmp_path)

    assert baseline.source == "cache_scan"
    assert baseline.trade_date == "20260702"
    assert baseline.manifest_path is None


def test_build_after_close_report_context_lists_formal_cache_paths(tmp_path: Path):
    for api_name in ["trade_cal", "daily", "daily_basic"]:
        path = tmp_path / api_name / "20260702.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("cached", encoding="utf-8")

    manifest = AfterCloseManifest(
        requested_date="20260703",
        trade_date="20260702",
        is_requested_date_open=True,
        data_ready_for_requested_date=False,
        cache_root=str(tmp_path),
        results=[],
        notes=["20260703: required daily bundle not ready"],
    )

    context = build_after_close_report_context(
        manifest,
        required_dataset_names=["trade_cal", "daily", "daily_basic", "moneyflow"],
    )

    assert context.requested_date == "20260703"
    assert context.trade_date == "20260702"
    assert context.report_date == "2026-07-02"
    assert context.dataset_paths["daily"] == str(tmp_path / "daily" / "20260702.parquet")
    assert context.missing_datasets == ["moneyflow"]
