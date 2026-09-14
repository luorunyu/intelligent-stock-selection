from __future__ import annotations

import pandas as pd

from stock_selection.data.industry_taxonomy import SW_LEVEL_COLUMNS, attach_sw_industry


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

    assert list(result.loc[0, list(SW_LEVEL_COLUMNS)]) == [
        "801010.SI",
        "农林牧渔",
        "801011.SI",
        "林业Ⅱ",
        "850111.SI",
        "种子",
    ]
    assert list(result.loc[1, list(SW_LEVEL_COLUMNS)]) == [""] * len(SW_LEVEL_COLUMNS)
