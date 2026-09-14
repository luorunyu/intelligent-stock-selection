"""将 Tushare 调用统一编排为可缓存、可审计、可局部失败的采集任务。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from stock_selection.data.industry_taxonomy import rebuild_sw_industry_membership
from stock_selection.data.tushare_cache import (
    append_jsonl,
    dataset_exists,
    read_dataset,
    write_dataset,
    write_json,
)
from stock_selection.data.tushare_client import TushareCallResult, call_api, call_pro_bar
from stock_selection.data.tushare_registry import default_params, get_spec, iter_group, iter_specs


@dataclass(frozen=True)
class CollectionResult:
    """单个接口采集后的状态、行数、缓存路径和错误摘要。"""
    api_name: str
    status: str
    rows: int | None = None
    path: str | None = None
    message: str | None = None


class TushareCollector:
    """按注册表采集日频、静态和按需数据，并写入正式缓存及调用日志。"""
    def __init__(
        self,
        *,
        pro: Any | None = None,
        cache_root: str | Path | None = None,
        caller: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> None:
        """可注入 Pro 客户端或调用函数，便于测试和特殊环境复用。"""
        self.pro = pro
        self.cache_root = cache_root
        self._caller = caller

    def collect_market_daily(
        self,
        trade_date: str | None = None,
        *,
        force: bool = False,
        available_only: bool = True,
    ) -> list[CollectionResult]:
        """采集个股与申万行业共同需要的完整日频数据包。"""
        resolved_date = trade_date or self.latest_trade_date()
        api_names = [spec.name for spec in iter_group("market_daily")]
        results = self.collect_daily(
            resolved_date,
            api_names=api_names,
            force=force,
            available_only=available_only,
        )
        readiness = {
            api_name: self._dataset_ready(api_name, resolved_date)
            for api_name in api_names
        }
        write_json(
            f"market_daily/{resolved_date}.json",
            {
                "trade_date": resolved_date,
                "datasets": readiness,
                "complete": all(readiness.values()),
                "results": [result.__dict__ for result in results],
            },
            cache_root=self.cache_root,
        )
        return results

    def collect_sw_static(
        self,
        *,
        force: bool = False,
        available_only: bool = True,
    ) -> list[CollectionResult]:
        """采集 SW2021 静态表并重建股票三级行业映射。"""
        api_names = [spec.name for spec in iter_group("sw_static")]
        results = self.collect_static(
            api_names=api_names,
            force=force,
            available_only=available_only,
        )
        try:
            membership = rebuild_sw_industry_membership(cache_root=self.cache_root)
            summary = membership.summary()
            results.append(
                CollectionResult(
                    "sw_industry_membership",
                    "ok",
                    rows=int(summary["rows"]),
                    message="rebuilt from index_classify and index_member_all",
                )
            )
            write_json("sw_static.json", summary, cache_root=self.cache_root)
        except Exception as exc:
            results.append(CollectionResult("sw_industry_membership", "failed", message=str(exc)))
        return results

    def collect_daily(
        self,
        trade_date: str | None = None,
        *,
        api_names: list[str] | None = None,
        force: bool = False,
        available_only: bool = True,
    ) -> list[CollectionResult]:
        """采集一个交易日的接口集合；已有缓存默认跳过，结果写入运行日志。"""
        resolved_date = trade_date or self.latest_trade_date()
        names = api_names or [spec.name for spec in iter_specs("daily", enabled_only=True)]
        available = self._available_api_names() if available_only else None
        results: list[CollectionResult] = []

        # 先按已探测权限过滤，再按缓存状态过滤，减少无效的网络请求。
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
        """只补采尚未落库的指定日频接口，适合盘后就绪性重试。"""

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
        """采集日频数据包；任一必需接口失败或返回空表时立即报错。"""

        names = required_api_names + list(optional_api_names or [])
        results = self.collect_daily(
            trade_date,
            api_names=names,
            force=force,
            available_only=available_only,
        )
        required = set(required_api_names)
        # 可选接口允许失败；只有必需接口决定该交易日是否可用于正式报告。
        for result in results:
            if result.api_name not in required:
                continue
            if result.status == "failed":
                raise RuntimeError(f"required Tushare API failed: {result.api_name}: {result.message}")
            if result.status == "empty" or (result.status == "ok" and result.rows == 0):
                raise RuntimeError(f"required Tushare API returned 0 rows: {result.api_name}")
        return results

    def collect_static(
        self,
        *,
        api_names: list[str] | None = None,
        force: bool = False,
        available_only: bool = True,
    ) -> list[CollectionResult]:
        """采集低频静态表，如股票池、指数和行业分类。"""
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
        """采集单次按需数据；提供 cache_key 时可复用正式缓存。"""
        get_spec(api_name)
        if not cache_key:
            return self._collect_one(api_name, params)
        if dataset_exists(api_name, cache_key, cache_root=self.cache_root) and not force:
            return CollectionResult(api_name, "skipped", message="cache exists")
        return self._collect_one(api_name, params, trade_date=cache_key)

    def latest_trade_date(self) -> str:
        """查询近 45 天交易日历，返回最近已开市日期。"""
        today = date.today()
        start = (today - timedelta(days=45)).strftime("%Y%m%d")
        end = today.strftime("%Y%m%d")
        # 交易日历是所有日频采集的锚点，不能用自然日直接代替。
        cal = self._call("trade_cal", {"exchange": "SSE", "start_date": start, "end_date": end, "is_open": "1"})
        if cal.empty:
            raise RuntimeError("trade_cal returned no open trading days")
        return str(cal.sort_values("cal_date").iloc[-1]["cal_date"])

    def trade_dates(self, start_date: str, end_date: str) -> list[str]:
        """返回指定区间内的开市日期。"""
        cal = self._call(
            "trade_cal",
            {
                "exchange": "SSE",
                "start_date": start_date,
                "end_date": end_date,
                "is_open": "1",
            },
        )
        if cal.empty:
            return []
        return [str(value) for value in cal.sort_values("cal_date")["cal_date"].tolist()]

    def _collect_one(
        self,
        api_name: str,
        params: dict[str, Any],
        *,
        trade_date: str | None = None,
        static: bool = False,
    ) -> CollectionResult:
        """执行一次接口调用；非空 DataFrame 写缓存，异常写错误审计日志。"""
        try:
            data = self._call(api_name, params)
            path = None
            # 只有表格结果才可进入本项目的 Parquet 缓存；其他结果仅返回调用状态。
            if isinstance(data, pd.DataFrame):
                if data.empty:
                    return CollectionResult(api_name, "empty", rows=0)
                self._validate_fields(api_name, data)
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
        """调用注入函数或默认客户端封装，并为成功调用追加审计记录。"""
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
        """读取权限探测结果；尚未探测时返回空值表示不做预过滤。"""
        from stock_selection.data.tushare_permissions import load_available_apis

        available = load_available_apis(cache_root=self.cache_root)
        if not available:
            return None
        return {name for name, info in available.items() if info.get("available")}

    def _dataset_ready(self, api_name: str, trade_date: str) -> bool:
        try:
            data = read_dataset(api_name, trade_date, cache_root=self.cache_root)
            self._validate_fields(api_name, data)
        except Exception:
            return False
        return not data.empty

    @staticmethod
    def _validate_fields(api_name: str, data: pd.DataFrame) -> None:
        required = get_spec(api_name).required_fields
        missing = [field for field in required if field not in data.columns]
        if missing:
            raise ValueError(f"{api_name} missing required fields: {', '.join(missing)}")
