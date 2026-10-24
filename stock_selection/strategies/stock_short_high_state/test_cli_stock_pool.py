"""Check CLI stock selection through saved states and backtest reports."""

from pathlib import Path

import pandas as pd
import pytest

from stock_selection.strategies.stock_short_high_state import cli


REPORT_DATE = "20261009"
ALL_CODES = {f"00000{number}.SZ" for number in range(1, 6)}
LENDABLE_CODES = {"000001.SZ", "000005.SZ"}


@pytest.fixture
def inputs(tmp_path: Path) -> dict[str, Path]:
    cache_root = tmp_path / "cache"
    daily_root = cache_root / "daily"
    daily_root.mkdir(parents=True)
    closes = [70, 80, 90, 100, 89, 81, 82, 83]
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
        "--cache-root", str(inputs["cache"]),
        "--lending-root", str(inputs["lending"]),
        "--records-root", str(inputs["reports"]),
        "--lookback-days", "3", "--high-heat-days", "2",
        *extra,
    ]


@pytest.mark.parametrize(
    ("extra", "expected_codes"),
    [
        ([], ALL_CODES),
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
    states = pd.read_csv(state_path, dtype={"ts_code": str, "trade_date": str})
    assert set(states["ts_code"]) == expected_codes
    assert len(states) == 8 * len(expected_codes)
    report = (state_path.parent / "short_trade_backtest.md").read_text(encoding="utf-8")
    for code in ALL_CODES:
        assert (code in report) == (code in expected_codes)


def test_all_stocks_does_not_require_lending_file(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs["lending_file"].unlink()
    monkeypatch.setattr("sys.argv", _argv(inputs, "--all-stocks", "True"))

    assert cli.main() == 0

    state_path, = inputs["reports"].rglob("states.csv")
    assert set(pd.read_csv(state_path)["ts_code"]) == ALL_CODES


def test_lendable_only_requires_report_date_file_before_loading_daily(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs["lending_file"].rename(inputs["lending"] / "20261008.xls")
    monkeypatch.setattr("sys.argv", _argv(inputs, "--all-stocks", "False"))

    def unexpected_daily_load(**_kwargs):
        pytest.fail("Missing lending input should fail before loading daily data.")

    monkeypatch.setattr(cli, "read_cached_stock_daily", unexpected_daily_load)

    with pytest.raises(FileNotFoundError, match="--all-stocks False requires"):
        cli.main()
    assert not inputs["reports"].exists()


def test_lendable_only_rejects_explicit_file_from_another_date(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrong_date_file = inputs["lending_file"].rename(inputs["lending"] / "20261008.xls")
    monkeypatch.setattr("sys.argv", _argv(
        inputs, "--all-stocks", "False", "--lending-file", str(wrong_date_file),
    ))

    with pytest.raises(ValueError, match="lending data date must match"):
        cli.main()
    assert not inputs["reports"].exists()


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
