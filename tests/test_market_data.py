from __future__ import annotations

import pandas as pd

from stock_selection.data.industry_taxonomy import load_sw_catalog, load_sw_industry_membership
from stock_selection.data.market_data import load_stock_daily, load_sw_daily
from stock_selection.data.trading_calendar import cached_common_trade_dates, latest_complete_trade_date


def _write_csv(cache_root, api_name: str, data: pd.DataFrame, trade_date: str | None = None) -> None:
    if trade_date is None:
        path = cache_root / "static" / f"{api_name}.csv"
    else:
        path = cache_root / api_name / f"{trade_date}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    data.to_csv(path, index=False)


def _write_market_cache(cache_root) -> None:
    _write_csv(
        cache_root,
        "index_classify",
        pd.DataFrame(
            {
                "index_code": ["801010.SI", "801011.SI", "850111.SI"],
                "industry_name": ["农林牧渔", "林业Ⅱ", "种子"],
                "level": ["L1", "L2", "L3"],
                "industry_code": ["110000", "110300", "850111"],
                "parent_code": ["0", "110000", "110300"],
                "src": ["SW2021"] * 3,
            }
        ),
    )
    _write_csv(
        cache_root,
        "sw_industry_membership",
        pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "sw_l1_code": ["801010.SI"],
                "sw_l1_name": ["农林牧渔"],
                "sw_l2_code": ["801011.SI"],
                "sw_l2_name": ["林业Ⅱ"],
                "sw_l3_code": ["850111.SI"],
                "sw_l3_name": ["种子"],
            }
        ),
    )
    for trade_date, stock_close, industry_close in [
        ("20260105", 10.0, 1000.0),
        ("20260106", 10.5, 1010.0),
    ]:
        _write_csv(
            cache_root,
            "daily",
            pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ"],
                    "trade_date": [trade_date, trade_date],
                    "open": [stock_close - 0.2, 20.0],
                    "high": [stock_close + 0.2, 20.5],
                    "low": [stock_close - 0.3, 19.5],
                    "close": [stock_close, 20.2],
                    "vol": [1000.0, 2000.0],
                    "amount": [10000.0, 20000.0],
                }
            ),
            trade_date,
        )
        _write_csv(
            cache_root,
            "sw_daily",
            pd.DataFrame(
                {
                    "ts_code": ["850111.SI", "801010.SI"],
                    "trade_date": [trade_date, trade_date],
                    "open": [industry_close - 5.0, industry_close + 95.0],
                    "high": [industry_close + 10.0, industry_close + 110.0],
                    "low": [industry_close - 10.0, industry_close + 90.0],
                    "close": [industry_close, industry_close + 100.0],
                }
            ),
            trade_date,
        )


def test_load_stock_daily_returns_one_stock_history(tmp_path) -> None:
    _write_market_cache(tmp_path)

    result = load_stock_daily("000001.SZ", cache_root=tmp_path)

    assert result["trade_date"].tolist() == ["20260105", "20260106"]
    assert result["close"].tolist() == [10.0, 10.5]


def test_load_sw_catalog_members_and_level_bars(tmp_path) -> None:
    _write_market_cache(tmp_path)

    catalog = load_sw_catalog(level="三级", cache_root=tmp_path)
    members = load_sw_industry_membership(cache_root=tmp_path).data
    bars = load_sw_daily(level="L3", cache_root=tmp_path)

    assert catalog["industry_name"].tolist() == ["种子"]
    assert members.loc[0, "sw_l3_name"] == "种子"
    assert bars["ts_code"].tolist() == ["850111.SI", "850111.SI"]
    assert bars["industry_name"].tolist() == ["种子", "种子"]


def test_common_trade_dates_require_daily_and_sw_daily(tmp_path) -> None:
    _write_market_cache(tmp_path)
    extra = tmp_path / "daily" / "20260107.csv"
    pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "trade_date": ["20260107"],
            "open": [10.5],
            "high": [10.8],
            "low": [10.4],
            "close": [10.7],
            "vol": [1000.0],
            "amount": [10000.0],
        }
    ).to_csv(extra, index=False)

    assert cached_common_trade_dates(cache_root=tmp_path) == ["20260105", "20260106"]
    assert latest_complete_trade_date(cache_root=tmp_path) == "20260106"
