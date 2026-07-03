from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Frequency = Literal["daily", "static", "on_demand"]
CacheMode = Literal["trade_date", "static", "on_demand"]


@dataclass(frozen=True)
class TushareApiSpec:
    name: str
    frequency: Frequency
    cache_mode: CacheMode
    description: str
    enabled_by_default: bool = True


DAILY_API_SPECS: tuple[TushareApiSpec, ...] = (
    TushareApiSpec("trade_cal", "daily", "trade_date", "Trading calendar confirmation"),
    TushareApiSpec("daily", "daily", "trade_date", "A-share daily OHLCV snapshot"),
    TushareApiSpec("daily_basic", "daily", "trade_date", "Valuation and turnover snapshot"),
    TushareApiSpec("stk_limit", "daily", "trade_date", "Limit-up and limit-down prices"),
    TushareApiSpec("moneyflow", "daily", "trade_date", "A-share money-flow snapshot"),
    TushareApiSpec("sw_daily", "daily", "trade_date", "Shenwan industry daily bars"),
    TushareApiSpec("top_list", "daily", "trade_date", "Dragon tiger list summary"),
    TushareApiSpec("top_inst", "daily", "trade_date", "Institution seats from dragon tiger list"),
    TushareApiSpec("margin", "daily", "trade_date", "Market margin trading summary"),
    TushareApiSpec("margin_detail", "daily", "trade_date", "Stock-level margin detail"),
    TushareApiSpec("index_daily", "daily", "trade_date", "Index daily bars"),
    TushareApiSpec("index_dailybasic", "daily", "trade_date", "Index valuation and turnover"),
)

STATIC_API_SPECS: tuple[TushareApiSpec, ...] = (
    TushareApiSpec("stock_basic", "static", "static", "Listed A-share stock universe"),
    TushareApiSpec("index_basic", "static", "static", "Index universe"),
    TushareApiSpec("index_classify", "static", "static", "Industry classification"),
    TushareApiSpec("index_member_all", "static", "static", "Industry/index constituents"),
)

ON_DEMAND_API_SPECS: tuple[TushareApiSpec, ...] = (
    TushareApiSpec("pro_bar", "on_demand", "on_demand", "Adjusted stock bars", False),
    TushareApiSpec("income", "on_demand", "on_demand", "Income statement", False),
    TushareApiSpec("balancesheet", "on_demand", "on_demand", "Balance sheet", False),
    TushareApiSpec("cashflow", "on_demand", "on_demand", "Cash-flow statement", False),
    TushareApiSpec("fina_indicator", "on_demand", "on_demand", "Financial indicators", False),
    TushareApiSpec("forecast", "on_demand", "on_demand", "Earnings forecast", False),
    TushareApiSpec("express", "on_demand", "on_demand", "Earnings express", False),
    TushareApiSpec("dividend", "on_demand", "on_demand", "Dividend records", False),
    TushareApiSpec("stk_holdernumber", "on_demand", "on_demand", "Shareholder count", False),
    TushareApiSpec("stk_holdertrade", "on_demand", "on_demand", "Shareholder increase/decrease", False),
    TushareApiSpec("pledge_stat", "on_demand", "on_demand", "Share pledge statistics", False),
    TushareApiSpec("share_float", "on_demand", "on_demand", "Lock-up share release", False),
)

ALL_API_SPECS = DAILY_API_SPECS + STATIC_API_SPECS + ON_DEMAND_API_SPECS
_SPECS_BY_NAME = {spec.name: spec for spec in ALL_API_SPECS}


def get_spec(api_name: str) -> TushareApiSpec:
    try:
        return _SPECS_BY_NAME[api_name]
    except KeyError as exc:
        raise KeyError(f"Unknown Tushare API: {api_name}") from exc


def iter_specs(
    frequency: Frequency | None = None,
    *,
    enabled_only: bool = False,
) -> tuple[TushareApiSpec, ...]:
    specs = ALL_API_SPECS
    if frequency is not None:
        specs = tuple(spec for spec in specs if spec.frequency == frequency)
    if enabled_only:
        specs = tuple(spec for spec in specs if spec.enabled_by_default)
    return specs


def default_params(api_name: str, trade_date: str | None = None) -> dict[str, str]:
    if api_name == "stock_basic":
        return {
            "exchange": "",
            "list_status": "L",
            "fields": "ts_code,symbol,name,area,industry,market,list_date",
        }
    if api_name == "index_basic":
        return {"market": "SSE"}
    if api_name == "index_classify":
        return {"src": "SW2021"}
    if api_name == "index_member_all":
        return {}
    if api_name == "pro_bar":
        return {"ts_code": "000001.SZ", "adj": "qfq", "freq": "D", "limit": "60"}
    if api_name == "trade_cal":
        if trade_date is None:
            raise ValueError("trade_cal requires trade_date")
        return {"exchange": "SSE", "start_date": trade_date, "end_date": trade_date}

    if trade_date is None:
        raise ValueError(f"{api_name} requires trade_date")
    return {"trade_date": trade_date}
