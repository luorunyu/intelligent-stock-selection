import json

import pandas as pd

from stock_selection.strategies.stock_peak_pullback_rebound.report import (
    build_peak_pullback_rebound_report,
    save_peak_pullback_rebound_report,
)


def test_report_saves_event_files(tmp_path):
    events = pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "stock_name": "测试股票",
                "peak_date": "20260301",
                "peak_close": 100.0,
                "pullback_low_date": "20260308",
                "pullback_low_close": 65.0,
                "pullback_pct": -0.35,
                "pullback_days": 7,
                "second_peak_date": "20260320",
                "second_peak_close": 75.0,
                "second_peak_ratio": 0.75,
                "peak_to_second_peak_days": 19,
            }
        ]
    )
    report = build_peak_pullback_rebound_report(
        events,
        as_of_date="20260930",
        peak_lookback_days=120,
        peak_lookforward_days=120,
        max_rebound_days=40,
        min_pullback_pct=0.10,
        min_second_peak_ratio=0.70,
    )
    csv_path, json_path, markdown_path = save_peak_pullback_rebound_report(
        report, events, records_root=tmp_path
    )

    assert csv_path.exists()
    assert json.loads(json_path.read_text(encoding="utf-8"))["summary"]["events"] == 1
    assert "000001.SZ" in markdown_path.read_text(encoding="utf-8")
