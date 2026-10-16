from pathlib import Path
import re

import pandas as pd
import pytest

from stock_selection.strategies.stock_short_high_state.screen import StockShortState
from stock_selection.strategies.stock_short_high_state.visualization import (
    SHORT_FILL_COLOR,
    build_short_state_intervals,
    build_stock_short_state_figure,
    generate_stock_short_state_charts,
    merge_bars_and_states,
    normalise_stock_code,
    read_chart_codes,
    resolve_states_csv,
)


pytest.importorskip("plotly")


def _states() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ"] * 5,
            "stock_name": ["测试股票"] * 5,
            "trade_date": [
                "20260105",
                "20260106",
                "20260107",
                "20260108",
                "20260109",
            ],
            "close": [10.5, 11.5, 11.0, 12.0, 11.0],
            "total_mv": [120000, 121000, 122000, 123000, 123456],
            "circ_mv": [90000, 91000, 92000, 93000, 100000],
            "state": [
                int(StockShortState.NORMAL),
                int(StockShortState.HIGH_CANDIDATE),
                int(StockShortState.HIGH_CANDIDATE),
                int(StockShortState.PEAK_1),
                int(StockShortState.INVALID_HIGH_NEW_HIGH),
            ],
            "state_name": [
                "NORMAL",
                "HIGH_CANDIDATE",
                "HIGH_CANDIDATE",
                "PEAK_1",
                "INVALID_HIGH_NEW_HIGH",
            ],
            "state_description": [
                "普通状态",
                "最高点候选区域",
                "最高点候选区域",
                "第一波峰区域",
                "最高点确认后再次创新高，原判断失效",
            ],
            "cycle_id": [0, 1, 1, 1, 1],
            "peak_number": [0, 0, 0, 1, 1],
            "heat": [None, 0.1, 0.12, 0.11, None],
            "reference_high_date": ["", "20260106", "20260107", "20260107", "20260107"],
            "reference_high_close": [None, 11.5, 11.5, 11.5, 11.5],
            "reference_low_date": ["", "", "", "20260105", "20260105"],
            "reference_low_close": [None, None, None, 10.5, 10.5],
            "alert_code": ["", "", "", "", "CONFIRMED_HIGH_BROKEN"],
        }
    )


def _bars() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": [
                "20260105",
                "20260106",
                "20260107",
                "20260108",
                "20260109",
            ],
            "open": [10.0, 11.0, 11.5, 11.2, 11.8],
            "high": [11.0, 12.0, 12.0, 12.5, 12.0],
            "low": [9.8, 10.8, 10.7, 11.0, 10.8],
            "close": [10.5, 11.5, 11.0, 12.0, 11.0],
            "vol": [100, 120, 90, 150, 180],
        }
    )


def test_reads_codes_with_comments_suffixes_and_duplicates(tmp_path):
    path = tmp_path / "codes.txt"
    path.write_text(
        "# test\n000001\n600000.SH\n000001.SZ\nbad\n",
        encoding="utf-8",
    )

    codes, errors = read_chart_codes(path)

    assert codes == ["000001.SZ", "600000.SH"]
    assert len(errors) == 1
    assert normalise_stock_code("300750") == "300750.SZ"


def test_resolves_dated_states_csv_inside_strategy_output(tmp_path):
    path = tmp_path / "2026-09-24" / "states.csv"
    path.parent.mkdir()
    path.write_text("test", encoding="utf-8")

    assert resolve_states_csv(
        as_of_date="20260924",
        output_root=tmp_path,
    ) == path


def test_builds_separate_intervals_when_short_state_changes():
    intervals = build_short_state_intervals(_states())

    assert [(item.state_name, item.trading_days) for item in intervals] == [
        ("HIGH_CANDIDATE", 2),
        ("PEAK_1", 1),
    ]


def test_merges_saved_states_with_ohlc_and_checks_close():
    merged = merge_bars_and_states(_bars(), _states())

    assert len(merged) == 5
    assert merged["close"].tolist() == _states()["close"].tolist()
    assert merged["date"].iloc[0] == pd.Timestamp("2026-01-05")


def test_figure_keeps_red_green_candles_and_adds_yellow_regions():
    merged = merge_bars_and_states(_bars(), _states())
    figure = build_stock_short_state_figure(
        merged,
        stock_code="000001.SZ",
        stock_name="测试股票",
    )

    candle = figure.data[0]
    assert candle.increasing.line.color == "#d84a3a"
    assert candle.decreasing.line.color == "#169c72"
    assert any(shape.fillcolor == SHORT_FILL_COLOR for shape in figure.layout.shapes)
    assert any(trace.name == "做空区域开始" for trace in figure.data)
    assert any(trace.name == "策略异常" for trace in figure.data)
    assert "12.35亿元" in figure.layout.title.text
    assert "10.00亿元" in figure.layout.title.text


@pytest.fixture
def chart_inputs(tmp_path):
    pytest.importorskip("pyarrow")
    stock_states = []
    stock_bars = []
    for code, final_close in (
        ("000001.SZ", 11.0),
        ("600000.SH", 13.0),
        ("600001.SH", 12.0),
        ("300750.SZ", None),
    ):
        states = _states()
        states["ts_code"] = code
        if final_close is None:
            states["state"] = int(StockShortState.NORMAL)
            states["state_name"] = "NORMAL"
            states["state_description"] = "普通状态"
            states["cycle_id"] = 0
            states["peak_number"] = 0
            states["alert_code"] = ""
        else:
            states.loc[states.index[-1], "close"] = final_close
            states.loc[states.index[-2], "state"] = int(
                StockShortState.HIGH_CANDIDATE_PULLBACK
            )
            states.loc[states.index[-2], "state_name"] = "HIGH_CANDIDATE_PULLBACK"
            states.loc[states.index[-2], "state_description"] = "最高点候选回调区域"
        stock_states.append(states)
        bars = _bars()
        bars["ts_code"] = code
        bars["close"] = states["close"]
        bars["open"] = bars["close"]
        bars["high"] = bars["close"] + 1
        bars["low"] = bars["close"] - 1
        stock_bars.append(bars)

    states_path = tmp_path / "states.csv"
    pd.concat(stock_states, ignore_index=True).to_csv(states_path, index=False)
    cache_root = tmp_path / "cache"
    daily = cache_root / "daily"
    daily.mkdir(parents=True)
    for date, bars in pd.concat(stock_bars, ignore_index=True).groupby("trade_date"):
        bars.to_parquet(daily / f"{date}.parquet", index=False)
    codes_path = tmp_path / "codes.txt"
    codes_path.write_text("", encoding="utf-8")
    return {
        "states_csv": states_path,
        "codes_file": codes_path,
        "cache_root": cache_root,
        "output_directory": tmp_path / "charts",
    }


@pytest.mark.parametrize("contents", ["", "\n \t\n", "\ufeff# 只含注释\n\n"])
def test_empty_code_file_charts_all_states_including_stocks_without_trades(
    chart_inputs, contents,
):
    chart_inputs["codes_file"].write_text(contents, encoding="utf-8")

    result = generate_stock_short_state_charts(**chart_inputs)

    charts = {item["ts_code"]: item for item in result["charts"]}
    assert set(charts) == {"000001.SZ", "600000.SH", "600001.SH", "300750.SZ"}
    assert result["errors"] == []
    assert charts["000001.SZ"]["return_category"] == "positive"
    assert charts["600000.SH"]["return_category"] == "negative"
    assert charts["600001.SH"]["return_category"] == "negative"
    assert charts["600001.SH"]["classified_return_pct"] == 0
    assert charts["300750.SZ"]["return_category"] == "unclassified"
    assert charts["300750.SZ"]["classified_return_pct"] is None
    index_path = Path(result["index"])
    index = index_path.read_text(encoding="utf-8")
    for chart in charts.values():
        assert (index_path.parent / chart["filename"]).is_file()
        assert f'href="{chart["filename"]}"' in index
    unclassified_index = Path(result["unclassified_index"])
    assert 'href="300750_SZ.html"' in unclassified_index.read_text(encoding="utf-8")
    assert (unclassified_index.parent / "plotly.min.js").is_file()


def test_nonempty_code_file_only_charts_requested_stocks(chart_inputs):
    chart_inputs["codes_file"].write_text(
        "300750\n300750.SZ\n", encoding="utf-8"
    )

    result = generate_stock_short_state_charts(**chart_inputs)

    assert [item["ts_code"] for item in result["charts"]] == ["300750.SZ"]
    assert result["errors"] == []


def test_invalid_only_code_file_does_not_fall_back_to_all_stocks(chart_inputs):
    chart_inputs["codes_file"].write_text("bad\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid stock code 'bad'"):
        generate_stock_short_state_charts(**chart_inputs)
    assert not chart_inputs["output_directory"].exists()


def test_missing_code_file_does_not_fall_back_to_all_stocks(chart_inputs, tmp_path):
    chart_inputs["codes_file"] = tmp_path / "missing.txt"

    with pytest.raises(FileNotFoundError, match="chart code file does not exist"):
        generate_stock_short_state_charts(**chart_inputs)


def test_nonfinite_backtest_return_still_generates_an_unclassified_chart(
    chart_inputs, monkeypatch,
):
    chart_inputs["codes_file"].write_text("000001.SZ\n", encoding="utf-8")
    monkeypatch.setattr(
        "stock_selection.strategies.stock_short_high_state.visualization."
        "build_short_trade_backtest",
        lambda *args, **kwargs: {
            "trades": [
                {
                    "ts_code": "000001.SZ",
                    "status": "OPEN",
                    "mark_return_pct": float("nan"),
                }
            ]
        },
    )

    result = generate_stock_short_state_charts(**chart_inputs)

    assert len(result["charts"]) == 1
    chart = result["charts"][0]
    assert chart["return_category"] == "unclassified"
    assert chart["classified_return_pct"] is None
    assert (Path(result["output_directory"]) / chart["filename"]).is_file()
    assert result["errors"] == []


def test_four_chart_scopes_classify_each_pool_and_include_zero_lending_quantity(
    chart_inputs, tmp_path,
):
    states = pd.read_csv(chart_inputs["states_csv"], dtype={"ts_code": str, "trade_date": str})
    chosen = states["ts_code"].eq("000001.SZ")
    states.loc[chosen, "close"] = [10, 7, 10, 11, 11]
    sequence = [StockShortState.HIGH_CANDIDATE_PULLBACK, StockShortState.TROUGH_CANDIDATE] * 2 + [StockShortState.NORMAL]
    states.loc[chosen, "state"] = [int(state) for state in sequence]
    states.loc[chosen, "state_name"] = [state.name for state in sequence]
    states.loc[chosen, "cycle_id"] = [1, 1, 2, 2, 0]
    states.loc[chosen, "peak_number"] = 0
    states.loc[chosen, "total_mv"] = [2_000_000, 2_000_000, 4_000_000, 4_000_000, 4_000_000]
    states.loc[states["ts_code"].eq("600000.SH"), "total_mv"] = 3_000_000
    states.to_csv(chart_inputs["states_csv"], index=False)
    for path in (chart_inputs["cache_root"] / "daily").glob("*.parquet"):
        bars = pd.read_parquet(path)
        price = states.loc[chosen & states["trade_date"].eq(path.stem), "close"].iloc[0]
        bars.loc[bars["ts_code"].eq("000001.SZ"), ["close", "open", "high", "low"]] = [price, price, price + 1, price - 1]
        bars.to_parquet(path, index=False)
    lending = tmp_path / "20260109.tsv"
    pd.DataFrame({
        "市场类别": ["深圳Ａ", "上海Ａ", "上海Ａ", "深圳Ａ"],
        "证券代码": ["000001", "600000", "600001", "300750"],
        "证券名称": ["测试"] * 4, "可用数量": [0, 100, 0, 0],
        "保证金比例(%)": [100] * 4, "融券状态": ["正常", "正常", "暂停", "正常"],
    }).to_csv(lending, sep="\t", index=False, encoding="gb18030")

    result = generate_stock_short_state_charts(**chart_inputs, lending_file=str(lending))
    assert result["errors"] == []
    assert set(result["sections"]) == {"overall", "avg_mv_below_300yi", "avg_mv_at_least_300yi", "lendable"}
    overall = {row["ts_code"]: row for row in result["charts"]}
    large = {row["ts_code"]: row for row in result["sections"]["avg_mv_at_least_300yi"]["charts"]}
    small = {row["ts_code"]: row for row in result["sections"]["avg_mv_below_300yi"]["charts"]}
    pool = {row["ts_code"]: row for row in result["sections"]["lendable"]["charts"]}
    assert overall["000001.SZ"]["return_category"] == "positive"
    assert large["000001.SZ"]["return_category"] == "negative"
    assert small["000001.SZ"]["return_category"] == "positive"
    assert "600000.SH" in large  # Exactly 300亿 belongs to the >= group.
    assert "000001.SZ" in pool  # Zero available quantity is still in the pool.
    assert "600001.SH" not in pool
    assert pool["300750.SZ"]["return_category"] == "unclassified"
    for section in result["sections"].values():
        for key in ("index", "positive_index", "negative_index", "unclassified_index"):
            entry = Path(section[key])
            for href in re.findall(r'href="([^"]+)"', entry.read_text(encoding="utf-8")):
                assert (entry.parent / href).is_file(), (entry, href)
