"""个股日K与申万行业缓存所需的最小 Tushare 接口注册表。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Frequency = Literal["daily", "static", "on_demand"]
CacheMode = Literal["trade_date", "static", "on_demand"]


@dataclass(frozen=True)
class TushareApiSpec:
    """单个接口的采集调度与缓存元数据。"""
    name: str
    frequency: Frequency
    cache_mode: CacheMode
    description: str
    required_fields: tuple[str, ...] = ()
    collection_group: str = ""
    refresh_policy: str = ""
    enabled_by_default: bool = True


DAILY_API_SPECS: tuple[TushareApiSpec, ...] = (
    TushareApiSpec(
        "trade_cal",
        "daily",
        "trade_date",
        "Trading calendar confirmation",
        ("exchange", "cal_date", "is_open"),
        "market_daily",
        "daily",
    ),
    TushareApiSpec(
        "daily",
        "daily",
        "trade_date",
        "A-share daily OHLCV snapshot",
        ("ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"),
        "market_daily",
        "daily",
    ),
    TushareApiSpec(
        "sw_daily",
        "daily",
        "trade_date",
        "Shenwan industry daily bars",
        ("ts_code", "trade_date", "open", "high", "low", "close"),
        "market_daily",
        "daily",
    ),
)

STATIC_API_SPECS: tuple[TushareApiSpec, ...] = (
    TushareApiSpec(
        "stock_basic",
        "static",
        "static",
        "Listed A-share stock universe",
        ("ts_code", "symbol", "name", "market", "list_date"),
        "sw_static",
        "monthly",
    ),
    TushareApiSpec(
        "index_classify",
        "static",
        "static",
        "SW2021 industry catalogue",
        ("index_code", "industry_name", "level", "industry_code", "parent_code", "src"),
        "sw_static",
        "monthly",
    ),
    TushareApiSpec(
        "index_member_all",
        "static",
        "static",
        "SW2021 stock membership",
        ("l1_code", "l1_name", "l2_code", "l2_name", "l3_code", "l3_name", "ts_code"),
        "sw_static",
        "monthly",
    ),
)

ON_DEMAND_API_SPECS: tuple[TushareApiSpec, ...] = (
    TushareApiSpec(
        "pro_bar",
        "on_demand",
        "on_demand",
        "Adjusted stock bars",
        ("ts_code", "trade_date", "open", "high", "low", "close", "vol"),
        "stock_on_demand",
        "on_demand",
    ),
)

ALL_API_SPECS = DAILY_API_SPECS + STATIC_API_SPECS + ON_DEMAND_API_SPECS
_SPECS_BY_NAME = {spec.name: spec for spec in ALL_API_SPECS}


def get_spec(api_name: str) -> TushareApiSpec:
    """按名称取得接口声明；未知名称立即报错以防写入错误缓存目录。"""
    try:
        return _SPECS_BY_NAME[api_name]
    except KeyError as exc:
        raise KeyError(f"Unknown Tushare API: {api_name}") from exc


def iter_specs(
    frequency: Frequency | None = None,
    *,
    enabled_only: bool = False,
) -> tuple[TushareApiSpec, ...]:
    """按频率及默认启用状态筛选接口声明。"""
    specs = ALL_API_SPECS
    if frequency is not None:
        specs = tuple(spec for spec in specs if spec.frequency == frequency)
    if enabled_only:
        specs = tuple(spec for spec in specs if spec.enabled_by_default)
    return specs


def iter_group(group: str) -> tuple[TushareApiSpec, ...]:
    """按采集组返回接口声明。"""
    return tuple(spec for spec in ALL_API_SPECS if spec.collection_group == group)


def default_params(api_name: str, trade_date: str | None = None) -> dict[str, str]:
    """返回正式采集参数。"""
    if api_name == "stock_basic":
        return {
            "exchange": "",
            "list_status": "L",
            "fields": "ts_code,symbol,name,area,industry,market,list_date,delist_date",
        }
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


def probe_params(api_name: str, trade_date: str) -> dict[str, str]:
    """返回只用于权限探测的轻量参数，不代表完整采集范围。"""
    if api_name == "index_member_all":
        return {"l1_code": "801010.SI"}
    if api_name == "pro_bar":
        return {"ts_code": "000001.SZ", "adj": "qfq", "freq": "D", "limit": "5"}
    return default_params(api_name, trade_date)
