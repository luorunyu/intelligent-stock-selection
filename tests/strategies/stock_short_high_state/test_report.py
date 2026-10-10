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
    assert json.loads(json_path.read_text(encoding="utf-8"))["summary"]["stocks"] == 1
    assert "策略五" in markdown_path.read_text(encoding="utf-8")
