import numpy as np
import pandas as pd
import pytest

from stock_selection.strategies.stock_short_high_state.backtest import (
    _compounded_return,
    _sequential_max_drawdown,
    build_short_trade_backtest,
    filter_short_trade_backtest_by_stock_pool,
    split_short_trade_backtest_by_average_market_cap,
)
from stock_selection.strategies.stock_short_high_state.report import (
    save_short_trade_backtest_report,
)
from stock_selection.strategies.stock_short_high_state.screen import (
    StockShortState,
    calculate_stock_short_states,
)

from tests.strategies.stock_short_high_state.test_screen import _bars


def _states(closes: list[float]):
    return calculate_stock_short_states(
        _bars(closes),
        lookback_days=5,
        candidate_pullback_pct=0.15,
        confirm_drawdown_pct=0.30,
        rebound_pct=0.10,
        high_heat_days=3,
        trough_confirm_days=3,
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
            12.2,
            12.1,
            13.3,
            13.0,
            12.2,
            13.4,
            12.1,
        ]
    )
    states.loc[states["trade_date"].eq("20250108"), "total_mv"] = 1_000_000

    backtest = build_short_trade_backtest(states, as_of_date="20250117")

    assert backtest["summary"]["signals"] == 3
    assert backtest["summary"]["completed_trades"] == 3
    assert backtest["summary"]["wins"] == 3
    assert backtest["summary"]["breakeven"] == 0
    assert backtest["summary"]["win_rate"] == pytest.approx(1)

    candidate = backtest["trades"][0]
    assert candidate["entry_state"] == "HIGH_CANDIDATE_PULLBACK"
    assert candidate["entry_source"] == "OBSERVED_HIGH_CANDIDATE_PULLBACK"
    assert candidate["entry_date"] == "20250108"
    assert candidate["entry_close"] == 17.8
    assert candidate["entry_total_mv"] == 1_000_000
    assert candidate["average_total_mv"] == 1_000_000
    assert candidate["market_cap_observation_days"] == 1
    assert candidate["exit_state"] == "TROUGH_CANDIDATE"
    assert candidate["exit_date"] == "20250109"
    assert candidate["exit_close"] == 14
    assert candidate["return_pct"] == pytest.approx(1 - 14 / 17.8)
    assert candidate["max_drawdown_pct"] == pytest.approx(0)

    first_peak = backtest["trades"][1]
    assert first_peak["entry_state"] == "PEAK_1"
    assert first_peak["exit_state"] == "TROUGH_REGION"
    assert first_peak["return_pct"] == pytest.approx(1 - 12.2 / 13.3)
    assert first_peak["mae_pct"] == pytest.approx(0)


def test_backtest_separates_open_and_invalidated_trades():
    backtest = build_short_trade_backtest(
        _states(
            [10, 10, 10, 10, 10, 20, 21, 14, 12, 12.2, 12.1, 13.3, 22, 23]
        ),
        as_of_date="20250114",
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
    assert invalidated["evaluation_date"] == "20250113"

    open_trade = next(
        trade for trade in backtest["trades"] if trade["status"] == "OPEN"
    )
    assert open_trade["entry_state"] == "HIGH_CANDIDATE"
    assert open_trade["entry_source"] == "OBSERVED_HIGH_CANDIDATE"
    assert open_trade["entry_date"] == "20250114"
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
    assert "30.00%" in markdown
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
    assert "30.00%" in html


def test_backtest_splits_trades_by_holding_period_average_market_cap(tmp_path):
    low = _states(
        [
            10,
            10,
            10,
            10,
            10,
            20,
            21,
            17.8,
            14,
            12,
            12.2,
            12.1,
            13.3,
            13.0,
            12.2,
            13.4,
            12.1,
        ]
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
        as_of_date="20250116",
    )
    split = split_short_trade_backtest_by_average_market_cap(backtest)

    assert split["below"]["summary"]["signals"] == 3
    assert split["at_least"]["summary"]["signals"] == 3
    assert {
        trade["ts_code"] for trade in split["below"]["trades"]
    } == {"000001.SZ"}
    assert {
        trade["ts_code"] for trade in split["at_least"]["trades"]
    } == {"600000.SH"}
    market_cap_filter = split["below"]["metadata"]["market_cap_filter"]
    assert market_cap_filter["missing_market_cap_trades_excluded"] == 3

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
        _states(
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
                12.2,
                12.1,
                13.3,
                13.0,
                12.2,
                13.4,
                12.1,
            ]
        ),
        as_of_date="20250116",
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

    assert filtered["summary"]["signals"] == 3
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


@pytest.mark.parametrize(
    "returns, expected_return, expected_drawdown",
    [
        ([], None, None),
        ([0.1, -0.2, 0.05], -0.076, 0.2),
        ([-0.2, 0.1], -0.12, 0.2),
        ([-0.999999], -0.999999, 0.999999),
        ([0.1, -1.0, 0.5], -1.0, 1.0),
        ([-1.5], None, None),
        ([-1.0, -1.5], None, None),
        ([float("nan")], None, None),
        ([float("inf")], None, None),
        ([0.5] * 3000, None, 0.0),
    ],
)
def test_sequential_statistics_handle_capital_exhaustion_without_numeric_warnings(
    returns, expected_return, expected_drawdown,
):
    trades = [
        {
            "ts_code": "000001.SZ",
            "entry_date": f"{index:08d}",
            "exit_date": f"{index + 1:08d}",
            "return_pct": value,
        }
        for index, value in reversed(list(enumerate(returns)))
    ]

    with np.errstate(all="raise"):
        compounded = _compounded_return(np.array(returns, dtype=float))
        drawdown = _sequential_max_drawdown(trades)

    if expected_return is None:
        assert compounded is None
    else:
        assert compounded == pytest.approx(expected_return)
    if expected_drawdown is None:
        assert drawdown is None
    else:
        assert drawdown == pytest.approx(expected_drawdown)


@pytest.mark.parametrize(
    "exit_close, expected_return, expected_drawdown",
    [(20.0, -1.0, 1.0), (25.0, None, None)],
)
def test_backtest_preserves_losses_when_sequential_equity_cannot_continue(
    tmp_path, exit_close, expected_return, expected_drawdown,
):
    states = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"] * 4,
            "trade_date": ["20260105", "20260106", "20260107", "20260108"],
            "close": [10.0, exit_close, 10.0, 1.0],
            "state": [
                int(StockShortState.HIGH_CANDIDATE_PULLBACK),
                int(StockShortState.TROUGH_CANDIDATE),
            ] * 2,
            "state_name": ["HIGH_CANDIDATE_PULLBACK", "TROUGH_CANDIDATE"] * 2,
            "cycle_id": [1, 1, 2, 2],
            "peak_number": [0] * 4,
        }
    )

    with np.errstate(all="raise"):
        backtest = build_short_trade_backtest(states, as_of_date="20260108")

    summary = backtest["summary"]
    loss = 1 - exit_close / 10
    assert summary["completed_trades"] == 2
    assert summary["wins"] == 1
    assert summary["losses"] == 1
    assert summary["win_rate"] == 0.5
    assert summary["worst_return_pct"] == loss
    assert summary["average_return_pct"] == pytest.approx((loss + 0.9) / 2)
    assert backtest["trades"][0]["return_pct"] == loss
    assert summary["compounded_return_pct"] == expected_return
    assert summary["sequential_max_drawdown_pct"] == expected_drawdown

    report_path = save_short_trade_backtest_report(backtest, records_root=tmp_path)
    markdown = report_path.read_text(encoding="utf-8")
    html = report_path.with_suffix(".html").read_text(encoding="utf-8")
    assert "亏损超过100%" in markdown
    assert "亏损超过100%" in html
    assert "1 - 平仓价 / 开仓价" in markdown
    if expected_return is None:
        assert "逐笔复利收益：-\n" in markdown
        assert "逐笔复利最大回撤：-\n" in markdown
    else:
        assert "逐笔复利收益：-100.00%" in markdown
        assert "逐笔复利最大回撤：100.00%" in markdown
