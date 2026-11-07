"""Observable local closing peaks and a baseline frozen above their region."""

from __future__ import annotations

from collections import deque
import json
from typing import Any, Iterator

import numpy as np
import pandas as pd


DEFAULT_COST_PEAK_WINDOW_DAYS = 16
BREAKOUT_BASELINE_RULE = "CONFIRMED_LOCAL_PEAK_MEAN_FROZEN_UNTIL_RETURN_TO_UPPER_BOUND"
BREAKOUT_BASE_KIND_LABELS = {"COST_PEAK_MEAN": "历史峰值均值"}
COST_ZONE_COLUMNS = [
    "cost_peak_window_days", "cost_peak_points", "cost_peak_count",
    "cost_zone_low", "cost_zone_high", "cost_zone_status", "cost_zone_frozen",
    "cost_zone_freeze_date", "cost_zone_high_date", "cost_zone_high_close",
    "entry_breakout_reason",
]
BREAKOUT_REASON_LABELS = {
    "FILTER_DISABLED": "过滤已关闭", "BASELINE_MISSING": "基准不足",
    "ZONE_NOT_FROZEN": "尚未突破成本区", "RETURNED_TO_ZONE": "回到成本区，解除冻结",
    "GAIN_BELOW_THRESHOLD": "突破涨幅不足", "QUALIFIED": "通过",
}
COST_ZONE_STATUS_LABELS = {
    "INSUFFICIENT_HISTORY": "历史不足", "NO_PEAKS": "无已确认峰值",
    "TRACKING": "滚动维护", "FROZEN": "已冻结", "RESET": "当日解除冻结",
}


def iter_cost_zone_fields(
    closes: np.ndarray, dates: np.ndarray, *, lookback_days: int,
    peak_window_days: int, min_gain_pct: float,
) -> Iterator[dict[str, Any]]:
    """Yield immutable daily snapshots; newly confirmed peaks enter the next day.

    A window of 16 means 8 bars before, the peak, and 7 bars after it. A
    plateau's last bar qualifies, once every following bar in the window is
    strictly lower. Only peak dates in the preceding lookback are candidates.
    Frozen snapshots outlive that lookback and all stock-state cycles.
    """
    left = peak_window_days // 2
    right = peak_window_days - left - 1
    peaks: deque[tuple[int, float, str]] = deque()
    frozen: dict[str, Any] | None = None
    cached_positions: tuple[int, ...] = ()
    cached: dict[str, Any] = {}
    running_high = np.nan
    high_date = ""
    for position, close in enumerate(closes):
        peak = position - right - 1
        if peak >= left:
            price = float(closes[peak])
            if price >= np.max(closes[peak - left:peak]) and price > np.max(closes[peak + 1:position]):
                peaks.append((peak, price, str(dates[position - 1])))
        while peaks and peaks[0][0] < position - lookback_days:
            peaks.popleft()

        if frozen is None:
            positions = tuple(point[0] for point in peaks) if position >= lookback_days else ()
            if positions != cached_positions or not cached:
                cached_positions = positions
                points = [
                    {"trade_date": str(dates[index]), "close": price, "confirm_date": confirmed}
                    for index, price, confirmed in peaks
                ] if positions else []
                prices = [point["close"] for point in points]
                cached = {
                    "cost_peak_points": json.dumps(points, separators=(",", ":")),
                    "cost_peak_count": len(points),
                    "cost_zone_low": min(prices) if prices else np.nan,
                    "cost_zone_high": max(prices) if prices else np.nan,
                    "breakout_base_close": float(np.mean(prices)) if prices else np.nan,
                    "breakout_base_start_date": points[0]["trade_date"] if points else "",
                    "breakout_base_date": points[-1]["trade_date"] if points else "",
                    "breakout_base_kind": "COST_PEAK_MEAN" if points else "",
                    "breakout_base_days": positions[-1] - positions[0] + 1 if positions else 0,
                }
            snapshot = cached
            status = "INSUFFICIENT_HISTORY" if position < lookback_days else ("TRACKING" if positions else "NO_PEAKS")
            if positions and close > snapshot["cost_zone_high"] + 1e-12:
                frozen = {**snapshot, "cost_zone_freeze_date": str(dates[position])}
                running_high, high_date = float(close), str(dates[position])
                status = "FROZEN"
        else:
            status = "FROZEN"

        if frozen is not None:
            snapshot = frozen
            if close > running_high:
                running_high, high_date = float(close), str(dates[position])
            if close <= frozen["cost_zone_high"] + 1e-12:
                status = "RESET"
                frozen = None
        baseline = snapshot["breakout_base_close"]
        gain = running_high / baseline - 1.0 if status in {"FROZEN", "RESET"} else np.nan
        reason = (
            "FILTER_DISABLED" if min_gain_pct == 0 else
            "BASELINE_MISSING" if not np.isfinite(baseline) else
            "RETURNED_TO_ZONE" if status == "RESET" else
            "ZONE_NOT_FROZEN" if status != "FROZEN" else
            "QUALIFIED" if gain + 1e-12 >= min_gain_pct else "GAIN_BELOW_THRESHOLD"
        )
        yield {
            **snapshot,
            "cost_peak_window_days": peak_window_days,
            "cost_zone_status": status,
            "cost_zone_frozen": status == "FROZEN",
            "cost_zone_freeze_date": snapshot.get("cost_zone_freeze_date", ""),
            "cost_zone_high_date": high_date if status in {"FROZEN", "RESET"} else "",
            "cost_zone_high_close": running_high if status in {"FROZEN", "RESET"} else np.nan,
            "breakout_gain_pct": gain,
            "entry_breakout_qualified": reason in {"FILTER_DISABLED", "QUALIFIED"},
            "entry_breakout_reason": reason,
        }


def cost_zone_rule_notes(window: int = DEFAULT_COST_PEAK_WINDOW_DAYS) -> str:
    left = window // 2
    right = window - left - 1
    return (
        f"突刺过滤：在当天之前 lookback-days 个有效交易日内寻找已确认收盘峰值；"
        f"极值窗口共{window}日（前{left}日、峰值当天、后{right}日），后侧交易日走完后于下一日纳入基准，连续同价平台只计最后一个峰值。"
        "峰值价格等权算术平均为成本基准，最低/最高峰值为区域下沿/上沿。收盘严格突破上沿时冻结已有峰值集合；"
        "新高、中途回调、行情周期变化及历史窗口滚动均不更换冻结基准。收盘小于或等于原上沿时当日解除冻结并禁止通过此过滤，下一交易日起滚动重建。"
        "冻结以来最高收盘价相对峰值均值达到最低突破涨幅后，新的候选回调事件才允许开仓，包含等于；不补发之前的回调。"
        "无峰值或历史不足时不通过，只有一个峰值时用该点；阈值为0时关闭过滤。"
    )


def format_cost_peaks(value: Any) -> str:
    try:
        points = json.loads(value) if isinstance(value, str) else value
        return "；".join(
            f"{point['trade_date']} {float(point['close']):.2f}（确认{point['confirm_date']}）"
            for point in points
        ) or "-"
    except (TypeError, ValueError, KeyError):
        return "-"


def validate_cost_zone_fields(states: pd.DataFrame) -> pd.DataFrame:
    """Validate persisted snapshots, including the causal evidence for entries."""
    missing = set(COST_ZONE_COLUMNS).difference(states.columns)
    if missing:
        raise ValueError(f"states.csv 缺少成本区突破涨幅过滤字段：{', '.join(sorted(missing))}，请重新运行策略五 CLI")
    data = states.copy()
    if data.empty:
        return data
    for name in ("cost_peak_window_days", "cost_peak_count", "cost_zone_low", "cost_zone_high", "cost_zone_high_close"):
        data[name] = pd.to_numeric(data[name], errors="coerce")
    for name in ("cost_zone_freeze_date", "cost_zone_high_date"):
        data[name] = data[name].fillna("").astype(str).str.replace(r"\.0$", "", regex=True)
    data["cost_zone_frozen"] = data["cost_zone_frozen"].map({True: True, False: False, "True": True, "False": False})
    invalid = (
        ~np.isfinite(data["cost_peak_window_days"]) | data["cost_peak_window_days"].lt(3)
        | data["cost_peak_window_days"].mod(1).ne(0)
        | ~np.isfinite(data["cost_peak_count"]) | data["cost_peak_count"].lt(0) | data["cost_peak_count"].mod(1).ne(0)
        | data["cost_zone_frozen"].isna()
        | ~data["cost_zone_status"].isin(COST_ZONE_STATUS_LABELS)
        | ~data["entry_breakout_reason"].isin(BREAKOUT_REASON_LABELS)
    )
    if invalid.any() or data["cost_peak_window_days"].nunique() != 1:
        raise ValueError("states.csv 成本区窗口、状态或峰值数量无效，请重新运行策略五 CLI")
    minimum = pd.to_numeric(data["min_breakout_gain_pct"], errors="coerce")
    entries = data[pd.to_numeric(data["entry_signal"], errors="coerce").eq(15) & minimum.gt(0)]
    for row in entries.to_dict("records"):
        try:
            points = json.loads(row["cost_peak_points"])
            prices = [float(point["close"]) for point in points]
            freeze = row["cost_zone_freeze_date"]
            peak_dates = [point["trade_date"] for point in points]
            valid = (
                bool(points) and len(points) == row["cost_peak_count"] and row["cost_zone_frozen"]
                and row["cost_zone_status"] == "FROZEN" and row["entry_breakout_reason"] == "QUALIFIED"
                and all(np.isfinite(price) and price > 0 for price in prices)
                and all(point["trade_date"] <= point["confirm_date"] < freeze for point in points)
                and peak_dates == sorted(set(peak_dates))
                and freeze <= str(row["trade_date"])
                and freeze <= row["cost_zone_high_date"] <= str(row["trade_date"])
                and np.isclose(np.mean(prices), float(row["breakout_base_close"]), rtol=1e-10)
                and np.isclose(min(prices), row["cost_zone_low"], rtol=1e-10)
                and np.isclose(max(prices), row["cost_zone_high"], rtol=1e-10)
                and row["cost_zone_high_close"] > row["cost_zone_high"]
                and float(row["close"]) > row["cost_zone_high"]
                and np.isclose(float(row["breakout_gain_pct"]), row["cost_zone_high_close"] / np.mean(prices) - 1, rtol=1e-10)
                and float(row["breakout_gain_pct"]) + 1e-12 >= float(row["min_breakout_gain_pct"])
            )
        except (ValueError, TypeError, KeyError):
            valid = False
        if not valid:
            raise ValueError("开仓信号未通过成本区突破涨幅过滤或使用了未来峰值，请重新运行策略五 CLI")
    return data
