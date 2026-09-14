from __future__ import annotations

import pandas as pd

from stock_selection.data.industry_taxonomy import load_sw_industry_membership
from stock_selection.data.tushare_cache import read_json
from stock_selection.data.tushare_collector import TushareCollector
from stock_selection.data.tushare_registry import iter_group, iter_specs


def test_registry_contains_only_current_branch_apis() -> None:
    assert [spec.name for spec in iter_specs("daily")] == ["trade_cal", "daily", "sw_daily"]
    assert [spec.name for spec in iter_specs("static")] == [
        "stock_basic",
        "index_classify",
        "index_member_all",
    ]
    assert [spec.name for spec in iter_specs("on_demand")] == ["pro_bar"]
    assert [spec.name for spec in iter_group("market_daily")] == ["trade_cal", "daily", "sw_daily"]


def test_collect_market_daily_writes_complete_manifest(tmp_path) -> None:
    def caller(api_name, params):
        trade_date = params.get("trade_date") or params.get("start_date")
        if api_name == "trade_cal":
            return pd.DataFrame({"exchange": ["SSE"], "cal_date": [trade_date], "is_open": [1]})
        if api_name == "daily":
            return pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": [trade_date],
                    "open": [10.0],
                    "high": [10.5],
                    "low": [9.8],
                    "close": [10.3],
                    "vol": [1000.0],
                    "amount": [10000.0],
                }
            )
        if api_name == "sw_daily":
            return pd.DataFrame(
                {
                    "ts_code": ["850111.SI"],
                    "trade_date": [trade_date],
                    "open": [1000.0],
                    "high": [1010.0],
                    "low": [995.0],
                    "close": [1008.0],
                }
            )
        raise AssertionError(api_name)

    collector = TushareCollector(cache_root=tmp_path, caller=caller)
    results = collector.collect_market_daily("20260106", available_only=False)
    manifest = read_json("market_daily/20260106.json", cache_root=tmp_path)

    assert all(result.status == "ok" for result in results)
    assert manifest["complete"] is True
    assert manifest["datasets"] == {
        "trade_cal": True,
        "daily": True,
        "sw_daily": True,
    }


def test_collect_sw_static_rebuilds_membership(tmp_path) -> None:
    def caller(api_name, params):
        if api_name == "stock_basic":
            return pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "symbol": ["000001"],
                    "name": ["甲"],
                    "market": ["主板"],
                    "list_date": ["19910403"],
                }
            )
        if api_name == "index_classify":
            return pd.DataFrame(
                {
                    "index_code": ["801010.SI", "801011.SI", "850111.SI"],
                    "industry_name": ["农林牧渔", "林业Ⅱ", "种子"],
                    "level": ["L1", "L2", "L3"],
                    "industry_code": ["110000", "110300", "850111"],
                    "parent_code": ["0", "110000", "110300"],
                    "src": ["SW2021"] * 3,
                }
            )
        if api_name == "index_member_all":
            return pd.DataFrame(
                {
                    "l1_code": ["801010.SI"],
                    "l1_name": ["农林牧渔"],
                    "l2_code": ["801011.SI"],
                    "l2_name": ["林业Ⅱ"],
                    "l3_code": ["850111.SI"],
                    "l3_name": ["种子"],
                    "ts_code": ["000001.SZ"],
                }
            )
        raise AssertionError(api_name)

    collector = TushareCollector(cache_root=tmp_path, caller=caller)
    results = collector.collect_sw_static(available_only=False)
    membership = load_sw_industry_membership(cache_root=tmp_path)

    assert all(result.status == "ok" for result in results)
    assert membership.data.loc[0, "sw_l3_name"] == "种子"
    assert membership.summary()["incomplete_rows"] == 0
