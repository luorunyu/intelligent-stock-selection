import json

import pandas as pd

from stock_selection.screening_schemes.plan_two.report import (
    build_plan_two_report,
    save_plan_two_report,
)


def test_saves_csv_json_and_markdown(tmp_path):
    result = pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "security_name": "测试股票",
                "available_quantity": 1000,
                "state_description": "最高点候选区域",
                "peak_number": 0,
                "heat": 0.2,
                "classification_name": "SHORT_SCOPE",
                "classification_description": "做空范围",
                "instrument_match_status": "MATCHED",
                "market_data_date": "20261009",
                "market_data_stale": False,
            }
        ]
    )
    report = build_plan_two_report(
        result,
        result,
        result,
        as_of_date="20261009",
        lending_file="data_cache/securities_lending/daily/20261009.xls",
        min_available_quantity=1,
        required_lending_status="正常",
        lookback_days=120,
        confirm_drawdown_pct=0.30,
        rebound_pct=0.10,
        high_heat_days=10,
        trough_heat_days=5,
        include_st=False,
        allow_stale_market_data=False,
    )
    csv_path, json_path, markdown_path = save_plan_two_report(
        report,
        result,
        records_root=tmp_path,
    )

    assert csv_path.exists()
    assert json.loads(json_path.read_text(encoding="utf-8"))["summary"][
        "result_rows"
    ] == 1
    assert "做空范围" in markdown_path.read_text(encoding="utf-8")
