"""Default signals, stock-level return groups and the CLI's four report scopes."""

from argparse import Namespace
from pathlib import Path
import re
import sys

import pandas as pd
import pytest

from stock_selection.strategies.stock_short_high_state import cli
from stock_selection.strategies.stock_short_high_state.backtest import (
    build_short_trade_backtest,
    stock_trade_return,
)
from stock_selection.strategies.stock_short_high_state.report import (
    save_short_trade_backtest_sections,
)
from stock_selection.strategies.stock_short_high_state.screen import StockShortState as S


def _states(states, prices, *, code="000068.SZ", cycle_ids=None):
    return pd.DataFrame({
        "ts_code": code, "stock_name": "测试股票",
        "trade_date": pd.bdate_range("20261001", periods=len(states)).strftime("%Y%m%d"),
        "state": [int(state) for state in states],
        "state_name": [state.name for state in states],
        "close": prices, "cycle_id": cycle_ids or [1] * len(states),
        "peak_number": [1 if state == S.PEAK_1 else 0 for state in states],
        "total_mv": 2_000_000,
    })


@pytest.mark.parametrize("exit_state", [
    S.TROUGH_CANDIDATE, S.TROUGH_REGION, S.CYCLE_ENDED, S.REVERSAL_CONFIRMED,
])
def test_candidate_can_exit_at_each_supported_state(exit_state):
    states = _states([S.HIGH_CANDIDATE_PULLBACK, exit_state], [10, 8])
    result = build_short_trade_backtest(states, as_of_date="20261016")
    trade = result["trades"][0]
    assert result["metadata"]["entry_states"] == ["HIGH_CANDIDATE_PULLBACK"]
    assert result["summary"]["completed_trades"] == 1
    assert trade["exit_state"] == exit_state.name
    assert trade["exit_date"] == "20261002"
    assert trade["exit_close"] == 8
    assert trade["return_pct"] == pytest.approx(0.2)


def test_earliest_exit_wins_and_peak_rows_no_longer_open_trades():
    states = _states([
        S.HIGH_CANDIDATE_PULLBACK, S.HIGH_CANDIDATE_PULLBACK,
        S.TROUGH_REGION, S.PEAK_1, S.CYCLE_ENDED, S.REVERSAL_CONFIRMED,
    ], [10, 9, 8, 9, 6.5, 6.8])
    result = build_short_trade_backtest(states, as_of_date="20261016")
    assert result["summary"]["signals"] == 1
    assert result["trades"][0]["exit_state"] == "TROUGH_REGION"
    states = states.iloc[[0, 4, 5]]
    result = build_short_trade_backtest(states, as_of_date="20261016")
    assert result["trades"][0]["exit_state"] == "CYCLE_ENDED"


def test_reversal_exit_uses_confirmation_price_and_respects_report_cutoff():
    states = _states(
        [S.HIGH_CANDIDATE_PULLBACK, S.NORMAL, S.REVERSAL_CONFIRMED], [10, 5, 7]
    )
    states["reversal_low_date"] = "20261002"
    states["reversal_low_close"] = 5
    states["reversal_confirm_date"] = "20261005"
    before = build_short_trade_backtest(states, as_of_date="20261002")["trades"][0]
    after = build_short_trade_backtest(states, as_of_date="20261005")["trades"][0]
    assert before["status"] == "OPEN"
    assert before["evaluation_close"] == 5
    assert after["exit_date"] == "20261005"
    assert after["exit_close"] == 7
    assert after["return_pct"] == pytest.approx(0.3)


def _multi_stock_backtest():
    frames = []
    # A has one profitable and one losing trade, but its stock total is positive.
    for code, prices, caps, status in [
        ("000068.SZ", [10, 7, 10, 11], [2e6, 2e6, 4e6, 4e6], S.TROUGH_CANDIDATE),
        ("000069.SZ", [10, 12, 10, 10], [3e6] * 4, S.TROUGH_CANDIDATE),
        ("600000.SH", [10, 10, 10, 10], [2e6] * 4, S.TROUGH_CANDIDATE),
    ]:
        frame = _states(
            [S.HIGH_CANDIDATE_PULLBACK, status] * 2, prices,
            code=code, cycle_ids=[1, 1, 2, 2],
        )
        frame["total_mv"] = caps
        frames.append(frame)
    return build_short_trade_backtest(pd.concat(frames), as_of_date="20261016")


def test_stock_category_uses_marked_returns_and_ignores_nonfinite_values():
    assert stock_trade_return([
        {"status": "COMPLETED", "return_pct": 0.2, "mark_return_pct": -9},
        {"status": "OPEN", "mark_return_pct": -0.1},
        {"status": "INVALIDATED", "mark_return_pct": -0.3},
        {"status": "OPEN", "mark_return_pct": float("nan")},
    ]) == pytest.approx(-0.2)
    assert stock_trade_return([]) is None
    assert stock_trade_return([{"status": "COMPLETED", "return_pct": float("inf")}]) is None


@pytest.fixture
def lending_args(tmp_path):
    path = tmp_path / "20261009.tsv"
    pd.DataFrame({
        "市场类别": ["深圳Ａ"] * 4,
        "证券代码": ["000068", "000069", "000070", "000071"],
        "证券名称": ["测试"] * 4,
        "可用数量": [0, 100, 0, 100],
        "保证金比例(%)": [100] * 4,
        "融券状态": ["正常", "正常", "暂停", "暂停"],
    }).to_csv(path, sep="\t", index=False, encoding="gb18030")
    return Namespace(date="20261016", lending_file=None, lending_root=str(tmp_path))


def test_four_scopes_include_zero_quantity_and_use_latest_past_snapshot(lending_args):
    report = _multi_stock_backtest()
    states = pd.DataFrame({"ts_code": ["000068.SZ", "000069.SZ", "600000.SH"]})
    sections = cli._build_backtest_sections(states, report, args=lending_args)
    assert set(sections) == {"overall", "avg_mv_at_least_300yi", "avg_mv_below_300yi", "lendable"}
    pool = sections["lendable"]
    assert pool["metadata"]["stock_pool_filter"]["pool_date"] == "20261009"
    assert pool["metadata"]["stock_pool_filter"]["eligible_code_count"] == 2
    assert {t["ts_code"] for t in pool["trades"]} == {"000068.SZ", "000069.SZ"}
    assert {t["ts_code"] for t in sections["avg_mv_at_least_300yi"]["trades"]} == {"000068.SZ", "000069.SZ"}
    assert sections["overall"]["summary"]["wins"] == 1
    assert sections["overall"]["summary"]["losses"] == 2


def test_suite_writes_exactly_four_md_html_reports_without_return_splits(tmp_path, lending_args):
    report = _multi_stock_backtest()
    sections = cli._build_backtest_sections(
        pd.DataFrame({"ts_code": ["000068.SZ", "000069.SZ"]}), report, args=lending_args,
    )
    result = save_short_trade_backtest_sections(sections, records_root=tmp_path / "reports")
    index = Path(result["index"])
    html = index.read_text(encoding="utf-8")
    assert "20261009" in html
    assert len(list(index.parent.rglob("*.md"))) == 4
    assert len(list(index.parent.rglob("*.html"))) == 5
    assert not (index.parent / "backtests").exists()
    assert all(set(paths) == {"all"} for paths in result["sections"].values())
    for href in re.findall(r'href="([^"]+)"', html):
        assert (index.parent / href).is_file()
    overall = Path(result["sections"]["overall"]["all"])
    assert "收益分组" not in overall.read_text(encoding="utf-8")
    assert "REVERSAL_CONFIRMED" in overall.read_text(encoding="utf-8")
    assert "已完成：6" in overall.read_text(encoding="utf-8")


def test_missing_lending_is_explicit(tmp_path):
    args = Namespace(date="20261016", lending_file=None, lending_root=str(tmp_path))
    report = _multi_stock_backtest()
    sections = cli._build_backtest_sections(pd.DataFrame({"ts_code": []}), report, args=args)
    assert "lendable" not in sections
    result = save_short_trade_backtest_sections(
        sections, records_root=tmp_path, missing_lending_note="未找到融券名单",
    )
    assert "未找到融券名单" in Path(result["index"]).read_text(encoding="utf-8")


def test_future_snapshot_is_rejected(lending_args):
    lending_args.date = "20261008"
    lending_args.lending_file = str(Path(lending_args.lending_root) / "20261009.tsv")
    with pytest.raises(ValueError, match="on or before"):
        cli._build_backtest_sections(
            pd.DataFrame({"ts_code": []}), _multi_stock_backtest(), args=lending_args,
        )


def test_explicit_missing_lending_file_does_not_silently_skip(tmp_path):
    args = Namespace(date="20261016", lending_file=str(tmp_path / "missing.tsv"), lending_root=str(tmp_path))
    with pytest.raises(FileNotFoundError):
        cli._build_backtest_sections(pd.DataFrame({"ts_code": []}), _multi_stock_backtest(), args=args)


def test_cli_defaults_to_whole_universe_and_preserves_explicit_selection(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["cli"])
    args = cli.parse_args()
    assert args.stock_code is None
    assert args.start_date == "20240901"
    monkeypatch.setattr(sys, "argv", [
        "cli", "--start-date", "20250101", "--date", "20261016",
        "--stock-code", "000068.SZ", "--stock-code", "000069.SZ",
    ])
    args = cli.parse_args()
    assert args.stock_code == ["000068.SZ", "000069.SZ"]
    assert args.start_date == "20250101"
    assert args.date == "20261016"
