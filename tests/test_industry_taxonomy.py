from __future__ import annotations

import pandas as pd

from stock_selection.data.industry_taxonomy import (
    SW_LEVEL_COLUMNS,
    attach_sw_industry,
    build_sw_industry_stock_map,
    get_sw_industry_stock_codes,
)


def test_attach_sw_industry_keeps_official_three_levels() -> None:
    """股票横截面必须保留完整申万层级，缺失映射不得伪造为基础行业。"""
    daily = pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ"], "pct_chg": [1.0, 2.0]})
    membership = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "sw_l1_code": ["801010.SI"],
            "sw_l1_name": ["农林牧渔"],
            "sw_l2_code": ["801011.SI"],
            "sw_l2_name": ["林业Ⅱ"],
            "sw_l3_code": ["850111.SI"],
            "sw_l3_name": ["种子"],
        }
    )

    result = attach_sw_industry(daily, membership)

    assert list(result.loc[0, SW_LEVEL_COLUMNS]) == [
        "801010.SI",
        "农林牧渔",
        "801011.SI",
        "林业Ⅱ",
        "850111.SI",
        "种子",
    ]
    assert list(result.loc[1, SW_LEVEL_COLUMNS]) == [""] * len(SW_LEVEL_COLUMNS)


def test_build_stock_map_rolls_members_up_to_all_three_levels() -> None:
    classes = pd.DataFrame(
        {
            "index_code": ["L1.SI", "L2.SI", "L3.SI", "EMPTY.SI"],
            "industry_name": ["一级", "二级", "三级", "空行业"],
            "level": ["L1", "L2", "L3", "L3"],
            "industry_code": ["100", "110", "111", "112"],
            "parent_code": ["0", "100", "110", "110"],
            "src": ["SW2021"] * 4,
        }
    )
    members = pd.DataFrame(
        {
            "l1_code": ["L1.SI", "L1.SI"],
            "l2_code": ["L2.SI", "L2.SI"],
            "l3_code": ["L3.SI", "L3.SI"],
            "ts_code": ["000002.SZ", "000001.SZ"],
        }
    )

    result = build_sw_industry_stock_map(classes, members)

    assert result["L1.SI"]["stock_codes"] == ["000001.SZ", "000002.SZ"]
    assert result["L2.SI"]["stock_codes"] == ["000001.SZ", "000002.SZ"]
    assert result["L3.SI"]["stock_codes"] == ["000001.SZ", "000002.SZ"]
    assert result["EMPTY.SI"]["stock_codes"] == []
    assert result["L1.SI"]["parent_code"] is None
    assert result["L2.SI"]["parent_code"] == "L1.SI"
    assert result["L1.SI"]["child_codes"] == ["L2.SI"]
    assert result["L2.SI"]["child_codes"] == ["EMPTY.SI", "L3.SI"]


def test_lookup_stock_codes_accepts_code_or_name(tmp_path) -> None:
    static = tmp_path / "static"
    static.mkdir()
    (static / "sw_industry_stock_map.json").write_text(
        '{"industries":{"851761.SI":{"name":"航运","level":"L3",'
        '"stock_count":2,"stock_codes":["600026.SH","601919.SH"]}}}',
        encoding="utf-8",
    )

    expected = ["600026.SH", "601919.SH"]
    assert get_sw_industry_stock_codes("851761.SI", cache_root=tmp_path) == expected
    assert get_sw_industry_stock_codes("航运", cache_root=tmp_path) == expected
