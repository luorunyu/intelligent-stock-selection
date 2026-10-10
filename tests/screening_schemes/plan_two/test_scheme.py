import pandas as pd
import pytest

from stock_selection.screening_schemes.plan_two.scheme import (
    PlanTwoClassification,
    run_plan_two,
)
from stock_selection.strategies.stock_short_high_state.screen import StockShortState


def _lending(codes: list[str], lending_date: str = "20261009") -> pd.DataFrame:
    rows = []
    for code in codes:
        suffix = "SH" if code.startswith("6") or code.startswith("5") else "SZ"
        rows.append(
            {
                "lending_date": lending_date,
                "market": "上海Ａ" if suffix == "SH" else "深圳Ａ",
                "security_code": code,
                "ts_code": f"{code}.{suffix}",
                "security_name": f"股票{code}",
                "available_quantity": 1000,
                "margin_ratio": 100.0,
                "lending_status": "正常",
            }
        )
    return pd.DataFrame(rows)


def _daily(code: str, closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": code,
            "name": code,
            "trade_date": pd.date_range(
                "2026-01-01", periods=len(closes), freq="B"
            ).strftime("%Y%m%d"),
            "close": closes,
        }
    )


def test_classifies_high_candidate_as_short_scope():
    closes = [10.0] * 5 + [11.0]
    daily = _daily("000001.SZ", closes)
    as_of_date = str(daily["trade_date"].iloc[-1])

    _, states, result = run_plan_two(
        _lending(["000001"], as_of_date),
        daily,
        as_of_date=as_of_date,
        lookback_days=5,
    )

    assert states.iloc[0]["state"] == int(StockShortState.HIGH_CANDIDATE)
    assert result.iloc[0]["classification"] == int(
        PlanTwoClassification.SHORT_SCOPE
    )
    assert result.iloc[0]["classification_description"] == "做空范围"


def test_classifies_normal_state_as_non_short_scope():
    closes = [10.0, 9.0, 9.0, 9.0, 9.0, 9.0]
    daily = _daily("000001.SZ", closes)
    as_of_date = str(daily["trade_date"].iloc[-1])

    _, _, result = run_plan_two(
        _lending(["000001"], as_of_date),
        daily,
        as_of_date=as_of_date,
        lookback_days=5,
    )

    assert result.iloc[0]["classification"] == int(
        PlanTwoClassification.NON_SHORT_SCOPE
    )


def test_marks_non_stock_and_insufficient_history_unclassified():
    stock = _daily("000001.SZ", [10.0] * 5)
    as_of_date = str(stock["trade_date"].iloc[-1])

    _, states, result = run_plan_two(
        _lending(["000001", "510500"], as_of_date),
        stock,
        as_of_date=as_of_date,
        lookback_days=5,
    )

    assert states.empty
    by_code = result.set_index("security_code")
    assert by_code.loc["000001", "instrument_match_status"] == "INSUFFICIENT_HISTORY"
    assert by_code.loc["510500", "instrument_match_status"] == "NON_STOCK"
    assert set(result["classification"]) == {
        int(PlanTwoClassification.UNCLASSIFIED)
    }


def test_rejects_stale_market_data_by_default():
    daily = _daily("000001.SZ", [10.0] * 6)

    with pytest.raises(ValueError, match="latest stock daily data"):
        run_plan_two(
            _lending(["000001"]),
            daily,
            as_of_date="20261009",
            lookback_days=5,
        )


def test_allows_stale_market_data_when_explicit():
    daily = _daily("000001.SZ", [10.0] * 6)

    _, _, result = run_plan_two(
        _lending(["000001"]),
        daily,
        as_of_date="20261009",
        lookback_days=5,
        allow_stale_market_data=True,
    )

    assert result.iloc[0]["market_data_stale"]
    assert result.iloc[0]["market_data_date"] == daily["trade_date"].iloc[-1]


def test_uses_stock_basic_to_exclude_etf_even_if_daily_exists():
    stock = _daily("000001.SZ", [10.0] * 6)
    etf = _daily("510500.SH", [5.0] * 6)
    daily = pd.concat([stock, etf], ignore_index=True)
    stock_basic = pd.DataFrame(
        [{"ts_code": "000001.SZ", "name": "平安银行"}]
    )
    as_of_date = str(stock["trade_date"].iloc[-1])

    _, _, result = run_plan_two(
        _lending(["000001", "510500"], as_of_date),
        daily,
        as_of_date=as_of_date,
        lookback_days=5,
        stock_basic=stock_basic,
    )

    by_code = result.set_index("security_code")
    assert by_code.loc["510500", "instrument_match_status"] == "NON_STOCK"


def test_rejects_lending_data_from_another_date():
    daily = _daily("000001.SZ", [10.0] * 6)
    lending = _lending(["000001"])
    lending["lending_date"] = "20261008"
    as_of_date = str(daily["trade_date"].iloc[-1])

    with pytest.raises(ValueError, match="lending data date"):
        run_plan_two(
            lending,
            daily,
            as_of_date=as_of_date,
            lookback_days=5,
        )
