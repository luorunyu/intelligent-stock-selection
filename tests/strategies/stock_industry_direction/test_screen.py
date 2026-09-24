import pandas as pd
import pytest

from stock_selection.strategies.stock_industry_direction.screen import (
    build_stock_industry_memberships,
    calculate_stock_industry_direction,
    read_cached_direction_inputs,
    screen_stock_industry_direction,
)


def _dictionary(stock_codes=None):
    codes = stock_codes or ["000001.SZ"]
    return {
        "level2_index": {
            "801010.SI": {"name": "行业二级", "stock_codes": codes},
        },
        "level3_index": {
            "850101.SI": {"name": "行业三级A", "stock_codes": codes},
            "850102.SI": {"name": "行业三级B", "stock_codes": ["000001.SZ"]},
        },
    }


def _frames(stock_returns, industry_returns, *, stock_name="样例股份"):
    dates = pd.date_range("2026-01-01", periods=len(industry_returns), freq="B").strftime("%Y%m%d")
    stock = pd.DataFrame(
        {
            "ts_code": "000001.SZ",
            "name": stock_name,
            "trade_date": dates,
            "pct_chg": stock_returns,
        }
    )
    industry = pd.DataFrame(
        {
            "ts_code": "801010.SI",
            "trade_date": dates,
            "pct_change": industry_returns,
        }
    )
    return stock, industry


def test_calculates_same_direction_ratio_and_excludes_zero_days():
    stock, industry = _frames(
        [1, -1, 1, -1, 0, 1],
        [2, -2, -2, 2, 1, 0],
    )

    row = calculate_stock_industry_direction(
        stock,
        industry,
        _dictionary(),
        level="L2",
        window=6,
    ).iloc[0]

    assert row["data_quality"] == "ok"
    assert row["observations"] == 6
    assert row["direction_observations"] == 4
    assert row["same_direction_days"] == 2
    assert row["same_up_days"] == 1
    assert row["same_down_days"] == 1
    assert row["opposite_direction_days"] == 2
    assert row["neutral_days"] == 2
    assert row["same_direction_ratio"] == pytest.approx(0.5)


def test_filter_uses_only_ratio_and_valid_rows():
    metrics = pd.DataFrame(
        [
            {"ts_code": "A", "data_quality": "ok", "same_direction_ratio": 0.70, "direction_observations": 20},
            {"ts_code": "B", "data_quality": "ok", "same_direction_ratio": 0.55, "direction_observations": 20},
            {"ts_code": "C", "data_quality": "insufficient_history", "same_direction_ratio": 0.90, "direction_observations": 10},
        ]
    )

    result = screen_stock_industry_direction(metrics, min_same_direction_ratio=0.60)

    assert result["ts_code"].tolist() == ["A"]


def test_short_history_is_not_silently_shortened():
    stock, industry = _frames([1] * 10, [1] * 10)

    row = calculate_stock_industry_direction(
        stock, industry, _dictionary(), level="L2", window=20
    ).iloc[0]

    assert row["data_quality"] == "insufficient_history"
    assert row["observations"] == 10
    assert pd.isna(row["same_direction_ratio"])


def test_membership_inversion_preserves_multiple_industries():
    memberships = build_stock_industry_memberships(_dictionary(), "L3")

    assert set(memberships["industry_code"]) == {"850101.SI", "850102.SI"}


def test_st_is_excluded_by_default():
    stock, industry = _frames([1] * 20, [1] * 20, stock_name="*ST样例")

    row = calculate_stock_industry_direction(
        stock, industry, _dictionary(), level="L2", window=20
    ).iloc[0]

    assert row["data_quality"] == "st_excluded"


def test_missing_industry_index_is_reported():
    stock, industry = _frames([1] * 20, [1] * 20)

    row = calculate_stock_industry_direction(
        stock,
        industry.iloc[0:0],
        _dictionary(),
        level="L2",
        window=20,
    ).iloc[0]

    assert row["data_quality"] == "missing_industry_index"


def test_all_zero_days_have_no_direction_observations():
    stock, industry = _frames([0] * 20, [1] * 20)

    row = calculate_stock_industry_direction(
        stock, industry, _dictionary(), level="L2", window=20
    ).iloc[0]

    assert row["data_quality"] == "no_direction_observations"
    assert row["neutral_days"] == 20


def test_cache_loader_respects_as_of_date(tmp_path):
    for dataset in ("daily", "sw_daily"):
        directory = tmp_path / dataset
        directory.mkdir(parents=True)
        for trade_date in ("20260102", "20260105"):
            pd.DataFrame(
                {"ts_code": ["000001.SZ"], "trade_date": [trade_date], "pct_chg": [1.0]}
            ).to_parquet(directory / f"{trade_date}.parquet", index=False)

    stock, industry, basic = read_cached_direction_inputs(
        as_of_date="20260102", cache_root=tmp_path
    )

    assert set(stock["trade_date"]) == {"20260102"}
    assert set(industry["trade_date"]) == {"20260102"}
    assert basic.empty
