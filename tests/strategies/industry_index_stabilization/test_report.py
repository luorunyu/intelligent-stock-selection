import json

import pandas as pd

from stock_selection.strategies.industry_index_stabilization.report import (
    build_industry_index_stabilization_report,
    render_industry_index_stabilization_markdown,
    save_industry_index_stabilization_report,
)


def _signals() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "index_code": "L2A",
                "name": "行业A",
                "signal_date": "20260301",
                "decline_days": 22,
                "decline_pct": -0.35,
                "stable_range_points": 8.0,
                "max_forward_return": 0.45,
                "validation_status": "success",
                "target_hit_date": "20260320",
            },
            {
                "index_code": "L2B",
                "name": "行业B",
                "signal_date": "20260401",
                "decline_days": 25,
                "decline_pct": -0.20,
                "stable_range_points": 9.5,
                "max_forward_return": 0.25,
                "validation_status": "failed",
                "target_hit_date": None,
            },
            {
                "index_code": "L2A",
                "name": "行业A",
                "signal_date": "20260920",
                "decline_days": 21,
                "decline_pct": -0.18,
                "stable_range_points": 7.0,
                "max_forward_return": 0.05,
                "validation_status": "pending",
                "target_hit_date": None,
            },
        ]
    )


def test_report_calculates_effectiveness_from_resolved_signals():
    report = build_industry_index_stabilization_report(
        _signals(),
        as_of_date="20260924",
        level="L2",
        trend_lookback=120,
        min_decline_days=20,
        min_decline_pct=0.30,
        min_stable_days=5,
        max_stable_range_points=10,
        forward_window=60,
        target_forward_return=0.40,
    )

    assert report["summary"]["signals"] == 3
    assert report["summary"]["resolved_signals"] == 2
    assert report["summary"]["success_rate"] == 0.5
    markdown = render_industry_index_stabilization_markdown(report)
    assert "上涨至少40.0%" in markdown
    assert "已完成样本成功率：50.00%" in markdown


def test_report_saves_csv_json_and_markdown(tmp_path):
    signals = _signals()
    report = build_industry_index_stabilization_report(
        signals,
        as_of_date="20260924",
        level="L3",
        trend_lookback=120,
        min_decline_days=20,
        min_decline_pct=0.30,
        min_stable_days=5,
        max_stable_range_points=10,
        forward_window=60,
        target_forward_return=0.40,
    )
    csv_path, json_path, markdown_path = save_industry_index_stabilization_report(
        report, signals, records_root=tmp_path
    )

    assert csv_path.exists()
    assert json.loads(json_path.read_text(encoding="utf-8"))["summary"]["successes"] == 1
    assert markdown_path.read_text(encoding="utf-8").startswith("# 申万L3")
    assert "-L3-stabilization-backtest" in markdown_path.stem
