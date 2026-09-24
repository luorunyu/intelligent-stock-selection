import json

from stock_selection.screening_schemes.plan_one.report import (
    build_plan_one_report,
    render_plan_one_markdown,
    save_plan_one_report,
)
from stock_selection.screening_schemes.plan_one.scheme import run_plan_one
from tests.screening_schemes.plan_one.test_scheme import _inputs


def test_report_groups_stocks_under_ranked_industries_and_saves_files(tmp_path):
    industries, stocks, dictionary = _inputs()
    qualified, stock_metrics, selected = run_plan_one(
        industries,
        stocks,
        dictionary,
        as_of_date="20260108",
        industry_window=6,
        min_rise_from_low=0.05,
        min_days_since_low=4,
        min_up_days=4,
        stock_window=6,
        min_same_direction_ratio=0.60,
    )
    report = build_plan_one_report(
        qualified,
        stock_metrics,
        selected,
        as_of_date="20260108",
        industry_window=6,
        min_rise_from_low=0.05,
        min_days_since_low=4,
        min_up_days=4,
        stock_window=6,
        min_same_direction_ratio=0.60,
    )

    assert [item["industry_code"] for item in report["industries"]] == ["L2A", "L2B"]
    assert report["industries"][0]["stocks"][0]["ts_code"] == "000001.SZ"
    markdown = render_plan_one_markdown(report)
    assert markdown.index("L2A 行业A") < markdown.index("L2B 行业B")
    assert "仅作研究筛选，不构成投资建议" in markdown

    csv_path, json_path, markdown_path = save_plan_one_report(
        report, selected, records_root=tmp_path
    )
    assert csv_path.exists()
    assert json.loads(json_path.read_text(encoding="utf-8"))["summary"]["selected_unique_stocks"] == 2
    assert markdown_path.read_text(encoding="utf-8").startswith("# 筛选方案一")
