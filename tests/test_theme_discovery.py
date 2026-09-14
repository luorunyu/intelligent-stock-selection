from __future__ import annotations

import pandas as pd

from stock_selection.context.theme_discovery import discover_active_themes
from stock_selection.data.tushare_cache import write_dataset


def _write_static_cache(cache_root) -> None:
    write_dataset(
        pd.DataFrame(
            {
                "index_code": ["801010.SI", "801011.SI", "850111.SI", "850112.SI"],
                "industry_name": ["农林牧渔", "林业Ⅱ", "种子", "林木种植"],
                "level": ["L1", "L2", "L3", "L3"],
                "industry_code": ["110000", "110300", "850111", "850112"],
                "is_pub": [1, 1, 1, 1],
                "parent_code": ["0", "110000", "110300", "110300"],
                "src": ["SW2021"] * 4,
            }
        ),
        "index_classify",
        static=True,
        cache_root=cache_root,
    )
    write_dataset(
        pd.DataFrame(
            {
                "l1_code": ["801010.SI"] * 3,
                "l1_name": ["农林牧渔"] * 3,
                "l2_code": ["801011.SI"] * 3,
                "l2_name": ["林业Ⅱ"] * 3,
                "l3_code": ["850111.SI", "850111.SI", "850112.SI"],
                "l3_name": ["种子", "种子", "林木种植"],
                "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
                "name": ["甲", "乙", "丙"],
                "in_date": ["20200101"] * 3,
                "out_date": [None] * 3,
                "is_new": ["Y"] * 3,
            }
        ),
        "index_member_all",
        static=True,
        cache_root=cache_root,
    )


def _write_daily_cache(cache_root, trade_date: str, pct: list[float]) -> None:
    codes = ["000001.SZ", "000002.SZ", "000003.SZ"]
    write_dataset(
        pd.DataFrame({"ts_code": codes, "pct_chg": pct, "amount": [200000.0, 300000.0, 400000.0]}),
        "daily",
        trade_date,
        cache_root=cache_root,
    )
    write_dataset(
        pd.DataFrame({"ts_code": codes, "turnover_rate": [12.0, 11.0, 10.0], "volume_ratio": [2.0, 2.0, 2.0]}),
        "daily_basic",
        trade_date,
        cache_root=cache_root,
    )
    write_dataset(
        pd.DataFrame({"ts_code": codes, "name": ["甲", "乙", "丙"], "industry": ["旧分类"] * 3, "market": ["主板"] * 3}),
        "stock_basic",
        trade_date,
        cache_root=cache_root,
    )


def test_dynamic_discovery_has_no_seed_input_and_exposes_sw_levels(tmp_path) -> None:
    """热点发现只能从行情和 SW2021 归属生成，不应读取预设概念或股票。"""
    _write_static_cache(tmp_path)
    _write_daily_cache(tmp_path, "20260105", [1.0, 0.5, 0.8])
    _write_daily_cache(tmp_path, "20260106", [5.0, 4.0, 3.0])

    result = discover_active_themes(
        "20260106",
        cache_root=tmp_path,
        records_root=tmp_path / "records",
        lookback=2,
        min_theme_score=0.0,
    ).to_dict()

    assert "seeded_theme_matches" not in result
    assert "unseeded_themes" not in result
    assert result["taxonomy"]["mapped_stocks"] == 3
    assert all(stock["sw_l1_name"] and stock["sw_l2_name"] and stock["sw_l3_name"] for stock in result["active_pool"])
    assert result["market_hotspots"]
    assert all("formal_industry" in hotspot for hotspot in result["market_hotspots"])
    assert all(hotspot["concept_label"] is None for hotspot in result["dynamic_clusters"])
    for key in ("market_hotspots", "industry_hotspots", "dynamic_clusters", "themes"):
        for hotspot in result[key]:
            assert all(set(stock) == {"name", "active_reasons"} for stock in hotspot["stocks"])
