import json

import pandas as pd

from stock_selection.strategies.stock_industry_direction.report import (
    build_stock_industry_direction_report,
    save_stock_industry_direction_report,
)


def test_report_writes_simple_csv_json_and_markdown(tmp_path):
    metrics = pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "stock_name": "样例股份",
                "industry_code": "801010.SI",
                "industry_name": "行业二级",
                "data_quality": "ok",
                "same_direction_ratio": 0.7,
                "direction_observations": 20,
                "same_direction_days": 14,
                "same_up_days": 8,
                "same_down_days": 6,
                "opposite_direction_days": 6,
                "neutral_days": 0,
            }
        ]
    )
    report = build_stock_industry_direction_report(
        metrics,
        as_of_date="20260918",
        level="L2",
        window=20,
        min_same_direction_ratio=0.6,
    )

    csv_path, json_path, markdown_path = save_stock_industry_direction_report(
        report, metrics, records_root=tmp_path
    )

    assert csv_path.exists()
    assert json.loads(json_path.read_text(encoding="utf-8"))["summary"]["valid_rows"] == 1
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "同方向比例" in markdown
    assert "70.0%" in markdown
