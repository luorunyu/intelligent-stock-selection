from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from stock_selection.data.tushare_cache import read_json, write_json
from stock_selection.data.tushare_client import call_api, call_pro_bar
from stock_selection.data.tushare_registry import default_params, iter_specs


PERMISSIONS_FILE = "available_apis.json"


def load_available_apis(*, cache_root: str | Path | None = None) -> dict[str, Any]:
    return read_json(PERMISSIONS_FILE, cache_root=cache_root)


def probe_available_apis(
    *,
    trade_date: str,
    api_names: list[str] | None = None,
    cache_root: str | Path | None = None,
) -> dict[str, Any]:
    names = api_names or [spec.name for spec in iter_specs(enabled_only=True)]
    checked_at = datetime.now().isoformat(timespec="seconds")
    results: dict[str, Any] = {}

    for api_name in names:
        try:
            params = default_params(api_name, trade_date)
            if api_name == "pro_bar":
                data = call_pro_bar(params, max_retries=0)
            else:
                data = call_api(api_name, params, max_retries=0)
            rows = int(getattr(data, "shape", [0])[0]) if hasattr(data, "shape") else None
            results[api_name] = {
                "available": True,
                "last_checked": checked_at,
                "sample_rows": rows,
            }
        except Exception as exc:
            results[api_name] = {
                "available": False,
                "last_checked": checked_at,
                "reason": str(exc),
            }

    write_json(PERMISSIONS_FILE, results, cache_root=cache_root)
    return results

