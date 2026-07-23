from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from stock_selection.data.tushare_cache import append_jsonl, dataset_exists, write_dataset
from stock_selection.data.tushare_client import TushareCallResult, call_api, call_pro_bar
from stock_selection.data.tushare_registry import default_params, get_spec, iter_specs


@dataclass(frozen=True)
class CollectionResult:
    api_name: str
    status: str
    rows: int | None = None
    path: str | None = None
    message: str | None = None


class TushareCollector:
    def __init__(
        self,
        *,
        pro: Any | None = None,
        cache_root: str | Path | None = None,
        caller: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> None:
        self.pro = pro
        self.cache_root = cache_root
        self._caller = caller

    def collect_daily(
        self,
        trade_date: str | None = None,
        *,
        api_names: list[str] | None = None,
        force: bool = False,
        available_only: bool = True,
    ) -> list[CollectionResult]:
        resolved_date = trade_date or self.latest_trade_date()
        names = api_names or [spec.name for spec in iter_specs("daily", enabled_only=True)]
        available = self._available_api_names() if available_only else None
        results: list[CollectionResult] = []

        for api_name in names:
            if available is not None and api_name not in available:
                results.append(CollectionResult(api_name, "skipped", message="not available for current token"))
                continue

            if dataset_exists(api_name, resolved_date, cache_root=self.cache_root) and not force:
                results.append(CollectionResult(api_name, "skipped", message="cache exists"))
                continue

            params = default_params(api_name, resolved_date)
            results.append(self._collect_one(api_name, params, trade_date=resolved_date))

        append_jsonl(
            "runs.jsonl",
            {
                "kind": "daily",
                "trade_date": resolved_date,
                "force": force,
                "results": [result.__dict__ for result in results],
            },
            cache_root=self.cache_root,
        )
        return results

    def collect_missing_daily(
        self,
        trade_date: str,
        *,
        api_names: list[str],
        force: bool = False,
        available_only: bool = True,
    ) -> list[CollectionResult]:
        """Collect only requested daily APIs that are not already cached."""

        missing = [
            api_name
            for api_name in api_names
            if force or not dataset_exists(api_name, trade_date, cache_root=self.cache_root)
        ]
        if not missing:
            return [
                CollectionResult(api_name, "skipped", message="cache exists")
                for api_name in api_names
            ]
        return self.collect_daily(
            trade_date,
            api_names=missing,
            force=force,
            available_only=available_only,
        )

    def collect_daily_required(
        self,
        trade_date: str,
        *,
        required_api_names: list[str],
        optional_api_names: list[str] | None = None,
        force: bool = False,
        available_only: bool = True,
    ) -> list[CollectionResult]:
        """Collect a daily bundle and fail if required APIs are unavailable or empty."""

        names = required_api_names + list(optional_api_names or [])
        results = self.collect_daily(
            trade_date,
            api_names=names,
            force=force,
            available_only=available_only,
        )
        required = set(required_api_names)
        for result in results:
            if result.api_name not in required:
                continue
            if result.status == "failed":
                raise RuntimeError(f"required Tushare API failed: {result.api_name}: {result.message}")
            if result.status == "ok" and result.rows == 0:
                raise RuntimeError(f"required Tushare API returned 0 rows: {result.api_name}")
        return results

    def collect_static(
        self,
        *,
        api_names: list[str] | None = None,
        force: bool = False,
        available_only: bool = True,
    ) -> list[CollectionResult]:
        names = api_names or [spec.name for spec in iter_specs("static", enabled_only=True)]
        available = self._available_api_names() if available_only else None
        results: list[CollectionResult] = []

        for api_name in names:
            if available is not None and api_name not in available:
                results.append(CollectionResult(api_name, "skipped", message="not available for current token"))
                continue

            if dataset_exists(api_name, static=True, cache_root=self.cache_root) and not force:
                results.append(CollectionResult(api_name, "skipped", message="cache exists"))
                continue

            params = default_params(api_name)
            results.append(self._collect_one(api_name, params, static=True))

        append_jsonl(
            "runs.jsonl",
            {
                "kind": "static",
                "force": force,
                "results": [result.__dict__ for result in results],
            },
            cache_root=self.cache_root,
        )
        return results

    def collect_on_demand(
        self,
        api_name: str,
        params: dict[str, Any],
        *,
        cache_key: str | None = None,
        force: bool = False,
    ) -> CollectionResult:
        get_spec(api_name)
        if not cache_key:
            return self._collect_one(api_name, params)
        if dataset_exists(api_name, cache_key, cache_root=self.cache_root) and not force:
            return CollectionResult(api_name, "skipped", message="cache exists")
        return self._collect_one(api_name, params, trade_date=cache_key)

    def latest_trade_date(self) -> str:
        today = date.today()
        start = (today - timedelta(days=45)).strftime("%Y%m%d")
        end = today.strftime("%Y%m%d")
        cal = self._call("trade_cal", {"exchange": "SSE", "start_date": start, "end_date": end, "is_open": "1"})
        if cal.empty:
            raise RuntimeError("trade_cal returned no open trading days")
        return str(cal.sort_values("cal_date").iloc[-1]["cal_date"])

    def _collect_one(
        self,
        api_name: str,
        params: dict[str, Any],
        *,
        trade_date: str | None = None,
        static: bool = False,
    ) -> CollectionResult:
        try:
            data = self._call(api_name, params)
            path = None
            if isinstance(data, pd.DataFrame):
                if data.empty:
                    return CollectionResult(api_name, "empty", rows=0)
                path = write_dataset(data, api_name, trade_date, static=static, cache_root=self.cache_root)
                rows = int(data.shape[0])
            else:
                rows = None

            return CollectionResult(api_name, "ok", rows=rows, path=str(path) if path else None)
        except Exception as exc:
            append_jsonl(
                "errors.jsonl",
                {"api_name": api_name, "params": params, "error": str(exc)},
                cache_root=self.cache_root,
            )
            return CollectionResult(api_name, "failed", message=str(exc))

    def _call(self, api_name: str, params: dict[str, Any]) -> Any:
        if self._caller is not None:
            return self._caller(api_name, params)

        def log_call(result: TushareCallResult) -> None:
            append_jsonl(
                "calls.jsonl",
                {
                    "api_name": result.api_name,
                    "params": result.params,
                    "rows": result.rows,
                    "elapsed_seconds": round(result.elapsed_seconds, 3),
                    "attempts": result.attempts,
                },
                cache_root=self.cache_root,
            )

        if api_name == "pro_bar":
            return call_pro_bar(params, pro=self.pro, on_result=log_call)
        return call_api(api_name, params, pro=self.pro, on_result=log_call)

    def _available_api_names(self) -> set[str] | None:
        from stock_selection.data.tushare_permissions import load_available_apis

        available = load_available_apis(cache_root=self.cache_root)
        if not available:
            return None
        return {name for name, info in available.items() if info.get("available")}
