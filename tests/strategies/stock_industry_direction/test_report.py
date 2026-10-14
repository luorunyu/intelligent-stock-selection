import json

import pandas as pd

from stock_selection.strategies.stock_industry_direction.report import (
    build_stock_industry_direction_report,
    save_stock_industry_direction_history,
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
                "return_correlation": 0.8,
                "industry_beta": 1.2,
                "industry_r_squared": 0.64,
                "regression_quality": "ok",
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
    assert "0.8000" in markdown
    assert "1.2000" in markdown
    assert "0.6400" in markdown


def test_history_writes_parquet_and_metadata(tmp_path):
    history = pd.DataFrame(
        [
            {
                "as_of_date": "20260917",
                "ts_code": "000001.SZ",
                "industry_code": "801010.SI",
                "data_quality": "ok",
                "return_correlation": 0.7,
                "industry_beta": 1.1,
                "industry_r_squared": 0.49,
            },
            {
                "as_of_date": "20260918",
                "ts_code": "000001.SZ",
                "industry_code": "801010.SI",
                "data_quality": "ok",
                "return_correlation": 0.8,
                "industry_beta": 1.2,
                "industry_r_squared": 0.64,
            },
        ]
    )

    parquet_path, metadata_path = save_stock_industry_direction_history(
        history,
        end_date="20260918",
        level="L2",
        window=20,
        records_root=tmp_path,
    )

    assert pd.read_parquet(parquet_path).shape[0] == 2
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["rows"] == 2
    assert metadata["dates"] == 2
    assert metadata["relationships"] == 1
