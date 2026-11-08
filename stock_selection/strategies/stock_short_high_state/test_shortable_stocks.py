"""Current stock lists must preserve observable states and snapshot quantities."""

from pathlib import Path

import pandas as pd
import pytest

from stock_selection.strategies.stock_short_high_state.report import save_shortable_stock_list
from stock_selection.strategies.stock_short_high_state.screen import (
    StockShortState,
    calculate_stock_short_states,
)
from stock_selection.strategies.stock_short_high_state.stock_pool import (
    SHORTABLE_STOCK_COLUMNS,
    build_shortable_stock_list,
)
from stock_selection.strategies.stock_short_high_state.visualization import _write_chart_index


@pytest.fixture
def data():
    trough = [110] * 6 + [120, 90, 91, 92, 93, 92, 93, 94, 95]
    patterns = {
        "000001.SZ": [70, 80, 90, 100],
        "000002.SZ": [70, 80, 90, 100, 95],
        "000003.SZ": [70, 80, 90, 100, 89],
        "000004.SZ": [70, 80, 90, 100, 79],
        "000005.SZ": trough,
        "000006.SZ": trough + [100],
        "000007.SZ": [70, 69, 68, 67],
        "000008.SZ": [70, 80, 90, 100, 79, 101],
        "000009.SZ": [70, 80, 90, 100],
        "000011.SZ": [70, 80, 90, 100],
    }
    daily = pd.concat([
        pd.DataFrame({
            "ts_code": code,
            "stock_name": "*ST测试" if code == "000011.SZ" else f"股票{code}",
            "trade_date": pd.bdate_range("2026-01-05", periods=len(closes)).strftime("%Y%m%d"),
            "open": closes, "high": [close + 1 for close in closes],
            "close": closes, "vol": 1000,
        })
        for code, closes in patterns.items()
    ], ignore_index=True)
    states = calculate_stock_short_states(daily, lookback_days=3, high_heat_days=2, include_st=True)
    inventory = pd.DataFrame({
        "ts_code": [f"0000{number:02}.SZ" for number in range(1, 12)],
        "security_name": [f"券源名称{number}" for number in range(1, 12)],
        "available_quantity": [100, 300, 200, 500, 1000, 700, 800, 900, 0, 400, 100],
        "lending_date": "20260102",
        "eligible": [True] * 8 + [False, True, True],
    })
    return states, inventory


def _build(data, **kwargs):
    options = {"as_of_date": "20260130", "source_file": "20260102.csv", "selection_rule": "lendable_qty > 0"}
    options.update(kwargs)
    return build_shortable_stock_list(*data, **options)


def test_classifies_four_stages_and_keeps_latest_quantities(data):
    report = _build(data)
    rows = {row["ts_code"]: row for row in report["stocks"]}

    assert set(rows) == {f"00000{number}.SZ" for number in range(1, 7)}
    assert rows["000001.SZ"]["stage"] == "HIGH_NO_PULLBACK"
    assert rows["000002.SZ"]["stage"] == "PULLBACK"
    assert rows["000002.SZ"]["stage_detail"] == "回落不足10.0%"
    assert rows["000003.SZ"]["stage_detail"] == "已达到10.0%开仓回落阈值"
    assert rows["000003.SZ"]["has_entry_signal"] is False
    assert rows["000003.SZ"]["entry_breakout_qualified"] is False
    assert rows["000004.SZ"]["stage"] == "CONFIRMED_PULLBACK"
    assert rows["000005.SZ"]["stage_detail"] == "波谷区域（均线已确认）"
    assert rows["000006.SZ"]["stage_detail"] == "第1波反弹"
    assert rows["000006.SZ"]["entry_signal"] == 0  # 100 < confirmed high 120 * 90%.
    assert rows["000006.SZ"]["has_entry_signal"] is False
    assert rows["000005.SZ"]["lendable_qty"] == 1000
    assert rows["000005.SZ"]["trough_ma_close"] == 92.5
    assert rows["000005.SZ"]["drawdown_from_high_pct"] == pytest.approx(1 - 95 / 120)
    assert report["metadata"]["pool_date"] == "20260102"
    assert report["metadata"]["as_of_date"] == "20260130"
    assert report["summary"]["by_stage"] == {
        "HIGH_NO_PULLBACK": 1, "PULLBACK": 2, "CONFIRMED_PULLBACK": 1, "BOTTOM_REBOUND": 2,
    }
    assert report["summary"]["excluded"] == {
        "尚未进入高位周期": 1, "高点判断失效": 1, "无可用行情状态": 1, "ST股票": 1,
    }
    assert [row["ts_code"] for row in report["stocks"]] == [
        "000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ", "000005.SZ", "000006.SZ",
    ]


def test_as_of_date_does_not_use_future_backfilled_trough(data):
    states, inventory = data
    report = _build((states, inventory), as_of_date="20260115", stock_codes=["000005.SZ"])
    historical = states[states.ts_code.eq("000005.SZ") & states.trade_date.eq("20260115")].iloc[0]
    row, = report["stocks"]

    assert historical["state"] == StockShortState.TROUGH_REGION
    assert historical["observed_state"] == StockShortState.TROUGH_CANDIDATE
    assert row["stage"] == "CONFIRMED_PULLBACK"
    assert row["observed_state_name"] == "TROUGH_CANDIDATE"
    assert row["close"] == 91
    assert row["trade_date"] == "20260115"


def test_quantity_sorting_and_duplicate_rows_do_not_inflate_inventory(data):
    states, inventory = data
    inventory.loc[inventory.ts_code.eq("000003.SZ"), "available_quantity"] = 1500
    inventory = pd.concat([inventory, inventory[inventory.ts_code.eq("000003.SZ")]], ignore_index=True)
    report = _build((states, inventory), stock_codes=["000002.SZ", "000003.SZ"])

    assert [row["ts_code"] for row in report["stocks"]] == ["000003.SZ", "000002.SZ"]
    assert report["stocks"][0]["lendable_qty"] == 1500
    assert report["summary"]["eligible_stocks_in_scope"] == 2


def test_custom_threshold_and_st_selection_follow_run_settings(data):
    report = _build(data, candidate_pullback_pct=0.04, include_st=True)
    rows = {row["ts_code"]: row for row in report["stocks"]}

    assert rows["000002.SZ"]["stage_detail"] == "已达到4.0%开仓回落阈值"
    assert rows["000002.SZ"]["has_entry_signal"] is False
    assert rows["000011.SZ"]["stock_name"] == "*ST测试"


def test_peak_n_preserves_its_actual_number(data):
    states, inventory = data
    latest = states[states.ts_code.eq("000006.SZ")].index[-1]
    states.loc[latest, "observed_state"] = int(StockShortState.PEAK_N)
    states.loc[latest, "peak_number"] = 5
    report = _build((states, inventory), stock_codes=["000006.SZ"])

    row, = report["stocks"]
    assert row["stage_detail"] == "第5波反弹"
    assert row["observed_state_name"] == "PEAK_N"


def test_writes_readable_list_and_links_it_from_chart_index(data, tmp_path):
    report = _build(data)
    report["stocks"][0]["stock_name"] = "<script>名称</script>|测试"
    paths = save_shortable_stock_list(report, output_directory=tmp_path)
    csv = pd.read_csv(paths["csv"], dtype={"lending_date": str, "trade_date": str})

    assert list(csv.columns) == SHORTABLE_STOCK_COLUMNS
    assert csv.iloc[0]["stock_name"] == "<script>名称</script>|测试"
    assert csv.iloc[4]["lendable_qty"] == 1000
    assert csv["lending_date"].eq("20260102").all()
    html = paths["html"].read_text(encoding="utf-8")
    assert '<section id="BOTTOM_REBOUND"' in html
    assert "可融券数量（股）" in html
    assert "20260130" in html and "20260102" in html
    assert "&lt;script&gt;名称&lt;/script&gt;" in html
    assert "<script>名称</script>" not in html
    markdown = paths["md"].read_text(encoding="utf-8")
    assert "&#124;测试" in markdown
    assert "MA8" in markdown
    index = _write_chart_index(tmp_path, [], [], Path("states.csv"))
    assert 'href="shortable_stocks.html"' in index.read_text(encoding="utf-8")


def test_empty_list_has_headers_and_explicit_empty_stages(data, tmp_path):
    report = _build(data, stock_codes=[])
    paths = save_shortable_stock_list(report, output_directory=tmp_path)

    assert pd.read_csv(paths["csv"]).empty
    assert list(pd.read_csv(paths["csv"]).columns) == SHORTABLE_STOCK_COLUMNS
    assert report["summary"]["stocks"] == 0
    html = paths["html"].read_text(encoding="utf-8")
    assert html.count("此阶段暂无股票") == 4
    assert "nan" not in html


def test_verified_list_uses_actual_quantity_for_sorting_and_display(data, tmp_path):
    states, inventory = data
    inventory = inventory[inventory.ts_code.isin(["000002.SZ", "000003.SZ"])].copy()
    inventory["actual_sellable_qty"] = [100, 500]
    inventory["verification_status"] = "available"
    inventory["queried_at"] = "2026-01-02T10:00:00+08:00"
    # Inventory and verified quantities are independent display fields.
    inventory.loc[inventory.ts_code.eq("000003.SZ"), "available_quantity"] = 0
    report = _build((states, inventory))
    assert [row["ts_code"] for row in report["stocks"]] == ["000003.SZ", "000002.SZ"]
    assert report["stocks"][0]["lendable_qty"] == 0
    assert report["stocks"][0]["actual_sellable_qty"] == 500
    paths = save_shortable_stock_list(report, output_directory=tmp_path)
    saved = pd.read_csv(paths["csv"])
    assert saved["actual_sellable_qty"].tolist() == [500, 100]
    html = paths["html"].read_text(encoding="utf-8")
    assert "实际可融数量（股）" in html and "核验有券" in html
