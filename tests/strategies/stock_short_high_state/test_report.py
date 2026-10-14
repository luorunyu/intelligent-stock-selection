import json

from stock_selection.strategies.stock_short_high_state.report import (
    build_stock_short_state_report,
    save_stock_short_state_report,
)
from stock_selection.strategies.stock_short_high_state.screen import (
    calculate_stock_short_states,
)

from tests.strategies.stock_short_high_state.test_screen import _bars


def test_report_saves_full_daily_states(tmp_path):
    states = calculate_stock_short_states(
        _bars([10, 10, 10, 10, 10, 11, 12, 8.4]),
        lookback_days=5,
        high_heat_days=3,
        trough_heat_days=3,
    )
    states.loc[states.index[-1], "total_mv"] = 1_234_567.89
    states.loc[states.index[-1], "circ_mv"] = 987_654.32
    report = build_stock_short_state_report(
        states,
        as_of_date="20260930",
        lookback_days=5,
        confirm_drawdown_pct=0.30,
        rebound_pct=0.10,
        high_heat_days=3,
        trough_heat_days=3,
    )
    csv_path, json_path, markdown_path = save_stock_short_state_report(
        report,
        states,
        records_root=tmp_path,
    )

    assert csv_path.exists()
    assert csv_path.name == "states.csv"
    assert csv_path.parent.name == "2026-09-30"
    assert json_path.name == "report.json"
    assert markdown_path.name == "report.md"
    assert json.loads(json_path.read_text(encoding="utf-8"))["summary"]["stocks"] == 1
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "策略五" in markdown
    assert "123.46亿元" in markdown
    assert "98.77亿元" in markdown
