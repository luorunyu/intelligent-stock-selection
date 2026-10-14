import pandas as pd
import pytest

from stock_selection.strategies.stock_industry_direction.screen import (
    build_stock_industry_memberships,
    calculate_stock_industry_direction,
    calculate_stock_industry_direction_history,
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
    expected_correlation = pd.Series([1, -1, 1, -1, 0, 1]).corr(
        pd.Series([2, -2, -2, 2, 1, 0])
    )
    assert row["regression_observations"] == 6
    assert row["return_correlation"] == pytest.approx(expected_correlation)
    assert row["industry_r_squared"] == pytest.approx(expected_correlation**2)


def test_regression_metrics_use_returns_and_keep_zero_return_days():
    stock, industry = _frames(
        [-2, -1, 0, 1, 2],
        [-1, -0.5, 0, 0.5, 1],
    )

    row = calculate_stock_industry_direction(
        stock,
        industry,
        _dictionary(),
        level="L2",
        window=5,
    ).iloc[0]

    assert row["direction_observations"] == 4
    assert row["neutral_days"] == 1
    assert row["regression_observations"] == 5
    assert row["return_correlation"] == pytest.approx(1.0)
    assert row["industry_beta"] == pytest.approx(2.0)
    assert row["industry_r_squared"] == pytest.approx(1.0)
    assert row["regression_quality"] == "ok"


def test_history_returns_one_row_per_complete_rolling_window():
    stock, industry = _frames(
        [-2, -1, 0, 1, 2, 3],
        [-1, -0.5, 0, 0.5, 1, 1.5],
    )

    history = calculate_stock_industry_direction_history(
        stock,
        industry,
        _dictionary(),
        level="L2",
        window=4,
    )

    assert history["as_of_date"].tolist() == stock["trade_date"].tolist()[3:]
    assert history["ts_code"].tolist() == ["000001.SZ"] * 3
    assert history["observations"].tolist() == [4, 4, 4]
    assert history["return_correlation"].tolist() == pytest.approx([1.0, 1.0, 1.0])
    assert history["industry_beta"].tolist() == pytest.approx([2.0, 2.0, 2.0])
    assert history["industry_r_squared"].tolist() == pytest.approx([1.0, 1.0, 1.0])


def test_history_start_date_filters_output_without_losing_lookback():
    stock, industry = _frames(
        [-2, -1, 0, 1, 2, 3],
        [-1, -0.5, 0, 0.5, 1, 1.5],
    )
    last_date = stock["trade_date"].iloc[-1]

    history = calculate_stock_industry_direction_history(
        stock,
        industry,
        _dictionary(),
        level="L2",
        window=4,
        start_date=last_date,
        end_date=last_date,
    )

    assert history["as_of_date"].tolist() == [last_date]
    assert history.iloc[0]["industry_beta"] == pytest.approx(2.0)


def test_constant_industry_returns_mark_regression_unavailable():
    stock, industry = _frames([1, 2, 3, 4], [1, 1, 1, 1])

    row = calculate_stock_industry_direction(
        stock,
        industry,
        _dictionary(),
        level="L2",
        window=4,
    ).iloc[0]

    assert row["regression_quality"] == "zero_industry_variance"
    assert pd.isna(row["return_correlation"])
    assert pd.isna(row["industry_beta"])
    assert pd.isna(row["industry_r_squared"])


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


def test_membership_inversion_includes_full_l1_l2_l3_path():
    dictionary = _dictionary()
    dictionary["level1"] = {
        "801000.SI": {
            "name": "一级行业",
            "children": {
                "801010.SI": {
                    "name": "二级行业",
                    "children": {
                        "850101.SI": {
                            "name": "三级行业A",
                            "stock_codes": ["000001.SZ"],
                        },
                        "850102.SI": {
                            "name": "三级行业B",
                            "stock_codes": ["000001.SZ"],
                        },
                    },
                }
            },
        }
    }

    memberships = build_stock_industry_memberships(dictionary, "L2")
    row = memberships.iloc[0]

    assert row["l1_code"] == "801000.SI"
    assert row["l1_name"] == "一级行业"
    assert row["l2_code"] == "801010.SI"
    assert row["l2_name"] == "二级行业"
    assert row["l3_code"] == "850101.SI; 850102.SI"
    assert row["l3_name"] == "三级行业A; 三级行业B"


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
