from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import pandas as pd

from stock_selection.data.trading_calendar import cached_trade_dates
from stock_selection.data.tushare_cache import read_dataset


@dataclass(frozen=True)
class MultiDayMarketContext:
    end_date: str
    trade_dates: list[str]
    market_by_date: dict[str, dict[str, Any]]
    industries: list[dict[str, Any]]
    focus_stocks: list[dict[str, Any]]
    missing_datasets: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_multi_day_market_context(
    *,
    end_date: str | None = None,
    lookback: int = 5,
    cache_root: str | Path | None = None,
    focus_ts_codes: list[str] | None = None,
) -> MultiDayMarketContext:
    dates = _recent_dates(end_date=end_date, lookback=lookback, cache_root=cache_root)
    missing: list[str] = []
    daily_by_date: dict[str, pd.DataFrame] = {}
    stock_by_date: dict[str, pd.DataFrame] = {}
    basic_by_date: dict[str, pd.DataFrame] = {}
    money_by_date: dict[str, pd.DataFrame] = {}
    limit_by_date: dict[str, pd.DataFrame] = {}

    for trade_date in dates:
        daily_by_date[trade_date] = _read_optional("daily", trade_date, cache_root=cache_root, missing=missing)
        stock_by_date[trade_date] = _read_optional("stock_basic", trade_date, cache_root=cache_root, missing=missing)
        basic_by_date[trade_date] = _read_optional("daily_basic", trade_date, cache_root=cache_root, missing=missing)
        money_by_date[trade_date] = _read_optional("moneyflow", trade_date, cache_root=cache_root, missing=missing)
        limit_by_date[trade_date] = _read_optional("limit_list_d", trade_date, cache_root=cache_root, missing=missing)

    market_by_date = {
        trade_date: _market_stats(_merged_daily(daily_by_date[trade_date], stock_by_date[trade_date], limit_by_date[trade_date]))
        for trade_date in dates
    }
    industries = _industry_summaries(dates, daily_by_date, stock_by_date, money_by_date, limit_by_date)
    focus_stocks = _focus_stock_summaries(
        dates,
        daily_by_date,
        stock_by_date,
        basic_by_date,
        money_by_date,
        focus_ts_codes or [],
    )
    return MultiDayMarketContext(
        end_date=dates[-1] if dates else (end_date or ""),
        trade_dates=dates,
        market_by_date=market_by_date,
        industries=industries,
        focus_stocks=focus_stocks,
        missing_datasets=sorted(set(missing)),
    )


def _recent_dates(*, end_date: str | None, lookback: int, cache_root: str | Path | None) -> list[str]:
    all_dates = cached_trade_dates(cache_root=cache_root, api_name="daily")
    if end_date:
        compact = end_date.replace("-", "")
        all_dates = [date for date in all_dates if date <= compact]
    return all_dates[-lookback:]


def _read_optional(api_name: str, trade_date: str, *, cache_root: str | Path | None, missing: list[str]) -> pd.DataFrame:
    try:
        return read_dataset(api_name, trade_date, cache_root=cache_root)
    except Exception:
        missing.append(f"{api_name}/{trade_date}")
        return pd.DataFrame()


def _merged_daily(daily: pd.DataFrame, stock: pd.DataFrame, limit_list: pd.DataFrame) -> pd.DataFrame:
    if daily.empty:
        return pd.DataFrame()
    data = daily.copy()
    if not stock.empty and {"ts_code", "name", "industry"}.issubset(stock.columns):
        data = data.merge(stock[["ts_code", "name", "industry"]], on="ts_code", how="left")
    if not limit_list.empty and {"ts_code", "limit"}.issubset(limit_list.columns):
        data = data.merge(limit_list[["ts_code", "limit"]].drop_duplicates("ts_code"), on="ts_code", how="left")
    if "name" in data.columns:
        names = data["name"].fillna("")
        data = data[~(names.str.contains("ST", regex=False) | names.str.contains("退", regex=False))]
    return data


def _market_stats(data: pd.DataFrame) -> dict[str, Any]:
    if data.empty:
        return {}
    pct = data["pct_chg"].astype(float)
    return {
        "count": int(len(data)),
        "up": int((pct > 0).sum()),
        "down": int((pct < 0).sum()),
        "flat": int((pct == 0).sum()),
        "avg_pct": round(float(pct.mean()), 2),
        "amount_yi": round(float(data["amount"].sum()) / 100000, 2) if "amount" in data.columns else None,
        "limit_up": int((data.get("limit") == "U").sum() + (data.get("limit") == "Z").sum()) if "limit" in data.columns else 0,
        "limit_down": int((data.get("limit") == "D").sum()) if "limit" in data.columns else 0,
    }


def _industry_summaries(
    dates: list[str],
    daily_by_date: dict[str, pd.DataFrame],
    stock_by_date: dict[str, pd.DataFrame],
    money_by_date: dict[str, pd.DataFrame],
    limit_by_date: dict[str, pd.DataFrame],
) -> list[dict[str, Any]]:
    if not dates:
        return []
    daily_stats: dict[str, dict[str, dict[str, Any]]] = {}
    for trade_date in dates:
        data = _merged_daily(daily_by_date[trade_date], stock_by_date[trade_date], limit_by_date[trade_date])
        if data.empty or "industry" not in data.columns:
            continue
        if not money_by_date[trade_date].empty and {"ts_code", "net_mf_amount"}.issubset(money_by_date[trade_date].columns):
            data = data.merge(money_by_date[trade_date][["ts_code", "net_mf_amount"]], on="ts_code", how="left")
        for industry, group in data.dropna(subset=["industry"]).groupby("industry"):
            pct = group["pct_chg"].astype(float)
            daily_stats.setdefault(str(industry), {})[trade_date] = {
                "avg_pct": round(float(pct.mean()), 2),
                "up_ratio": round(float((pct > 0).mean()), 3),
                "amount_yi": round(float(group["amount"].sum()) / 100000, 2),
                "net_mf_wan": round(float(group.get("net_mf_amount", pd.Series(dtype=float)).fillna(0).sum()), 2),
                "limit_up": int((group.get("limit") == "U").sum() + (group.get("limit") == "Z").sum()) if "limit" in group.columns else 0,
                "limit_down": int((group.get("limit") == "D").sum()) if "limit" in group.columns else 0,
                "count": int(len(group)),
            }
    latest = dates[-1]
    summaries: list[dict[str, Any]] = []
    for industry, by_date in daily_stats.items():
        latest_stats = by_date.get(latest)
        if not latest_stats:
            continue
        avg_series = [by_date[date]["avg_pct"] for date in dates if date in by_date]
        amount_series = [by_date[date]["amount_yi"] for date in dates if date in by_date]
        summaries.append(
            {
                "industry": industry,
                "latest_avg_pct": latest_stats["avg_pct"],
                "latest_up_ratio": latest_stats["up_ratio"],
                "latest_amount_yi": latest_stats["amount_yi"],
                "latest_net_mf_wan": latest_stats["net_mf_wan"],
                "limit_up": latest_stats["limit_up"],
                "limit_down": latest_stats["limit_down"],
                "count": latest_stats["count"],
                "avg_pct_path": avg_series,
                "amount_path": amount_series,
                "amount_trend": _amount_trend(amount_series),
                "data_state": _industry_state(latest_stats),
            }
        )
    return sorted(summaries, key=lambda item: item["latest_avg_pct"], reverse=True)


def _focus_stock_summaries(
    dates: list[str],
    daily_by_date: dict[str, pd.DataFrame],
    stock_by_date: dict[str, pd.DataFrame],
    basic_by_date: dict[str, pd.DataFrame],
    money_by_date: dict[str, pd.DataFrame],
    focus_ts_codes: list[str],
) -> list[dict[str, Any]]:
    if not dates or not focus_ts_codes:
        return []
    unique_codes = list(dict.fromkeys(focus_ts_codes))
    rows: list[dict[str, Any]] = []
    for ts_code in unique_codes:
        closes: list[float] = []
        pct_path: list[float] = []
        amount_path: list[float] = []
        latest_row: pd.Series | None = None
        latest_stock: pd.Series | None = None
        latest_basic: pd.Series | None = None
        latest_money: pd.Series | None = None
        for trade_date in dates:
            row = _row_by_code(daily_by_date[trade_date], ts_code)
            if row is None:
                continue
            closes.append(float(row["close"]))
            pct_path.append(round(float(row["pct_chg"]), 2))
            amount_path.append(round(float(row["amount"]) / 100000, 2))
            latest_row = row
            latest_stock = _row_by_code(stock_by_date[trade_date], ts_code)
            latest_basic = _row_by_code(basic_by_date[trade_date], ts_code)
            latest_money = _row_by_code(money_by_date[trade_date], ts_code)
        if latest_row is None:
            continue
        cum_pct = None
        if len(closes) >= 2 and closes[0] != 0:
            cum_pct = round((closes[-1] / closes[0] - 1.0) * 100.0, 2)
        rows.append(
            {
                "ts_code": ts_code,
                "name": str(latest_stock.get("name", "")) if latest_stock is not None else "",
                "industry": str(latest_stock.get("industry", "")) if latest_stock is not None else "",
                "latest_pct": round(float(latest_row["pct_chg"]), 2),
                "lookback_cum_pct": cum_pct,
                "latest_amount_yi": round(float(latest_row["amount"]) / 100000, 2),
                "latest_turnover": _round_or_none(latest_basic.get("turnover_rate")) if latest_basic is not None else None,
                "latest_net_mf_wan": _round_or_none(latest_money.get("net_mf_amount")) if latest_money is not None else None,
                "pct_path": pct_path,
                "amount_path": amount_path,
            }
        )
    return sorted(rows, key=lambda item: abs(item.get("latest_amount_yi") or 0), reverse=True)


def _row_by_code(data: pd.DataFrame, ts_code: str) -> pd.Series | None:
    if data.empty or "ts_code" not in data.columns:
        return None
    rows = data[data["ts_code"].astype(str) == ts_code]
    if rows.empty:
        return None
    return rows.iloc[0]


def _round_or_none(value) -> float | None:
    try:
        if pd.isna(value):
            return None
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _amount_trend(values: list[float]) -> str:
    if len(values) < 2:
        return "待观察"
    if values[-1] >= values[0] * 1.2:
        return "放量"
    if values[-1] <= values[0] * 0.8:
        return "缩量"
    return "平稳"


def _industry_state(stats: dict[str, Any]) -> str:
    avg = stats["avg_pct"]
    up_ratio = stats["up_ratio"]
    amount = stats["amount_yi"]
    if avg >= 2 and up_ratio >= 0.6:
        return "强化"
    if avg >= 0.5 and up_ratio >= 0.5:
        return "延续/修复"
    if avg <= -2 and up_ratio <= 0.35 and amount >= 100:
        return "退潮"
    if avg <= -0.5 and up_ratio <= 0.45:
        return "削弱"
    return "分化/震荡"
