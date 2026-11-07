"""Observable SW L2 industry trends and the strategy-five entry gate."""

from dataclasses import dataclass
from enum import StrEnum
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from stock_selection.data.tushare_cache import DEFAULT_CACHE_ROOT


INDUSTRY_RULE = "SW_L2_ROLLING_CLOSE_RISE_FIRST_V1"
INDUSTRY_MEMBERSHIP_RULE = "CURRENT_SW_L2_SNAPSHOT"


class IndustryState(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    STABLE_OR_RISING = "STABLE_OR_RISING"


STATE_NAMES = {"UP": "上涨", "DOWN": "下跌", "STABLE_OR_RISING": "稳中向好"}
BLOCK_NAMES = {
    "INDUSTRY_UP": "行业上涨",
    "INDUSTRY_STABLE_OR_RISING": "行业稳中向好",
    "MISSING_INDUSTRY": "缺少二级行业归属",
    "AMBIGUOUS_INDUSTRY": "存在多个二级行业归属",
    "MISSING_INDUSTRY_DATE": "缺少当日行业日K",
    "INSUFFICIENT_HISTORY": "行业窗口不足",
    "INVALID_PRICE": "行业收盘价无效",
}


@dataclass(frozen=True)
class IndustryConfig:
    lookback_days: int = 7
    drawdown_pct: float = 0.06
    rise_pct: float = 0.06

    def __post_init__(self):
        if isinstance(self.lookback_days, bool) or not isinstance(self.lookback_days, int) or self.lookback_days < 2:
            raise ValueError("industry lookback days must be an integer >= 2")
        for name, value in (("drawdown", self.drawdown_pct), ("rise", self.rise_pct)):
            if not np.isfinite(value) or not 0 < value < 1:
                raise ValueError(f"industry {name} threshold must be finite and between 0 and 1")


TREND_COLUMNS = [
    "industry_code", "industry_name", "industry_trade_date", "industry_close",
    "industry_high_close", "industry_low_close", "industry_drawdown_pct",
    "industry_rebound_pct", "industry_observations", "industry_state",
    "industry_state_name", "industry_data_status",
]
INDUSTRY_COLUMNS = [
    *TREND_COLUMNS, "industry_filter_enabled", "industry_lookback_days",
    "industry_drawdown_threshold", "industry_rise_threshold", "industry_rule",
    "industry_membership_rule", "entry_industry_qualified", "entry_blocked_reason",
]


def calculate_industry_states(daily: pd.DataFrame, *, config: IndustryConfig = IndustryConfig()) -> pd.DataFrame:
    """Compute rolling closes through today, with no persistence or backfill."""
    if daily.empty:
        return pd.DataFrame(columns=TREND_COLUMNS)
    data = daily.rename(columns={"ts_code": "industry_code", "trade_date": "industry_trade_date", "close": "industry_close"}).copy()
    required = {"industry_code", "industry_trade_date", "industry_close"}
    if not required.issubset(data):
        raise ValueError(f"industry bars missing columns: {sorted(required - set(data))}")
    data["industry_code"] = data["industry_code"].fillna("").astype(str).str.strip()
    data["industry_trade_date"] = data["industry_trade_date"].astype(str).str.replace("-", "", regex=False)
    if pd.to_datetime(data["industry_trade_date"], format="%Y%m%d", errors="coerce").isna().any():
        raise ValueError("industry bars contain an invalid trade date")
    data = data.sort_values(["industry_code", "industry_trade_date"]).drop_duplicates(["industry_code", "industry_trade_date"], keep="last")
    data["industry_name"] = data.get("industry_name", data.get("name", pd.Series("", index=data.index))).fillna("")
    closes = pd.to_numeric(data["industry_close"], errors="coerce")
    data["industry_close"] = closes.where(np.isfinite(closes) & closes.gt(0))
    groups = data.groupby("industry_code", sort=False)["industry_close"]
    window = config.lookback_days
    data["industry_high_close"] = groups.transform(lambda values: values.rolling(window, min_periods=window).max())
    data["industry_low_close"] = groups.transform(lambda values: values.rolling(window, min_periods=window).min())
    data["industry_observations"] = groups.transform(lambda values: values.rolling(window, min_periods=1).count()).astype(int)
    data["industry_drawdown_pct"] = 1 - data["industry_close"] / data["industry_high_close"]
    data["industry_rebound_pct"] = data["industry_close"] / data["industry_low_close"] - 1
    complete = data["industry_observations"].eq(window)
    up = (data["industry_rebound_pct"] + 1e-12).ge(config.rise_pct)
    down = data["industry_drawdown_pct"].gt(config.drawdown_pct + 1e-12)
    data["industry_state"] = np.select([complete & up, complete & down, complete], [IndustryState.UP.value, IndustryState.DOWN.value, IndustryState.STABLE_OR_RISING.value], default="")
    data["industry_state_name"] = data["industry_state"].map(STATE_NAMES).fillna("")
    data["industry_data_status"] = np.select([data["industry_close"].isna(), ~complete], ["INVALID_PRICE", "INSUFFICIENT_HISTORY"], default="OK")
    return data[TREND_COLUMNS].reset_index(drop=True)


def read_industry_inputs(*, cache_root: str | Path | None = None, dictionary_path: str | Path | None = None, as_of_date: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reuse the local dictionary and industry bars, including warm-up history."""
    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    path = Path(dictionary_path) if dictionary_path is not None else root / "static/sw_industry_stock_dictionary.json"
    dictionary = json.loads(path.read_text(encoding="utf-8"))
    industries = dictionary.get("level2_index")
    if not isinstance(industries, dict) or not industries:
        raise ValueError(f"no SW L2 industries in {path}")
    memberships = pd.DataFrame([
        {"ts_code": str(code).strip(), "industry_code": index, "industry_name": str(entry.get("name") or "")}
        for index, entry in industries.items() for code in entry.get("stock_codes", [])
    ], columns=["ts_code", "industry_code", "industry_name"])
    target = str(as_of_date).replace("-", "")
    files = [p for p in sorted((root / "sw_daily").glob("*.parquet")) if p.stem <= target]
    if not files:
        raise FileNotFoundError(f"no cached sw_daily bars on or before {target} under {root}")
    frames = []
    for file in files:
        frame = pd.read_parquet(file, columns=["ts_code", "trade_date", "close"])
        frame = frame[frame["ts_code"].isin(industries)]
        frames.append(frame)
    bars = pd.concat(frames, ignore_index=True)
    bars = bars[bars["trade_date"].astype(str).le(target)].copy()
    bars["name"] = bars["ts_code"].map({code: entry.get("name", "") for code, entry in industries.items()})
    return bars, memberships


def attach_industry_states(states: pd.DataFrame, industry_states: pd.DataFrame | None = None, memberships: pd.DataFrame | None = None, *, enabled: bool = True, config: IndustryConfig = IndustryConfig()) -> pd.DataFrame:
    """Keep stock signals intact; attach same-day evidence for the entry gate."""
    result = states.drop(columns=INDUSTRY_COLUMNS, errors="ignore").copy()
    if not result.empty and (enabled or industry_states is not None or memberships is not None):
        if industry_states is None or memberships is None:
            raise ValueError("industry states and memberships must both be provided")
        members = memberships.drop_duplicates(["ts_code", "industry_code"]).copy()
        ambiguous = set(members.loc[members.groupby("ts_code")["industry_code"].transform("nunique").gt(1), "ts_code"])
        members = members[~members["ts_code"].isin(ambiguous)].drop_duplicates("ts_code")
        result = result.merge(members[["ts_code", "industry_code", "industry_name"]], on="ts_code", how="left", validate="many_to_one")
        result = result.merge(industry_states.drop(columns="industry_name"), left_on=["industry_code", "trade_date"], right_on=["industry_code", "industry_trade_date"], how="left", validate="many_to_one")
        result["industry_data_status"] = result["industry_data_status"].fillna("MISSING_INDUSTRY_DATE")
        result.loc[result["industry_code"].isna(), "industry_data_status"] = "MISSING_INDUSTRY"
        result.loc[result["ts_code"].isin(ambiguous), "industry_data_status"] = "AMBIGUOUS_INDUSTRY"
    else:
        for column in TREND_COLUMNS:
            result[column] = np.nan if column in {"industry_close", "industry_high_close", "industry_low_close", "industry_drawdown_pct", "industry_rebound_pct"} else ""
        result["industry_observations"] = 0
        result["industry_data_status"] = "DISABLED" if not enabled else "MISSING_INDUSTRY"
    for column in ("industry_code", "industry_name", "industry_trade_date", "industry_state", "industry_state_name"):
        result[column] = result[column].fillna("").astype(str)
    result["industry_observations"] = pd.to_numeric(result["industry_observations"], errors="coerce").fillna(0).astype(int)
    result["industry_filter_enabled"] = bool(enabled)
    result["industry_lookback_days"] = config.lookback_days
    result["industry_drawdown_threshold"] = config.drawdown_pct
    result["industry_rise_threshold"] = config.rise_pct
    result["industry_rule"] = INDUSTRY_RULE
    result["industry_membership_rule"] = INDUSTRY_MEMBERSHIP_RULE
    result["entry_industry_qualified"] = ~result["industry_filter_enabled"] | (result["industry_data_status"].eq("OK") & result["industry_state"].eq(IndustryState.DOWN.value))
    result["entry_blocked_reason"] = ""
    blocked = ~result["entry_industry_qualified"]
    result.loc[blocked, "entry_blocked_reason"] = result.loc[blocked, "industry_data_status"]
    valid_blocked = blocked & result["industry_data_status"].eq("OK")
    result.loc[valid_blocked, "entry_blocked_reason"] = "INDUSTRY_" + result.loc[valid_blocked, "industry_state"]
    result.attrs["industry_filter"] = {
        "enabled": bool(enabled), "lookback_days": config.lookback_days,
        "drawdown_pct": config.drawdown_pct, "rise_pct": config.rise_pct,
        "rule": INDUSTRY_RULE, "membership_rule": INDUSTRY_MEMBERSHIP_RULE,
    }
    return result


def _bool_column(values: pd.Series) -> pd.Series:
    normalised = values.astype(str).str.strip().str.lower()
    if not normalised.isin({"true", "false", "1", "0"}).all():
        raise ValueError("invalid industry filter boolean; regenerate states.csv")
    return normalised.isin({"true", "1"})


def validate_industry_fields(states: pd.DataFrame) -> pd.DataFrame:
    """Reject old/mixed or inconsistent saved gates rather than bypassing them."""
    missing = set(INDUSTRY_COLUMNS) - set(states)
    if missing:
        raise ValueError(f"states.csv 缺少行业过滤字段 {', '.join(sorted(missing))}；请重新运行策略五 CLI")
    data = states.copy()
    if data.empty:
        return data
    data["industry_filter_enabled"] = _bool_column(data["industry_filter_enabled"])
    data["entry_industry_qualified"] = _bool_column(data["entry_industry_qualified"])
    for column in ("industry_code", "industry_name", "industry_trade_date", "industry_state", "industry_state_name", "industry_data_status", "entry_blocked_reason"):
        data[column] = data[column].fillna("").astype(str)
    # A CSV with missing dates may infer the remaining YYYYMMDD values as floats.
    data["industry_trade_date"] = data["industry_trade_date"].str.replace(r"\.0$", "", regex=True)
    parameters = ["industry_lookback_days", "industry_drawdown_threshold", "industry_rise_threshold"]
    for column in parameters:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    if any(data[column].nunique(dropna=False) != 1 for column in ["industry_filter_enabled", *parameters]):
        raise ValueError("states.csv 混用了不同的行业过滤参数，请重新运行策略五 CLI")
    days, drop, rise = (data[column].iloc[0] for column in parameters)
    if not np.isfinite(days) or days % 1:
        raise ValueError("invalid industry lookback days")
    IndustryConfig(int(days), float(drop), float(rise))
    if not data["industry_rule"].eq(INDUSTRY_RULE).all() or not data["industry_membership_rule"].eq(INDUSTRY_MEMBERSHIP_RULE).all():
        raise ValueError("states.csv 行业状态规则已过期，请重新运行策略五 CLI")
    enabled = data["industry_filter_enabled"]
    ok = data["industry_data_status"].eq("OK")
    for column in ("industry_close", "industry_high_close", "industry_low_close", "industry_drawdown_pct", "industry_rebound_pct", "industry_observations"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    closes = data["industry_close"]
    high, low = data["industry_high_close"], data["industry_low_close"]
    expected = np.select([(data["industry_rebound_pct"] + 1e-12).ge(rise), data["industry_drawdown_pct"].gt(drop + 1e-12)], ["UP", "DOWN"], default="STABLE_OR_RISING")
    invalid = enabled & ok & (
        data["industry_trade_date"].ne(data["trade_date"].astype(str))
        | data["industry_code"].eq("") | data["industry_observations"].ne(days)
        | ~np.isfinite(closes) | closes.le(0) | ~np.isfinite(high) | ~np.isfinite(low)
        | high.lt(closes) | low.gt(closes) | low.le(0)
        | ~np.isclose(data["industry_drawdown_pct"], 1 - closes / high, rtol=0, atol=1e-12)
        | ~np.isclose(data["industry_rebound_pct"], closes / low - 1, rtol=0, atol=1e-12)
        | data["industry_state"].ne(expected)
    )
    qualified = ~enabled | (ok & data["industry_state"].eq("DOWN"))
    reason = data["industry_data_status"].where(~ok, "INDUSTRY_" + data["industry_state"]).where(~qualified, "")
    if invalid.any() or data["entry_industry_qualified"].ne(qualified).any() or data["entry_blocked_reason"].ne(reason).any():
        raise ValueError("states.csv 行业状态或开仓过滤结果不一致，请重新运行策略五 CLI")
    return data


def industry_filter_metadata(states: pd.DataFrame) -> dict[str, Any]:
    if states.empty or "industry_filter_enabled" not in states:
        return states.attrs.get("industry_filter", {"enabled": False})
    row = states.iloc[0]
    return {"enabled": bool(row["industry_filter_enabled"]), "lookback_days": int(row["industry_lookback_days"]), "drawdown_pct": float(row["industry_drawdown_threshold"]), "rise_pct": float(row["industry_rise_threshold"]), "rule": INDUSTRY_RULE, "membership_rule": INDUSTRY_MEMBERSHIP_RULE}


def industry_rule_notes(metadata: dict[str, Any]) -> list[str]:
    rule = metadata.get("industry_filter", {})
    if not rule.get("enabled"):
        return ["二级行业开仓过滤已关闭；行业数据可用时仍计算并展示状态，仅供观察，不限制个股开仓。"]
    return [
        f"二级行业过滤：含当天的最近{rule['lookback_days']}个有效行业交易日，仅使用收盘价；相对窗口最低收盘价上涨达到{rule['rise_pct']:.2%}时优先判为上涨；否则相对窗口最高收盘价回撤严格超过{rule['drawdown_pct']:.2%}时判为下跌，其余为稳中向好。每天滚动重算。",
        "只有行业当天处于下跌状态才允许个股新开仓；行业缺失、当日日K缺失或窗口不足时阻止开仓。原始个股信号保留，被行业拦截的信号不延后补开；行业状态不触发平仓。",
        "行业归属采用当前申万二级行业快照，历史回测也使用该归属，不能视为完整的历史行业归属。",
    ]
