"""Chip changes preserve signed percentage mass and use cached trading days."""

import json

import numpy as np
import pandas as pd
import pytest

from stock_selection.data.tushare_cache import write_dataset
from stock_selection.data.tushare_chips import (
    NET_RULE, calculate_chip_net_change, collect_chip_changes,
    load_chip_net_payload, save_chip_codes,
)
from stock_selection.data.tushare_collector import TushareCollector


CODE = "603993.SH"


def snapshot(date, prices, percentages):
    return pd.DataFrame({"ts_code": CODE, "trade_date": date, "price": prices, "percent": percentages})


def test_subtraction_keeps_negative_values_without_range_normalization():
    result = calculate_chip_net_change(
        snapshot("20260129", [10., 11., 12.], [30., 50., 20.]),
        snapshot("20260130", [10., 11., 12.], [35., 40., 25.]),
        ts_code=CODE, trade_date="20260130", previous_trade_date="20260129", low=10., high=11., close=10.5,
    )
    assert result.price.tolist() == [10., 11.]
    assert result.net_change_pp.tolist() == [5., -10.]
    assert result.today_percent.sum() == 75
    assert result.previous_percent.sum() == 80
    assert result.rule.eq(NET_RULE).all()


def test_different_price_grids_conserve_mass_before_range_selection():
    result = calculate_chip_net_change(
        snapshot("20260129", [10., 10.2], [50., 50.]),
        snapshot("20260130", [10., 10.1, 10.2], [25., 50., 25.]),
        ts_code=CODE, trade_date="20260130", previous_trade_date="20260129", low=9., high=11., close=10.,
    )
    assert result.previous_percent.sum() == pytest.approx(100)
    assert result.today_percent.sum() == pytest.approx(100)
    assert result.net_change_pp.sum() == pytest.approx(0)
    assert result.net_change_pp.abs().max() < 1e-6


def test_one_price_day_without_a_source_price_is_not_fabricated():
    result = calculate_chip_net_change(
        snapshot("20260129", [10., 10.2], [50., 50.]),
        snapshot("20260130", [10., 10.2], [45., 55.]),
        ts_code=CODE, trade_date="20260130", previous_trade_date="20260129", low=10.1, high=10.1, close=10.1,
    )
    assert result.empty


@pytest.mark.parametrize("field,value", [("ts_code", "000001.SZ"), ("trade_date", "20260128")])
def test_other_stock_or_date_cannot_contaminate_a_snapshot(field, value):
    today = snapshot("20260130", [10.], [100.])
    today[field] = value
    with pytest.raises(ValueError, match="different"):
        calculate_chip_net_change(snapshot("20260129", [10.], [100.]), today,
                                  ts_code=CODE, trade_date="20260130", previous_trade_date="20260129", low=9., high=11., close=10.)


def make_collector(tmp_path, *, omit=None):
    # Friday -> Monday ensures comparison follows trading days, not yesterday's calendar date.
    for date in ["20260130", "20260202", "20260203"]:
        write_dataset(pd.DataFrame({"ts_code": [CODE], "trade_date": [date], "low": [10.], "high": [11.], "close": [10.5], "vol": [100]}),
                      "daily", date, cache_root=tmp_path)
    calls = []
    def caller(api, params):
        calls.append((api, params))
        return pd.concat([snapshot(date, [10., 11.], [40. + index, 60. - index])
                          for index, date in enumerate(["20260130", "20260202", "20260203"]) if date != omit], ignore_index=True)
    return TushareCollector(cache_root=tmp_path, caller=caller), calls


def test_backfill_uses_previous_trading_day_and_second_run_uses_cache(tmp_path):
    collector, calls = make_collector(tmp_path)
    result = collect_chip_changes(collector, ts_code=CODE, start_date="20260202", end_date="20260203")
    assert result["written_days"] == 2
    assert result["failures"] == {}
    payload = load_chip_net_payload(CODE, ["20260202", "20260203"], tmp_path)
    assert payload["20260202"]["previous_date"] == "20260130"
    assert payload["20260202"]["net"] == [1., -1.]
    assert all(params["ts_code"] == CODE for _, params in calls)
    calls.clear()
    again = collect_chip_changes(collector, ts_code=CODE, start_date="20260202", end_date="20260203")
    assert again["cached_days"] == 2
    assert not calls


def test_missing_middle_day_is_not_treated_as_zero_or_skipped_for_comparison(tmp_path):
    collector, _ = make_collector(tmp_path, omit="20260202")
    result = collect_chip_changes(collector, ts_code=CODE, start_date="20260202", end_date="20260203")
    assert set(result["failures"]) == {"20260202", "20260203"}
    assert load_chip_net_payload(CODE, ["20260202", "20260203"], tmp_path) == {}


def test_daily_collection_updates_only_configured_stock_even_if_daily_is_cached(tmp_path):
    collector, calls = make_collector(tmp_path)
    save_chip_codes([CODE], tmp_path)
    results = collector.collect_daily("20260202", api_names=["daily"], available_only=False)
    assert results[0].status == "skipped"
    assert len(calls) == 1
    assert calls[0][0] == "cyq_chips"
    assert calls[0][1]["ts_code"] == CODE
    assert load_chip_net_payload(CODE, ["20260202"], tmp_path)


def test_optional_chip_permission_failure_does_not_break_daily_cache(tmp_path):
    collector, _ = make_collector(tmp_path)
    save_chip_codes([CODE], tmp_path)
    def fail(api, params):
        raise Exception("permission denied")
    collector._caller = fail
    results = collector.collect_daily("20260202", api_names=["daily"], available_only=False)
    assert results[0].status == "skipped"
    record = json.loads((tmp_path / "_metadata/chip_runs.jsonl").read_text().splitlines()[-1])
    assert record["status"] == "failed"


def test_chart_script_contains_only_net_change_and_offline_date_controls():
    from stock_selection.strategies.stock_short_high_state.chip_detail import chip_detail_script
    script = chip_detail_script({"20260130": {"status": "OK", "net": [1., -1.]}}, [{"date": "20260130", "close": 10.}])
    assert "plotly_click" in script
    assert "selectDate(date)" in script
    assert "fetch(" not in script
    assert "estimated_new" not in script
    assert "未将缺失数据视为零" in script
