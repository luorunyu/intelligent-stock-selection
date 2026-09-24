import pandas as pd

from stock_selection.screening_schemes.plan_one.scheme import run_plan_one


def _inputs():
    dates = pd.date_range("2026-01-01", periods=6, freq="B").strftime("%Y%m%d")
    closes = {
        "L2A": [100, 90, 92, 94, 96, 100],
        "L2B": [100, 90, 91, 92, 93, 96],
        "L2C": [100, 99, 98, 97, 96, 95],
    }
    industry_rows = []
    for code, values in closes.items():
        returns = pd.Series(values).pct_change().fillna(0) * 100
        for trade_date, close, pct_change in zip(dates, values, returns):
            industry_rows.append(
                {
                    "ts_code": code,
                    "name": f"行业{code[-1]}",
                    # 真实 sw_daily 缓存通常没有层级，方案应通过行业字典识别 L2。
                    "level": "",
                    "trade_date": trade_date,
                    "close": close,
                    "pct_change": pct_change,
                }
            )
    stock_returns = {
        "000001.SZ": [0, -1, 1, 1, 1, 1],
        "000002.SZ": [0, 1, -1, 1, -1, 1],
        "000003.SZ": [0, -1, 1, -1, -1, 1],
        "000004.SZ": [0, -1, -1, -1, -1, -1],
    }
    stock_rows = []
    for code, returns in stock_returns.items():
        for trade_date, pct_chg in zip(dates, returns):
            stock_rows.append(
                {
                    "ts_code": code,
                    "name": f"股票{code[5]}",
                    "trade_date": trade_date,
                    "pct_chg": pct_chg,
                }
            )
    dictionary = {
        "level2_index": {
            "L2A": {"name": "行业A", "stock_codes": ["000001.SZ", "000002.SZ"]},
            "L2B": {"name": "行业B", "stock_codes": ["000003.SZ"]},
            "L2C": {"name": "行业C", "stock_codes": ["000004.SZ"]},
        }
    }
    return pd.DataFrame(industry_rows), pd.DataFrame(stock_rows), dictionary


def test_plan_one_filters_industries_then_stocks_and_preserves_order():
    industries, stocks, selected = _inputs()

    qualified, stock_metrics, result = run_plan_one(
        industries,
        stocks,
        selected,
        as_of_date="20260108",
        industry_window=6,
        min_rise_from_low=0.05,
        min_days_since_low=4,
        min_up_days=4,
        stock_window=6,
        min_same_direction_ratio=0.60,
    )

    assert qualified["index_code"].tolist() == ["L2A", "L2B"]
    assert qualified["industry_rank"].tolist() == [1, 2]
    assert set(stock_metrics["industry_code"]) == {"L2A", "L2B"}
    assert result[["industry_code", "ts_code"]].to_records(index=False).tolist() == [
        ("L2A", "000001.SZ"),
        ("L2B", "000003.SZ"),
    ]
    assert result["same_direction_ratio"].tolist() == [1.0, 0.6]


def test_plan_one_returns_empty_final_table_when_no_industry_qualifies():
    industries, stocks, selected = _inputs()

    qualified, stock_metrics, result = run_plan_one(
        industries,
        stocks,
        selected,
        as_of_date="20260108",
        industry_window=6,
        min_rise_from_low=0.50,
        min_days_since_low=4,
        min_up_days=4,
        stock_window=6,
        min_same_direction_ratio=0.60,
    )

    assert qualified.empty
    assert stock_metrics.empty
    assert result.empty
    assert "industry_rank" in result.columns
