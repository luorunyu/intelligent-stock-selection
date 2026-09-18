import pandas as pd

from stock_selection.data.sw_industry_dictionary import (
    build_sw_industry_dictionary,
    collect_sw_industry_data,
)


def _classes() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"index_code": "L1A", "industry_name": "一级A", "level": "L1", "industry_code": "I1", "parent_code": ""},
            {"index_code": "L2A", "industry_name": "二级A", "level": "L2", "industry_code": "I2", "parent_code": "I1"},
            {"index_code": "L3A", "industry_name": "三级A", "level": "L3", "industry_code": "I3", "parent_code": "I2"},
            {"index_code": "L3B", "industry_name": "三级B", "level": "L3", "industry_code": "I4", "parent_code": "I2"},
        ]
    )


def test_build_dictionary_rolls_stocks_up_to_all_levels():
    members = pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "l1_code": "L1A",
                "l1_name": "一级A",
                "l2_code": "L2A",
                "l2_name": "二级A",
                "l3_code": "L3A",
                "l3_name": "三级A",
                "out_date": None,
            },
            {
                "ts_code": "600000.SH",
                "l1_code": "L1A",
                "l1_name": "一级A",
                "l2_code": "L2A",
                "l2_name": "二级A",
                "l3_code": "L3B",
                "l3_name": "三级B",
                "out_date": "",
            },
            {
                "ts_code": "000002.SZ",
                "l1_code": "L1A",
                "l1_name": "一级A",
                "l2_code": "L2A",
                "l2_name": "二级A",
                "l3_code": "L3A",
                "l3_name": "三级A",
                "out_date": "20200101",
            },
        ]
    )

    result = build_sw_industry_dictionary(_classes(), members, generated_at="2026-09-18T12:00:00+08:00")

    assert result["level1"]["L1A"]["children"]["L2A"]["children"]["L3A"]["stock_codes"] == ["000001.SZ"]
    assert result["level1_index"]["L1A"]["stock_codes"] == ["000001.SZ", "600000.SH"]
    assert result["level2_index"]["L2A"]["stock_codes"] == ["000001.SZ", "600000.SH"]
    assert result["level3_index"]["L3A"]["stock_codes"] == ["000001.SZ"]
    assert result["level3_index"]["L3B"]["stock_codes"] == ["600000.SH"]
    assert "stock_codes" not in result["level1"]["L1A"]
    assert "stock_codes" not in result["level1"]["L1A"]["children"]["L2A"]
    assert result["metadata"]["counts"]["unique_stocks"] == 2


def test_collect_queries_every_level3_industry():
    calls = []

    def caller(api_name, params):
        calls.append((api_name, params))
        if api_name == "index_classify":
            return _classes()
        level3_code = params["l3_code"]
        return pd.DataFrame(
            [
                {
                    "ts_code": f"{level3_code}.SZ",
                    "l1_code": "L1A",
                    "l1_name": "一级A",
                    "l2_code": "L2A",
                    "l2_name": "二级A",
                    "l3_code": level3_code,
                    "l3_name": level3_code,
                }
            ]
        )

    classes, members = collect_sw_industry_data(caller=caller)

    assert len(classes) == 4
    assert set(members["l3_code"]) == {"L3A", "L3B"}
    assert calls == [
        ("index_classify", {"src": "SW2021"}),
        ("index_member_all", {"l3_code": "L3A"}),
        ("index_member_all", {"l3_code": "L3B"}),
    ]
