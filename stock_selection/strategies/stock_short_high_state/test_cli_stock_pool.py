"""Check CLI stock selection through saved states and backtest reports."""

from pathlib import Path
import json

import pandas as pd
import pytest

from stock_selection.strategies.stock_short_high_state import cli


REPORT_DATE = "20261009"
ALL_CODES = {f"00000{number}.SZ" for number in range(1, 6)}
LENDABLE_CODES = {"000001.SZ", "000002.SZ", "000003.SZ", "000005.SZ"}


@pytest.fixture
def inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    # Use isolated date-based discovery instead of the user's default snapshot.
    monkeypatch.setattr(cli, "DEFAULT_LENDING_FILE", None)
    cache_root = tmp_path / "cache"
    daily_root = cache_root / "daily"
    daily_root.mkdir(parents=True)
    # Short fixtures exercise stock pools and industry gating with the cost filter disabled.
    closes = [60, 50, 55, 100, 89, 81, 82, 83]
    dates = pd.bdate_range(end=REPORT_DATE, periods=len(closes)).strftime("%Y%m%d")
    daily = pd.concat([
        pd.DataFrame({
            "ts_code": code,
            "name": f"测试股票{code}",
            "trade_date": dates,
            "open": closes,
            "high": [close + 1 for close in closes],
            "close": closes,
            "vol": 1000,
        })
        for code in sorted(ALL_CODES)
    ], ignore_index=True)
    daily.to_parquet(daily_root / f"{REPORT_DATE}.parquet", index=False)
    lending_root = tmp_path / "lending"
    lending_root.mkdir()
    lending_file = lending_root / f"{REPORT_DATE}.xls"
    lending_file.write_text(
        "市场类别\t证券代码\t证券名称\t可用数量\t保证金比例(%)\t融券状态\n"
        "深圳Ａ\t000001\t测试一\t100\t100\t正常\n"
        "深圳Ａ\t000002\t测试二\t0\t100\t正常\n"
        "深圳Ａ\t000003\t测试三\t100\t100\t暂停\n"
        "深圳Ａ\t000005\t测试五\t200\t100\t正常\n",
        encoding="gb18030",
    )
    return {
        "cache": cache_root,
        "lending": lending_root,
        "lending_file": lending_file,
        "reports": tmp_path / "reports",
    }


def _argv(inputs: dict[str, Path], *extra: str) -> list[str]:
    return [
        "cli", "--date", REPORT_DATE,
        "--industry-filter", "False",
        "--cache-root", str(inputs["cache"]),
        "--lending-root", str(inputs["lending"]),
        "--records-root", str(inputs["reports"]),
        "--lookback-days", "3", "--high-heat-days", "2",
        "--min-breakout-gain-pct", "0",
        *extra,
    ]


@pytest.mark.parametrize(
    ("extra", "expected_codes"),
    [
        ([], LENDABLE_CODES),
        (["--all-stocks", "True"], ALL_CODES),
        (["--all-stocks", "False"], LENDABLE_CODES),
        (["--all-stocks", "false", "--stock-code", "000004.SZ",
          "--stock-code", "000005.SZ"], {"000005.SZ"}),
        (["--all-stocks", "True", "--stock-code", "000004.SZ"], {"000004.SZ"}),
        (["--all-stocks", "False", "--stock-code", "000004.SZ"], set()),
    ],
)
def test_stock_selection_applies_to_saved_states_and_main_backtest(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch,
    extra: list[str], expected_codes: set[str],
) -> None:
    monkeypatch.setattr("sys.argv", _argv(inputs, *extra))

    assert cli.main() == 0

    state_path, = inputs["reports"].rglob("states.csv")
    # All reports from one invocation must share this single batch directory.
    assert list(inputs["reports"].glob("*/charts_*")) == [state_path.parent]
    states = pd.read_csv(state_path, dtype={"ts_code": str, "trade_date": str})
    assert set(states["ts_code"]) == expected_codes
    assert states["trough_ma_days"].eq(8).all()
    assert len(states) == 8 * len(expected_codes)
    report = (state_path.parent / "short_trade_backtest.md").read_text(encoding="utf-8")
    for code in ALL_CODES:
        assert (code in report) == (code in expected_codes)
    stock_list = pd.read_csv(state_path.parent / "shortable_stocks.csv")
    assert set(stock_list["ts_code"]) == expected_codes.intersection(LENDABLE_CODES)
    assert set(stock_list["stage"]) <= {"PULLBACK"}
    for suffix in ("md", "html"):
        assert (state_path.parent / f"shortable_stocks.{suffix}").is_file()


def test_all_stocks_does_not_require_lending_file(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs["lending_file"].unlink()
    monkeypatch.setattr("sys.argv", _argv(inputs, "--all-stocks", "True"))

    assert cli.main() == 0

    state_path, = inputs["reports"].rglob("states.csv")
    assert set(pd.read_csv(state_path)["ts_code"]) == ALL_CODES
    assert not (state_path.parent / "shortable_stocks.csv").exists()


def test_lendable_only_requires_available_file_before_loading_daily(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs["lending_file"].unlink()
    monkeypatch.setattr("sys.argv", _argv(inputs, "--all-stocks", "False"))

    def unexpected_daily_load(**_kwargs):
        pytest.fail("Missing lending input should fail before loading daily data.")

    monkeypatch.setattr(cli, "read_cached_stock_daily", unexpected_daily_load)

    with pytest.raises(FileNotFoundError, match="--all-stocks False requires"):
        cli.main()
    assert not inputs["reports"].exists()


def test_lendable_only_rejects_explicit_file_from_future_date(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrong_date_file = inputs["lending_file"].rename(inputs["lending"] / "20261010.xls")
    monkeypatch.setattr("sys.argv", _argv(
        inputs, "--all-stocks", "False", "--lending-file", str(wrong_date_file),
    ))

    with pytest.raises(ValueError, match="lending data date must be on or before"):
        cli.main()
    assert not inputs["reports"].exists()


@pytest.mark.parametrize("explicit_file", [False, True])
def test_older_lending_export_keeps_report_and_snapshot_dates_separate(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, explicit_file: bool,
) -> None:
    old_file = inputs["lending_file"].rename(inputs["lending"] / "20261008.xls")
    # Directory discovery must select the old file even if a future file exists.
    (inputs["lending"] / "20261010.xls").write_bytes(old_file.read_bytes())
    extra = ["--lending-file", str(old_file)] if explicit_file else []
    monkeypatch.setattr("sys.argv", _argv(inputs, *extra))

    assert cli.main() == 0

    state_path, = inputs["reports"].rglob("states.csv")
    states = pd.read_csv(state_path, dtype={"trade_date": str})
    assert set(states["ts_code"]) == LENDABLE_CODES
    assert states["trade_date"].max() == REPORT_DATE
    stock_list = pd.read_csv(state_path.parent / "shortable_stocks.csv", dtype={"lending_date": str})
    assert stock_list["lending_date"].eq("20261008").all()
    for suffix in ("md", "html"):
        report = (state_path.parent / f"short_trade_backtest_lendable_{REPORT_DATE}.{suffix}").read_text(encoding="utf-8")
        assert "20261008可融券股票池" in report
        assert str(old_file) in report
        assert "按报告日融券快照" not in report


def test_lendable_only_excludes_other_stocks_before_state_calculation(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily_path = inputs["cache"] / "daily" / f"{REPORT_DATE}.parquet"
    daily = pd.read_parquet(daily_path)
    daily.loc[~daily["ts_code"].isin(LENDABLE_CODES), "high"] = 0
    daily.to_parquet(daily_path, index=False)
    monkeypatch.setattr("sys.argv", _argv(inputs, "--all-stocks", "False"))

    assert cli.main() == 0

    state_path, = inputs["reports"].rglob("states.csv")
    assert set(pd.read_csv(state_path)["ts_code"]) == LENDABLE_CODES


def test_cli_rejects_invalid_boolean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["cli", "--all-stocks", "maybe"])
    with pytest.raises(SystemExit) as exc:
        cli.parse_args()
    assert exc.value.code == 2


def test_cli_passes_ma_window_to_states_and_reports(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.argv", _argv(inputs, "--trough-ma-days", "2"))

    assert cli.main() == 0

    state_path, = inputs["reports"].rglob("states.csv")
    states = pd.read_csv(state_path)
    assert states["trough_ma_days"].eq(2).all()
    assert "trough_up_days" not in states.columns
    last = states[states["trade_date"].eq(int(REPORT_DATE))]
    assert last["trough_ma_close"].eq(82.5).all()
    report = (state_path.parent / "short_trade_backtest.md").read_text(encoding="utf-8")
    assert "MA2" in report


@pytest.mark.parametrize("use_default_file", [False, True])
@pytest.mark.parametrize("snapshot_date", [REPORT_DATE, "20261008"])
def test_htsc_csv_filters_states_and_reports_with_correct_quantity_rule(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch,
    use_default_file: bool, snapshot_date: str,
) -> None:
    inventory_path = inputs["lending"] / f"{snapshot_date}.csv"
    pd.DataFrame({
        "trade_date": snapshot_date,
        "ts_code": ["000001.SZ", "000002.SZ", "000005.SZ"],
        "lendable_qty": ["1,000", "0", "200"],
    }).to_csv(inventory_path, index=False, encoding="utf-8-sig")
    extra = ["--all-stocks", "False"]
    if use_default_file:
        monkeypatch.setattr(cli, "DEFAULT_LENDING_FILE", inventory_path)
    else:
        extra += ["--lending-file", str(inventory_path)]
    monkeypatch.setattr("sys.argv", _argv(inputs, *extra))

    assert cli.main() == 0

    state_path, = inputs["reports"].rglob("states.csv")
    states = pd.read_csv(state_path)
    expected_codes = {"000001.SZ", "000002.SZ", "000005.SZ"}
    assert set(states["ts_code"]) == expected_codes
    assert str(states["trade_date"].max()) == REPORT_DATE
    stock_list = pd.read_csv(state_path.parent / "shortable_stocks.csv", dtype={"lending_date": str})
    assert set(stock_list["ts_code"]) == expected_codes
    assert stock_list.set_index("ts_code")["lendable_qty"].to_dict() == {
        "000001.SZ": 1000, "000002.SZ": 0, "000005.SZ": 200,
    }
    assert stock_list["lending_date"].eq(snapshot_date).all()
    for suffix in ("md", "html"):
        path = state_path.parent / f"short_trade_backtest_lendable_{REPORT_DATE}.{suffix}"
        report = path.read_text(encoding="utf-8")
        assert "不按可融券数量筛选" in report
        assert str(inventory_path) in report
        assert "000002.SZ" in report
        assert f"{snapshot_date}可融券股票池" in report


def test_htsc_csv_rejects_mixed_snapshot_dates(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory_path = inputs["lending"] / "mixed.csv"
    pd.DataFrame({
        "trade_date": ["20261008", REPORT_DATE],
        "ts_code": ["000001.SZ", "000005.SZ"],
        "lendable_qty": [100, 200],
    }).to_csv(inventory_path, index=False, encoding="utf-8-sig")
    monkeypatch.setattr("sys.argv", _argv(inputs, "--lending-file", str(inventory_path)))

    with pytest.raises(ValueError, match="exactly one snapshot date"):
        cli.main()
    assert not inputs["reports"].exists()


@pytest.mark.parametrize("quantity", ["--", "-1", "1.5"])
def test_htsc_csv_rejects_invalid_quantities_before_loading_daily(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, quantity: str,
) -> None:
    inventory_path = inputs["lending"] / f"{REPORT_DATE}.csv"
    pd.DataFrame({
        "trade_date": [REPORT_DATE], "ts_code": ["000001.SZ"],
        "lendable_qty": [quantity],
    }).to_csv(inventory_path, index=False, encoding="utf-8-sig")
    monkeypatch.setattr("sys.argv", _argv(inputs, "--lending-file", str(inventory_path)))

    with pytest.raises(ValueError, match="lendable_qty"):
        cli.main()
    assert not inputs["reports"].exists()


@pytest.mark.parametrize("all_stocks", [True, False])
def test_latest_lending_roster_includes_zero_and_failed_queries_in_states_and_reports(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, all_stocks: bool,
) -> None:
    inputs["lending_file"].unlink()
    # The preceding file must not leak an older positive quantity into this run.
    pd.DataFrame({
        "trade_date": ["20261008"], "ts_code": ["000001.SZ"], "lendable_qty": [1000],
    }).to_csv(inputs["lending"] / "20261008.csv", index=False)
    latest = inputs["lending"] / f".{REPORT_DATE}.partial.csv"
    pd.DataFrame({
        "trade_date": REPORT_DATE, "ts_code": ["000001.SZ", "000002.SZ", "000005.SZ"],
        "lendable_qty": [0, 2000, 3000], "actual_sellable_qty": ["0", "", "200"],
        "verification_status": ["zero", "error", "available"],
        "queried_at": "2026-10-09T10:00:00+08:00", "verification_error": ["", "timeout", ""],
    }).to_csv(latest, index=False)
    monkeypatch.setattr("sys.argv", _argv(inputs, "--all-stocks", str(all_stocks)))
    assert cli.main() == 0
    state_path, = inputs["reports"].rglob("states.csv")
    states = pd.read_csv(state_path)
    expected_codes = {"000001.SZ", "000002.SZ", "000005.SZ"}
    assert set(states["ts_code"]) == (ALL_CODES if all_stocks else expected_codes)
    stock_list = pd.read_csv(state_path.parent / "shortable_stocks.csv")
    rows = stock_list.set_index("ts_code")
    assert set(rows.index) == expected_codes
    assert rows.loc["000001.SZ", "lendable_qty"] == 0
    assert rows.loc["000001.SZ", "actual_sellable_qty"] == 0
    assert rows.loc["000001.SZ", "verification_status"] == "zero"
    assert pd.isna(rows.loc["000002.SZ", "actual_sellable_qty"])
    assert rows.loc["000002.SZ", "verification_status"] == "error"
    assert rows.loc["000002.SZ", "verification_error"] == "timeout"
    assert rows.loc["000005.SZ", "lendable_qty"] == 3000
    assert rows.loc["000005.SZ", "actual_sellable_qty"] == 200
    assert rows.loc["000005.SZ", "verification_status"] == "available"
    for suffix in ("md", "html"):
        backtest = (state_path.parent / f"short_trade_backtest_lendable_{REPORT_DATE}.{suffix}").read_text(encoding="utf-8")
        assert "不按可融数量、实际可融数量或核验状态筛选" in backtest
        for code in ALL_CODES:
            assert (code in backtest) == (code in expected_codes)
    html = (state_path.parent / "shortable_stocks.html").read_text(encoding="utf-8")
    assert "实际可融数量（股）" in html
    assert "核验无券" in html and "查询失败" in html and "未知" in html
    assert "不按数量或核验状态筛选" in html
    assert "均未纳入" not in html
    assert "扫描尚未完成" in html
    assert str(latest) in html


@pytest.mark.parametrize(("status", "quantity"), [("zero", "0"), ("error", "")])
def test_lending_pool_with_no_positive_quantities_still_calculates_states_and_trades(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, status: str, quantity: str,
) -> None:
    snapshot = inputs["lending"] / f"{REPORT_DATE}.csv"
    pd.DataFrame({
        "trade_date": [REPORT_DATE], "ts_code": ["000002.SZ"],
        "lendable_qty": [0], "actual_sellable_qty": [quantity],
        "verification_status": [status], "queried_at": ["2026-10-09T10:00:00+08:00"],
    }).to_csv(snapshot, index=False)
    monkeypatch.setattr("sys.argv", _argv(inputs, "--lending-file", str(snapshot)))
    assert cli.main() == 0
    state_path, = inputs["reports"].rglob("states.csv")
    assert set(pd.read_csv(state_path)["ts_code"]) == {"000002.SZ"}
    stock_list = pd.read_csv(state_path.parent / "shortable_stocks.csv")
    assert stock_list["ts_code"].tolist() == ["000002.SZ"]
    assert stock_list["verification_status"].tolist() == [status]
    report = (state_path.parent / "short_trade_backtest.md").read_text(encoding="utf-8")
    assert "000002.SZ" in report


def test_cli_passes_configurable_breakout_gain_to_states_and_reports(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.argv", _argv(inputs, "--min-breakout-gain-pct", "0.8", "--cost-peak-window-days", "5"))
    assert cli.main() == 0
    state_path, = inputs["reports"].rglob("states.csv")
    states = pd.read_csv(state_path)
    assert states["min_breakout_gain_pct"].eq(0.8).all()
    assert states["cost_peak_window_days"].eq(5).all()
    assert states["entry_signal"].eq(0).all()
    report = (state_path.parent / "short_trade_backtest.md").read_text(encoding="utf-8")
    assert "80.00%" in report
    assert "极值窗口共5日" in report


def test_cli_sample_list_stays_in_one_run_with_all_its_reports(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.argv", _argv(inputs, "--sample-chart-codes"))
    assert cli.main() == 0
    state_path, = inputs["reports"].rglob("states.csv")
    assert list(inputs["reports"].glob("*/charts_*")) == [state_path.parent]
    codes_file = state_path.parent / "chart_codes.txt"
    sampled = set(codes_file.read_text(encoding="utf-8").splitlines())
    assert sampled == LENDABLE_CODES
    snapshot, = state_path.parent.glob("sampled_chart_codes_*.txt")
    assert snapshot.read_bytes() == codes_file.read_bytes()
    for path in inputs["reports"].rglob("short_trade_backtest*.md"):
        assert path.parent == state_path.parent


@pytest.mark.parametrize("filter_enabled", [True, False])
@pytest.mark.parametrize(("signal_day_industry_close", "expected_entries"), [(90, 4), (107, 0), (100, 0)])
def test_cli_industry_gate_writes_matching_reports_and_same_run_charts(
    inputs, monkeypatch, signal_day_industry_close, expected_entries, filter_enabled,
):
    from stock_selection.strategies.stock_short_high_state.backtest import build_short_trade_backtest
    from stock_selection.strategies.stock_short_high_state.visualization import generate_stock_short_state_charts, read_saved_states

    static = inputs["cache"] / "static"
    static.mkdir()
    (static / "sw_industry_stock_dictionary.json").write_text(json.dumps({
        "level2_index": {"801011.SI": {"name": "测试行业", "stock_codes": sorted(ALL_CODES)}},
    }), encoding="utf-8")
    stock_bars = pd.read_parquet(inputs["cache"] / "daily" / f"{REPORT_DATE}.parquet")
    stock_bars["low"] = stock_bars["close"] - 1
    stock_bars.to_parquet(inputs["cache"] / "daily" / f"{REPORT_DATE}.parquet", index=False)
    stock_dates = sorted(stock_bars.trade_date.unique())
    dates = pd.bdate_range(end=REPORT_DATE, periods=14).strftime("%Y%m%d")
    industry_root = inputs["cache"] / "sw_daily"
    industry_root.mkdir()
    pd.DataFrame({
        "ts_code": "801011.SI", "trade_date": dates,
        "close": [100] * 10 + [signal_day_industry_close, 90, 89, 88],
    }).to_parquet(industry_root / f"{REPORT_DATE}.parquet", index=False)
    monkeypatch.setattr("sys.argv", _argv(inputs, "--industry-filter", str(filter_enabled), "--sample-chart-codes"))
    assert cli.main() == 0
    path, = inputs["reports"].rglob("states.csv")
    states = read_saved_states(path)
    assert states.industry_filter_enabled.eq(filter_enabled).all()
    if not filter_enabled:
        expected_entries = len(LENDABLE_CODES)
        assert states.entry_industry_qualified.all()
        assert states.entry_blocked_reason.eq("").all()
    assert set(states.ts_code) == LENDABLE_CODES
    assert states.industry_data_status.eq("OK").all()
    industry_states = pd.read_csv(path.parent / "industry_states.csv", dtype={"industry_trade_date": str})
    assert industry_states.industry_trade_date.min() == stock_dates[0]
    assert industry_states.industry_observations.eq(7).all()
    result = build_short_trade_backtest(states, as_of_date=REPORT_DATE)
    assert len(result["trades"]) == expected_entries
    blocked = pd.read_csv(path.parent / "industry_blocked_entries.csv")
    assert len(blocked) == 4 - expected_entries
    assert set(blocked.columns) >= {"entry_blocked_reason", "industry_state", "industry_rebound_pct"}
    report = (path.parent / "short_trade_backtest.html").read_text(encoding="utf-8")
    if filter_enabled:
        assert "二级行业过滤" in report and "6.00%" in report
        assert "当前申万二级行业快照" in report
    else:
        assert "二级行业开仓过滤已关闭" in report
    assert "industry_blocked_entries.csv" in report
    stock_list = pd.read_csv(path.parent / "shortable_stocks.csv")
    assert stock_list.industry_code.eq("801011.SI").all()
    charts = generate_stock_short_state_charts(states_csv=path, stock_codes=["000001.SZ"], cache_root=inputs["cache"])
    assert charts["errors"] == []
    assert charts["output_directory"] != str(path.parent)
    assert Path(charts["output_directory"]).parent == path.parent.parent
    assert set(inputs["reports"].glob("*/charts_*")) == {path.parent, Path(charts["output_directory"])}
    chart_path = Path(charts["output_directory"]) / charts["charts"][0]["filename"]
    html = chart_path.read_text(encoding="utf-8")
    assert json.dumps("二级行业收盘价")[1:-1] in html


def test_cli_cost_region_filter_produces_auditable_entries_and_matching_stock_list(inputs, monkeypatch):
    from stock_selection.strategies.stock_short_high_state.backtest import build_short_trade_backtest
    from stock_selection.strategies.stock_short_high_state.visualization import read_saved_states

    history = sum(([80] * 8 + [peak] + [80] * 7 for peak in (100, 110, 120)), [])
    closes = history + [170, 153]
    dates = pd.bdate_range(end=REPORT_DATE, periods=len(closes)).strftime("%Y%m%d")
    daily = pd.concat([
        pd.DataFrame({"ts_code": code, "name": f"股票{code}", "trade_date": dates,
                      "open": closes, "high": [value + 1 for value in closes],
                      "close": closes, "vol": 1000})
        for code in ALL_CODES
    ], ignore_index=True)
    daily.to_parquet(inputs["cache"] / "daily" / f"{REPORT_DATE}.parquet", index=False)
    monkeypatch.setattr("sys.argv", _argv(inputs, "--lookback-days", "48", "--min-breakout-gain-pct", "0.5"))
    assert cli.main() == 0
    path, = inputs["reports"].rglob("states.csv")
    states = read_saved_states(path)
    result = build_short_trade_backtest(states, as_of_date=REPORT_DATE)
    assert len(result["trades"]) == len(LENDABLE_CODES)
    assert {trade["entry_breakout_base_close"] for trade in result["trades"]} == {110}
    assert {trade["entry_cost_peak_count"] for trade in result["trades"]} == {3}
    assert result["metadata"]["entry_rule"]["cost_peak_window_days"] == 16
    stock_list = pd.read_csv(path.parent / "shortable_stocks.csv")
    assert stock_list["cost_zone_frozen"].all()
    assert stock_list["breakout_base_close"].eq(110).all()
    assert stock_list["entry_breakout_reason"].eq("QUALIFIED").all()
    html = (path.parent / "shortable_stocks.html").read_text(encoding="utf-8")
    assert "成本均值" in html and "冻结日期" in html and "110.00" in html
