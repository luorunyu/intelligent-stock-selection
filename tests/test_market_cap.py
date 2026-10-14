import pandas as pd

from stock_selection.data.market_cap import format_market_cap, merge_market_caps


def test_market_caps_are_joined_by_stock_and_trade_date_without_backfill():
    states = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000001.SZ"],
            "trade_date": ["20261008", "20261009"],
            "close": [11.5, 11.6],
        }
    )
    market_caps = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "trade_date": ["20261009"],
            "total_mv": [22_491_459.1938],
            "circ_mv": [22_491_188.915],
        }
    )

    result = merge_market_caps(states, market_caps)

    assert pd.isna(result.loc[0, "total_mv"])
    assert result.loc[1, "total_mv"] == 22_491_459.1938
    assert result.loc[1, "circ_mv"] == 22_491_188.915


def test_formats_tushare_market_cap_units_for_display():
    assert format_market_cap(1_234_567.89) == "123.46亿元"
    assert format_market_cap(9_999.4) == "9,999万元"
    assert format_market_cap(None) == "-"
