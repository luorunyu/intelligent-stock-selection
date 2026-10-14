import pandas as pd
import pytest

from stock_selection.strategies.stock_short_high_state.backtest import (
    build_short_trade_backtest,
    filter_short_trade_backtest_by_stock_pool,
    split_short_trade_backtest_by_average_market_cap,
)
from stock_selection.strategies.stock_short_high_state.report import (
    save_short_trade_backtest_report,
)
from stock_selection.strategies.stock_short_high_state.screen import (
    calculate_stock_short_states,
)

from tests.strategies.stock_short_high_state.test_screen import _bars


def _states(closes: list[float]):
    return calculate_stock_short_states(
        _bars(closes),
        lookback_days=5,
        confirm_drawdown_pct=0.30,
        rebound_pct=0.10,
        high_heat_days=3,
        trough_heat_days=3,
    )


def test_backtest_pairs_candidate_and_numbered_peak_states():
    states = _states(
        [
            10,
            10,
            10,
            10,
            10,
            20,
            21,
            14,
            12,
            13.2,
            14,
            13.2,
            11,
            12.1,
            12.8,
            12.1,
        ]
    )
    states.loc[states["trade_date"].eq("20250106"), "total_mv"] = 1_000_000

    backtest = build_short_trade_backtest(states, as_of_date="20250116")

    assert backtest["summary"]["signals"] == 3
    assert backtest["summary"]["completed_trades"] == 3
    assert backtest["summary"]["wins"] == 1
    assert backtest["summary"]["breakeven"] == 2
    assert backtest["summary"]["win_rate"] == pytest.approx(1 / 3)

    candidate = backtest["trades"][0]
    assert candidate["entry_state"] == "HIGH_CANDIDATE"
    assert candidate["entry_source"] == "RECONSTRUCTED_STATE_START"
    assert candidate["entry_date"] == "20250106"
    assert candidate["entry_close"] == 20
    assert candidate["entry_total_mv"] == 1_000_000
    assert candidate["average_total_mv"] == 1_000_000
    assert candidate["market_cap_observation_days"] == 1
    assert candidate["exit_state"] == "TROUGH_WAIT_REBOUND"
    assert candidate["exit_date"] == "20250108"
    assert candidate["exit_close"] == 14
    assert candidate["return_pct"] == pytest.approx(20 / 14 - 1)
    assert candidate["max_drawdown_pct"] == pytest.approx(1 - (20 / 21))

    first_peak = backtest["trades"][1]
    assert first_peak["entry_state"] == "PEAK_1"
    assert first_peak["exit_state"] == "PEAK_1_PULLBACK"
    assert first_peak["return_pct"] == pytest.approx(0)
    assert first_peak["mae_pct"] == pytest.approx(14 / 13.2 - 1)


def test_backtest_separates_open_and_invalidated_trades():
    backtest = build_short_trade_backtest(
        _states([10, 10, 10, 10, 10, 20, 21, 14, 12, 13.2, 22, 23]),
        as_of_date="20250112",
    )

    assert backtest["summary"]["completed_trades"] == 1
    assert backtest["summary"]["invalidated_trades"] == 1
    assert backtest["summary"]["open_trades"] == 1

    invalidated = next(
        trade
        for trade in backtest["trades"]
        if trade["status"] == "INVALIDATED"
    )
    assert invalidated["entry_state"] == "PEAK_1"
    assert invalidated["return_pct"] is None
    assert invalidated["evaluation_date"] == "20250111"

    open_trade = next(
        trade for trade in backtest["trades"] if trade["status"] == "OPEN"
    )
    assert open_trade["entry_state"] == "HIGH_CANDIDATE"
    assert open_trade["entry_source"] == "OBSERVED_HIGH_CANDIDATE"
    assert open_trade["entry_date"] == "20250112"
    assert open_trade["entry_close"] == 23
    assert open_trade["return_pct"] is None


def test_backtest_report_is_saved_as_an_additional_markdown_file(tmp_path):
    backtest = build_short_trade_backtest(
        _states([10, 10, 10, 10, 10, 20, 21, 14]),
        as_of_date="20250108",
    )

    path = save_short_trade_backtest_report(backtest, records_root=tmp_path)

    assert path.name == "short_trade_backtest.md"
    assert path.parent.name == "2025-01-08"
    markdown = path.read_text(encoding="utf-8")
    assert "状态做空回测" in markdown
    assert "HIGH_CANDIDATE" in markdown
    assert "42.86%" in markdown
    assert "已确认周期" in markdown
    assert "short_trade_backtest.html" in markdown

    html_path = path.with_suffix(".html")
    assert html_path.exists()
    html = html_path.read_text(encoding="utf-8")
    assert '<table class="trade-table">' in html
    assert "<th>开仓流通市值</th>" in html
    assert "<th>最大回撤</th>" in html
    assert "main{width:100%;max-width:none;margin:0}" in html
    assert "HIGH_CANDIDATE" in html
    assert "42.86%" in html


def test_backtest_splits_trades_by_holding_period_average_market_cap(tmp_path):
    low = _states(
        [10, 10, 10, 10, 10, 20, 21, 14, 12, 13.2, 14, 13.2]
    )
    low["total_mv"] = 2_000_000
    boundary = low.copy()
    boundary["ts_code"] = "600000.SH"
    boundary["stock_name"] = "边界股票"
    boundary["total_mv"] = 3_000_000
    missing = low.copy()
    missing["ts_code"] = "600001.SH"
    missing["stock_name"] = "缺失市值"
    missing["total_mv"] = None

    backtest = build_short_trade_backtest(
        pd.concat([low, boundary, missing], ignore_index=True),
        as_of_date="20250112",
    )
    split = split_short_trade_backtest_by_average_market_cap(backtest)

    assert split["below"]["summary"]["signals"] == 2
    assert split["at_least"]["summary"]["signals"] == 2
    assert {
        trade["ts_code"] for trade in split["below"]["trades"]
    } == {"000001.SZ"}
    assert {
        trade["ts_code"] for trade in split["at_least"]["trades"]
    } == {"600000.SH"}
    market_cap_filter = split["below"]["metadata"]["market_cap_filter"]
    assert market_cap_filter["missing_market_cap_trades_excluded"] == 2

    below_path = save_short_trade_backtest_report(
        split["below"],
        records_root=tmp_path,
        filename="short_trade_backtest_avg_mv_below_300yi.md",
    )
    markdown = below_path.read_text(encoding="utf-8")
    assert "平均总市值低于300亿元" in markdown
    assert "平均总市值为每笔交易" in markdown
    assert "200.00亿元" in markdown


def test_backtest_filters_a_fixed_lendable_stock_pool(tmp_path):
    backtest = build_short_trade_backtest(
        _states([10, 10, 10, 10, 10, 20, 21, 14, 12, 13.2, 14, 13.2]),
        as_of_date="20250112",
    )

    filtered = filter_short_trade_backtest_by_stock_pool(
        backtest,
        ["000001.SZ", "510300.SH"],
        pool_label="20250112可融券股票池",
        pool_date="20250112",
        source_file="lending/20250112.xls",
        selection_rule="融券状态正常且可用数量大于0",
        source_record_count=10,
        matched_state_stock_count=1,
    )

    assert filtered["summary"]["signals"] == 2
    pool = filtered["metadata"]["stock_pool_filter"]
    assert pool["eligible_code_count"] == 2
    assert pool["matched_state_stock_count"] == 1
    assert pool["matched_trade_stock_count"] == 1

    path = save_short_trade_backtest_report(
        filtered,
        records_root=tmp_path,
        filename="short_trade_backtest_lendable_20250112.md",
    )
    markdown = path.read_text(encoding="utf-8")
    assert "20250112可融券股票池" in markdown
    assert "源记录 10 条" in markdown
    assert "不能证明历史开仓日当时有券可借" in markdown
