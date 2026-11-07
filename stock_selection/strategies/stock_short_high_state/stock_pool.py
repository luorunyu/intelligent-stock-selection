"""Read strategy-five stock pools from HTSC inventory or lending exports."""

from collections import Counter
from datetime import datetime
from pathlib import Path
import re
from typing import Any, Iterable

import numpy as np
import pandas as pd

from stock_selection.strategies.stock_short_high_state.industry_state import INDUSTRY_COLUMNS, industry_filter_metadata
from stock_selection.strategies.stock_short_high_state.cost_zone import COST_ZONE_COLUMNS, DEFAULT_COST_PEAK_WINDOW_DAYS

from stock_selection.screening_schemes.plan_two.lending import (
    extract_lending_date,
    read_lending_file,
)
from stock_selection.strategies.stock_short_high_state.screen import (
    DEFAULT_CANDIDATE_PULLBACK_PCT,
    DEFAULT_CONFIRM_DRAWDOWN_PCT,
    StockShortState,
    latest_stock_short_states,
)


SHORTABLE_STAGES = {
    "HIGH_NO_PULLBACK": "新高、尚未回撤",
    "PULLBACK": "开始回撤",
    "CONFIRMED_PULLBACK": "确认回撤",
    "BOTTOM_REBOUND": "底部反弹",
}
SHORTABLE_STOCK_COLUMNS = [
    "ts_code", "stock_name", "stage", "stage_label", "stage_detail",
    "observed_state", "observed_state_name", "trade_date", "close",
    "reference_high_date", "reference_high_close", "drawdown_from_high_pct",
    "trough_ma_days", "trough_ma_close", "peak_number", "lendable_qty",
    "actual_sellable_qty", "verification_status", "queried_at", "verification_error",
    "lending_date", "breakout_base_date",
    "breakout_base_close", "breakout_gain_pct", "min_breakout_gain_pct",
    "breakout_base_kind", "breakout_base_start_date", "breakout_base_days",
    "entry_breakout_qualified", "entry_signal", "has_entry_signal",
    *INDUSTRY_COLUMNS, *COST_ZONE_COLUMNS,
]


def resolve_stock_pool_file(
    *, as_of_date: str, lending_file: str | Path | None = None,
    lending_root: str | Path,
) -> Path:
    """Use the latest dated snapshot, including the query script's checkpoints."""
    if lending_file is not None:
        path = Path(lending_file)
        if not path.is_file():
            raise FileNotFoundError(f"lending file does not exist: {path}")
        return path
    target = datetime.strptime(str(as_of_date).replace("-", ""), "%Y%m%d").strftime("%Y%m%d")
    root = Path(lending_root)
    candidates = []
    for path in root.glob("*"):
        match = re.fullmatch(r"(\d{8})\.csv|\.(\d{8})\.partial\.csv", path.name)
        legacy_date = extract_lending_date(path) if path.suffix.lower() in {".xls", ".tsv", ".txt"} else ""
        if (match or legacy_date) and path.is_file():
            snapshot_date = (match.group(1) or match.group(2)) if match else legacy_date
            try:
                datetime.strptime(snapshot_date, "%Y%m%d")
            except ValueError:
                continue
            if snapshot_date <= target:
                candidates.append((snapshot_date, not path.name.endswith(".partial.csv"), path))
    if candidates:
        return max(candidates, key=lambda candidate: (candidate[0], candidate[1], candidate[2].name))[2]
    latest = root / "latest.csv"
    if latest.is_file():
        dates = pd.read_csv(latest, usecols=["trade_date"], dtype=str)["trade_date"].unique()
        if len(dates) == 1 and dates[0] <= target:
            return latest
    raise FileNotFoundError(f"no securities-lending file found on or before {target} under {root}")


def read_lending_stock_pool(path: str | Path) -> tuple[pd.DataFrame, str]:
    """Return dated inventory rows, eligibility, and the rule used to select them."""
    source = Path(path)
    if source.suffix.lower() == ".csv":
        try:
            data = pd.read_csv(source, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        except UnicodeDecodeError:
            # Legacy exports may use GB18030, tabs and a .csv extension.
            data = None
        if data is not None and "lendable_qty" in data.columns:
            if "actual_sellable_qty" in data.columns:
                return _normalise_htsc_availability(data), "华泰可融券名单中的全部有效代码，不按可融数量、实际可融数量或核验状态筛选"
            return _normalise_htsc_inventory(data), "华泰可融券名单中的全部有效代码，不按可融券数量筛选"

    data = read_lending_file(source)
    data["eligible"] = data["ts_code"].str.fullmatch(r"\d{6}\.(SH|SZ|BJ)")
    return data, "券商可融券名单中的全部有效代码，不按可用数量或融券状态筛选"


def _normalise_htsc_inventory(data: pd.DataFrame) -> pd.DataFrame:
    required = {"trade_date", "ts_code", "lendable_qty"}
    missing = sorted(required.difference(data.columns))
    if missing:
        raise ValueError(f"HTSC inventory is missing columns: {', '.join(missing)}")
    dates = pd.to_datetime(data["trade_date"].str.strip(), format="%Y%m%d", errors="coerce")
    quantities = pd.to_numeric(
        data["lendable_qty"].str.strip().str.replace(",", "", regex=False), errors="coerce",
    )
    if dates.isna().any():
        raise ValueError("HTSC inventory contains an invalid trade_date")
    if (~np.isfinite(quantities) | quantities.lt(0) | quantities.mod(1).ne(0)).any():
        raise ValueError("HTSC inventory lendable_qty must contain non-negative integers")
    codes = data["ts_code"].str.strip().str.upper()
    result = data.rename(columns={
        "trade_date": "lending_date", "lendable_qty": "available_quantity",
    }).copy()
    result["lending_date"] = dates.dt.strftime("%Y%m%d")
    result["ts_code"] = codes
    result["available_quantity"] = quantities
    result["eligible"] = codes.str.fullmatch(r"\d{6}\.(SH|SZ|BJ)")
    return result


def _normalise_htsc_availability(data: pd.DataFrame) -> pd.DataFrame:
    required = {"actual_sellable_qty", "verification_status", "queried_at"}
    missing = sorted(required.difference(data.columns))
    if missing:
        raise ValueError(f"HTSC availability is missing columns: {', '.join(missing)}")
    result = _normalise_htsc_inventory(data)
    if not result.empty and result["lending_date"].nunique() != 1:
        raise ValueError("lending data must contain exactly one snapshot date")
    status = result["verification_status"].str.strip()
    if not status.isin({"available", "zero", "error"}).all():
        raise ValueError("HTSC availability contains an invalid verification_status")
    quantities = pd.to_numeric(
        result["actual_sellable_qty"].str.strip().str.replace(",", "", regex=False), errors="coerce",
    )
    success = status.isin({"available", "zero"})
    invalid = ~np.isfinite(quantities) | quantities.lt(0) | quantities.mod(1).ne(0)
    if (success & (invalid | (status.eq("available") & quantities.le(0)) | (status.eq("zero") & quantities.ne(0)))).any():
        raise ValueError("HTSC availability actual_sellable_qty must match the successful verification_status")
    result["verification_status"] = status
    result["actual_sellable_qty"] = quantities.where(success).astype("Int64")
    queried = pd.to_datetime(result["queried_at"], errors="coerce", utc=True)
    if queried.isna().any():
        raise ValueError("HTSC availability contains an invalid queried_at")
    # A later failed/zero query overrides an earlier positive result too.
    return result.assign(_query_time=queried).sort_values("_query_time", kind="stable").drop_duplicates(
        "ts_code", keep="last",
    ).drop(columns="_query_time").reset_index(drop=True)


def build_shortable_stock_list(
    states: pd.DataFrame,
    lending_data: pd.DataFrame,
    *,
    as_of_date: str,
    source_file: str | Path,
    selection_rule: str,
    candidate_pullback_pct: float = DEFAULT_CANDIDATE_PULLBACK_PCT,
    confirm_drawdown_pct: float = DEFAULT_CONFIRM_DRAWDOWN_PCT,
    stock_codes: Iterable[str] | None = None,
    stock_basic: pd.DataFrame | None = None,
    include_st: bool = False,
) -> dict[str, Any]:
    """Join the chosen inventory to latest observable states within the report date."""
    target = str(as_of_date).replace("-", "")
    datetime.strptime(target, "%Y%m%d")
    dates = set(lending_data["lending_date"].dropna().astype(str))
    if len(dates) != 1:
        raise ValueError("lending data must contain exactly one snapshot date")
    pool_date = next(iter(dates))
    if pool_date > target:
        raise ValueError("lending snapshot must be on or before the report date")
    # A duplicated snapshot row must not multiply a stock or inflate its quantity.
    inventory = lending_data.drop_duplicates("ts_code", keep="last")
    inventory = inventory[inventory["eligible"]]
    if stock_codes is not None:
        inventory = inventory[inventory["ts_code"].isin(set(stock_codes))]

    latest = latest_stock_short_states(
        states[states["trade_date"].astype(str).le(target)] if not states.empty else states,
    ).set_index("ts_code")
    basic_names = {}
    if stock_basic is not None and {"ts_code", "name"}.issubset(stock_basic.columns):
        basic_names = stock_basic.drop_duplicates("ts_code").set_index("ts_code")["name"].to_dict()

    stocks = []
    excluded: Counter[str] = Counter()
    for source in inventory.to_dict("records"):
        code = str(source["ts_code"])
        row = latest.loc[code].to_dict() if code in latest.index else None
        names = [(row or {}).get("stock_name"), basic_names.get(code), source.get("security_name")]
        name = next((str(value).strip() for value in names if pd.notna(value) and str(value).strip()), "")
        if not include_st and "ST" in name.upper():
            excluded["ST股票"] += 1
            continue
        if row is None:
            excluded["无可用行情状态"] += 1
            continue
        state = StockShortState(int(row["observed_state"]))
        if state == StockShortState.NORMAL:
            excluded["尚未进入高位周期"] += 1
            continue
        if state == StockShortState.INVALID_HIGH_NEW_HIGH:
            excluded["高点判断失效"] += 1
            continue
        high = float(row["reference_high_close"])
        close = float(row["close"])
        if not np.isfinite([high, close]).all() or min(high, close) <= 0:
            excluded["缺少有效价格"] += 1
            continue
        drawdown = max(0.0, 1.0 - close / high)
        stage, detail = _shortable_stage(state, drawdown, int(row["peak_number"]), candidate_pullback_pct)
        if not stage:
            excluded["非当前支持状态"] += 1
            continue
        ma = float(row["trough_ma_close"])
        stocks.append({
            "ts_code": code,
            "stock_name": name,
            "stage": stage,
            "stage_label": SHORTABLE_STAGES[stage],
            "stage_detail": detail,
            "observed_state": int(state),
            "observed_state_name": state.name,
            "trade_date": str(row["trade_date"]),
            "close": close,
            "reference_high_date": str(row["reference_high_date"]),
            "reference_high_close": high,
            "drawdown_from_high_pct": drawdown,
            "trough_ma_days": int(row["trough_ma_days"]),
            "trough_ma_close": ma if np.isfinite(ma) else None,
            "peak_number": int(row["peak_number"]),
            "lendable_qty": int(source["available_quantity"]),
            "actual_sellable_qty": int(source["actual_sellable_qty"]) if pd.notna(source.get("actual_sellable_qty")) else None,
            "verification_status": str(source.get("verification_status", "unverified")),
            "queried_at": str(source.get("queried_at", "")),
            "verification_error": str(source.get("verification_error", "")),
            "lending_date": str(source["lending_date"]),
            "breakout_base_date": str(row.get("breakout_base_date", "")),
            "breakout_base_close": row.get("breakout_base_close"),
            "breakout_base_kind": str(row.get("breakout_base_kind", "")),
            "breakout_base_start_date": str(row.get("breakout_base_start_date", "")),
            "breakout_base_days": row.get("breakout_base_days", 0),
            "breakout_gain_pct": row.get("breakout_gain_pct"),
            "min_breakout_gain_pct": row.get("min_breakout_gain_pct"),
            "entry_breakout_qualified": bool(row.get("entry_breakout_qualified", False)),
            "entry_signal": int(row["entry_signal"]),
            **{column: row.get(column) for column in [*INDUSTRY_COLUMNS, *COST_ZONE_COLUMNS]},
            # Peak events remain observable, but are outside the entry range.
            "has_entry_signal": int(row["entry_signal"]) == int(StockShortState.HIGH_CANDIDATE_PULLBACK),
        })
    order = {stage: position for position, stage in enumerate(SHORTABLE_STAGES)}
    stocks.sort(key=lambda row: (
        order[row["stage"]],
        -(row["actual_sellable_qty"] if row["actual_sellable_qty"] is not None else row["lendable_qty"]),
        row["ts_code"],
    ))
    return {
        "metadata": {
            "report_type": "stock_shortable_current_states",
            "as_of_date": target,
            "pool_date": pool_date,
            "source_file": str(source_file),
            "selection_rule": selection_rule,
            "state_source": "observed_state",
            "cost_peak_window_days": int(states["cost_peak_window_days"].iloc[0]) if not states.empty else DEFAULT_COST_PEAK_WINDOW_DAYS,
            "industry_filter": industry_filter_metadata(states),
            "actual_quantity_verified": "actual_sellable_qty" in lending_data,
            "partial_snapshot": Path(source_file).name.endswith(".partial.csv"),
            "candidate_pullback_pct": float(candidate_pullback_pct),
            "confirm_drawdown_pct": float(confirm_drawdown_pct),
        },
        "summary": {
            "eligible_stocks_in_scope": len(inventory),
            "zero_actual_quantity": int(lending_data.get("verification_status", pd.Series(dtype=str)).eq("zero").sum()),
            "unverified_quantity": int(lending_data.get("verification_status", pd.Series(dtype=str)).eq("error").sum()),
            "stocks": len(stocks),
            "by_stage": {stage: sum(row["stage"] == stage for row in stocks) for stage in SHORTABLE_STAGES},
            "excluded": dict(excluded),
        },
        "stocks": stocks,
    }


def _shortable_stage(
    state: StockShortState, drawdown: float, peak_number: int, candidate_pullback_pct: float,
) -> tuple[str, str]:
    if state in {StockShortState.HIGH_CANDIDATE, StockShortState.HIGH_CANDIDATE_PULLBACK}:
        if drawdown <= 1e-12:
            return "HIGH_NO_PULLBACK", "收盘仍在本轮高点"
        threshold = f"{candidate_pullback_pct:.1%}"
        detail = f"已达到{threshold}开仓回落阈值" if drawdown + 1e-12 >= candidate_pullback_pct else f"回落不足{threshold}"
        return "PULLBACK", detail
    if state == StockShortState.TROUGH_CANDIDATE:
        return "CONFIRMED_PULLBACK", "高点已确认，寻找波谷"
    if state == StockShortState.TROUGH_REGION:
        return "BOTTOM_REBOUND", "波谷区域（均线已确认）"
    if state in {StockShortState.PEAK_1, StockShortState.PEAK_2, StockShortState.PEAK_3, StockShortState.PEAK_N}:
        return "BOTTOM_REBOUND", f"第{peak_number}波反弹"
    return "", ""
