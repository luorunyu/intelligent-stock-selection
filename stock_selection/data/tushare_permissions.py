"""探测并缓存当前 Tushare 令牌可用的接口，供批量采集跳过无权限项。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from stock_selection.data.tushare_cache import read_json, write_json
from stock_selection.data.tushare_client import call_api, call_pro_bar
from stock_selection.data.tushare_registry import get_spec, iter_specs, probe_params


PERMISSIONS_FILE = "available_apis.json"


def load_available_apis(*, cache_root: str | Path | None = None) -> dict[str, Any]:
    """读取最近一次权限探测结果；首次运行时返回空字典。"""
    return read_json(PERMISSIONS_FILE, cache_root=cache_root)


def probe_available_apis(
    *,
    trade_date: str,
    api_names: list[str] | None = None,
    cache_root: str | Path | None = None,
) -> dict[str, Any]:
    """用最小参数逐接口试调，记录可用性、样本行数或失败原因。"""
    names = api_names or [spec.name for spec in iter_specs(enabled_only=True)]
    checked_at = datetime.now().isoformat(timespec="seconds")
    results: dict[str, Any] = {}

    # 单个接口失败不应阻断整次探测；结果会成为后续采集的可用性过滤器。
    for api_name in names:
        try:
            params = probe_params(api_name, trade_date)
            if api_name == "pro_bar":
                data = call_pro_bar(params, max_retries=0)
            else:
                data = call_api(api_name, params, max_retries=0)
            rows = int(getattr(data, "shape", [0])[0]) if hasattr(data, "shape") else None
            results[api_name] = {
                "available": True,
                "last_checked": checked_at,
                "sample_rows": rows,
                "probe_only": get_spec(api_name).cache_mode != "trade_date",
                "full_collection_required": get_spec(api_name).collection_group == "sw_static",
            }
        except Exception as exc:
            results[api_name] = {
                "available": False,
                "last_checked": checked_at,
                "reason": str(exc),
                "probe_only": get_spec(api_name).cache_mode != "trade_date",
                "full_collection_required": get_spec(api_name).collection_group == "sw_static",
            }

    write_json(PERMISSIONS_FILE, results, cache_root=cache_root)
    return results
