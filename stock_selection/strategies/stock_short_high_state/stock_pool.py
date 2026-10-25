"""Read strategy-five stock pools from HTSC inventory or lending exports."""

from pathlib import Path

import numpy as np
import pandas as pd

from stock_selection.screening_schemes.plan_two.lending import read_lending_file


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
            return _normalise_htsc_inventory(data), "华泰可融券列表 lendable_qty 大于0"

    data = read_lending_file(source)
    data["eligible"] = (
        data["available_quantity"].gt(0)
        & data["lending_status"].eq("正常")
        & data["ts_code"].ne("")
    )
    return data, "融券状态正常且可用数量大于0"


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
    result["eligible"] = quantities.gt(0) & codes.str.fullmatch(r"\d{6}\.(SH|SZ|BJ)")
    return result
