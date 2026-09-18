import json

import pandas as pd

from stock_selection.context.industry_index_report import (
    build_industry_index_report,
    render_industry_index_report_markdown,
    save_industry_index_report,
)


def _metrics() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "index_code": "L1A",
                "name": "一级A",
                "level": "L1",
                "data_quality": "ok",
                "rise_from_low_pct": 0.10,
                "days_since_low": 5,
                "up_days_after_low": 3,
                "window_low_date": "20260901",
                "up_pct_sum_after_low": 0.12,
                "down_pct_sum_after_low": -0.02,
            },
            {
                "index_code": "L2A",
                "name": "二级A",
                "level": "L2",
                "data_quality": "ok",
                "rise_from_low_pct": 0.01,
                "days_since_low": 2,
                "up_days_after_low": 1,
                "window_low_date": "20260903",
                "up_pct_sum_after_low": 0.02,
                "down_pct_sum_after_low": 0.00,
            },
            {
                "index_code": "L3A",
                "name": "三级A",
                "level": "L3",
                "data_quality": "ok",
                "rise_from_low_pct": 0.08,
                "days_since_low": 4,
                "up_days_after_low": 3,
                "window_low_date": "20260902",
                "up_pct_sum_after_low": 0.10,
                "down_pct_sum_after_low": -0.01,
            },
        ]
    )


def _hierarchy() -> dict:
    return {
        "L1A": {
            "name": "一级A",
            "children": {
                "L2A": {
                    "name": "二级A",
                    "children": {
                        "L3A": {"name": "三级A", "stock_codes": ["000001.SZ"]},
                        "L3B": {"name": "三级B", "stock_codes": ["000002.SZ"]},
                    },
                },
                "L2B": {
                    "name": "二级B",
                    "children": {
                        "L3C": {"name": "三级C", "stock_codes": ["000003.SZ"]},
                    },
                },
            },
        }
    }


def test_report_counts_direct_children_and_keeps_qualified_descendant():
    report = build_industry_index_report(
        _metrics(),
        _hierarchy(),
        as_of_date="20260918",
        window=20,
        min_rise_from_low=0.05,
        min_days_since_low=3,
        min_up_days=3,
    )

    assert report["summary"]["level1"] == {"total": 1, "qualified": 1}
    assert report["summary"]["level2"] == {"total": 2, "qualified": 0}
    assert report["summary"]["level3"] == {"total": 3, "qualified": 1}
    level1 = report["level1"][0]
    assert level1["children_summary"]["direct_children_qualified"] == 0
    assert level1["children_summary"]["qualified_descendants"] == 1
    assert level1["children_summary"]["descendant_counts"]["level2"] == {"total": 2, "qualified": 0}
    assert level1["children_summary"]["descendant_counts"]["level3"] == {"total": 3, "qualified": 1}
    assert level1["level2"][0]["level3"][0]["index_code"] == "L3A"


def test_report_renders_markdown_and_saves_both_formats(tmp_path):
    report = build_industry_index_report(
        _metrics(),
        _hierarchy(),
        as_of_date="20260918",
        window=20,
        min_rise_from_low=0.05,
        min_days_since_low=3,
        min_up_days=3,
    )
    markdown = render_industry_index_report_markdown(report)
    assert "# 申万行业指数近月低点初筛报告 - 20260918" in markdown
    assert "二级行业：满足 0 / 2" in markdown
    json_path, markdown_path = save_industry_index_report(report, records_root=tmp_path)
    assert json.loads(json_path.read_text(encoding="utf-8"))["summary"]["level3"]["qualified"] == 1
    assert markdown_path.read_text(encoding="utf-8").startswith("# 申万行业指数")
