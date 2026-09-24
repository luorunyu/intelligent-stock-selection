"""申万行业分类的缓存映射与校验。

本模块只处理 Tushare ``SW2021`` 正式分类，不维护任何市场概念、主题名或
预设股票名单。主题发现需要的每股一级、二级、三级行业均从这里取得。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from stock_selection.data.tushare_cache import DEFAULT_CACHE_ROOT, read_dataset, write_dataset


SW_MEMBERSHIP_DATASET = "sw_industry_membership"
SW_COMPLETE_MEMBERS_DATASET = "index_member_all_complete"
SW_INDUSTRY_STOCK_MAP_FILE = "sw_industry_stock_map.json"
SW_LEVEL_COLUMNS = (
    "sw_l1_code",
    "sw_l1_name",
    "sw_l2_code",
    "sw_l2_name",
    "sw_l3_code",
    "sw_l3_name",
)


@dataclass(frozen=True)
class SwIndustryMembership:
    """一次申万股票映射的表格、来源状态和无法映射的原因。"""

    data: pd.DataFrame
    source: str
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, object]:
        """返回适合写入热点发现 JSON 的小型来源说明。"""
        return {
            "source": self.source,
            "rows": int(len(self.data)),
            "mapped_stocks": int(self.data["ts_code"].nunique()) if "ts_code" in self.data else 0,
            "warnings": list(self.warnings),
            "columns": list(SW_LEVEL_COLUMNS),
        }


def refresh_sw_industry_stock_map(
    *,
    cache_root: str | Path | None = None,
    caller: Callable[[str, dict[str, Any]], pd.DataFrame] | None = None,
) -> dict[str, dict[str, Any]]:
    """Refresh the complete SW2021 industry-to-current-stock dictionary.

    Tushare limits an unfiltered ``index_member_all`` call to 2000 rows. This
    function queries each L1 industry separately, then builds L1/L2/L3 lists
    from the combined result. Only codes present in the current listed-stock
    universe are retained, which removes stale delisted membership records.
    """

    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    classes = read_dataset("index_classify", static=True, cache_root=root)
    listed = read_dataset("stock_basic", static=True, cache_root=root)
    _validate_sw_classes(classes)
    if "ts_code" not in listed.columns:
        raise ValueError("stock_basic is missing ts_code")

    if caller is None:
        from stock_selection.data.tushare_client import call_api

        caller = lambda api_name, params: call_api(api_name, params)

    l1_codes = (
        classes.loc[classes["level"].astype(str).str.upper() == "L1", "index_code"]
        .dropna()
        .astype(str)
        .sort_values()
        .tolist()
    )
    member_frames: list[pd.DataFrame] = []
    for l1_code in l1_codes:
        frame = caller("index_member_all", {"l1_code": l1_code})
        if not isinstance(frame, pd.DataFrame):
            raise TypeError(f"index_member_all returned non-DataFrame for {l1_code}")
        if not frame.empty:
            member_frames.append(frame)
    if not member_frames:
        raise RuntimeError("index_member_all returned no SW2021 membership rows")

    members = pd.concat(member_frames, ignore_index=True)
    required = {"l1_code", "l2_code", "l3_code", "ts_code"}
    missing = sorted(required - set(members.columns))
    if missing:
        raise ValueError(f"index_member_all is missing columns: {missing}")
    members = members.drop_duplicates(["l1_code", "l2_code", "l3_code", "ts_code"], keep="last")
    current = _current_listed_members(members, set(listed["ts_code"].dropna().astype(str)))
    industry_map = build_sw_industry_stock_map(classes, current)

    write_dataset(members, SW_COMPLETE_MEMBERS_DATASET, static=True, cache_root=root)
    write_dataset(_normalise_membership(current), SW_MEMBERSHIP_DATASET, static=True, cache_root=root)
    _write_industry_stock_map(industry_map, root, member_rows=len(members))
    return industry_map


def build_sw_industry_stock_map(
    classes: pd.DataFrame,
    members: pd.DataFrame,
) -> dict[str, dict[str, Any]]:
    """Build a dictionary for every SW L1/L2/L3 code from complete members."""

    _validate_sw_classes(classes)
    level_columns = {"L1": "l1_code", "L2": "l2_code", "L3": "l3_code"}
    industry_code_to_index = {
        str(row["industry_code"]): str(row["index_code"])
        for _, row in classes.iterrows()
        if "industry_code" in classes.columns and not pd.isna(row.get("industry_code"))
    }
    result: dict[str, dict[str, Any]] = {}
    for _, row in classes.sort_values(["level", "index_code"]).iterrows():
        code = str(row["index_code"])
        level = str(row["level"]).upper()
        member_column = level_columns.get(level)
        if member_column is None:
            continue
        if members.empty or member_column not in members.columns:
            stock_codes: list[str] = []
        else:
            stock_codes = sorted(
                members.loc[members[member_column].astype(str) == code, "ts_code"]
                .dropna()
                .astype(str)
                .unique()
                .tolist()
            )
        raw_parent = _clean_text(row.get("parent_code"))
        parent_code = industry_code_to_index.get(raw_parent, raw_parent)
        result[code] = {
            "name": str(row["industry_name"]),
            "level": level,
            "parent_code": parent_code,
            "child_codes": [],
            "stock_count": len(stock_codes),
            "stock_codes": stock_codes,
        }
    for code, item in result.items():
        parent_code = item["parent_code"]
        if parent_code in result:
            result[parent_code]["child_codes"].append(code)
    for item in result.values():
        item["child_codes"].sort()
    return result


def load_sw_industry_stock_map(
    *, cache_root: str | Path | None = None
) -> dict[str, dict[str, Any]]:
    """Load the persisted SW industry dictionary keyed by industry code."""

    path = sw_industry_stock_map_path(cache_root=cache_root)
    if not path.exists():
        raise FileNotFoundError(
            f"SW industry stock map does not exist: {path}. "
            "Run scripts/refresh_sw_industry_stock_map.py first."
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    industries = payload.get("industries")
    if not isinstance(industries, dict):
        raise ValueError(f"invalid SW industry stock map: {path}")
    return industries


def get_sw_industry_stock_codes(
    industry: str,
    *,
    cache_root: str | Path | None = None,
) -> list[str]:
    """Return current listed stock codes for an SW code or unambiguous name."""

    industry_map = load_sw_industry_stock_map(cache_root=cache_root)
    if industry in industry_map:
        return list(industry_map[industry]["stock_codes"])
    matches = [item for item in industry_map.values() if item.get("name") == industry]
    if not matches:
        raise KeyError(f"unknown SW industry code or name: {industry}")
    if len(matches) > 1:
        levels = ", ".join(str(item.get("level")) for item in matches)
        raise ValueError(f"ambiguous SW industry name {industry!r}; matching levels: {levels}")
    return list(matches[0]["stock_codes"])


def sw_industry_stock_map_path(*, cache_root: str | Path | None = None) -> Path:
    """Return the formal JSON path for the SW industry stock dictionary."""

    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    return root / "static" / SW_INDUSTRY_STOCK_MAP_FILE


def load_sw_industry_membership(
    *,
    cache_root: str | Path | None = None,
    rebuild: bool = False,
) -> SwIndustryMembership:
    """读取或由 Tushare 原始静态表重建股票到申万三级的正式映射。

    ``index_member_all`` 是唯一的股票归属来源；``index_classify`` 用于校验
    分类版本是否为 SW2021。缺少任一表时返回空映射和显式 warning，调用方不应
    退回为人工主题或 ``stock_basic.industry``。
    """
    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    warnings: list[str] = []
    derived_path = root / "static" / f"{SW_MEMBERSHIP_DATASET}.parquet"
    if derived_path.exists() and not rebuild:
        data = read_dataset(SW_MEMBERSHIP_DATASET, static=True, cache_root=root)
        return SwIndustryMembership(_normalise_membership(data), "derived_cache", warnings)

    try:
        classes = read_dataset("index_classify", static=True, cache_root=root)
    except Exception as exc:
        warnings.append(f"missing index_classify: {exc}")
        classes = pd.DataFrame()
    try:
        members = read_dataset("index_member_all", static=True, cache_root=root)
    except Exception as exc:
        warnings.append(f"missing index_member_all: {exc}")
        members = pd.DataFrame()

    if classes.empty or members.empty:
        if classes.empty:
            warnings.append("SW2021 classification catalogue is unavailable")
        if members.empty:
            warnings.append("SW2021 stock membership is unavailable")
        return SwIndustryMembership(_empty_membership(), "unavailable", warnings)

    source_values = set(classes.get("src", pd.Series(dtype=str)).dropna().astype(str))
    if source_values and source_values != {"SW2021"}:
        warnings.append("index_classify source is not exclusively SW2021: " + ", ".join(sorted(source_values)))
    data = _normalise_membership(members)
    if data.empty:
        warnings.append("index_member_all has no usable l1/l2/l3 membership columns")
        return SwIndustryMembership(data, "unavailable", warnings)

    # 派生缓存可避免每次盘后都对原始成分表重复清洗；原始表仍保留在 static 目录。
    write_dataset(data, SW_MEMBERSHIP_DATASET, static=True, cache_root=root)
    return SwIndustryMembership(data, "rebuilt_from_tushare_static", warnings)


def attach_sw_industry(data: pd.DataFrame, membership: pd.DataFrame) -> pd.DataFrame:
    """按股票代码左连接正式申万三级，不用基础行业字段替代缺失的申万归属。"""
    if data.empty:
        return data.copy()
    result = data.copy()
    for column in SW_LEVEL_COLUMNS:
        if column in result.columns:
            result = result.drop(columns=[column])
    if membership.empty or "ts_code" not in membership.columns:
        for column in SW_LEVEL_COLUMNS:
            result[column] = ""
        return result
    keep = ["ts_code", *SW_LEVEL_COLUMNS]
    return result.merge(membership[keep].drop_duplicates("ts_code"), on="ts_code", how="left").fillna({column: "" for column in SW_LEVEL_COLUMNS})


def _normalise_membership(data: pd.DataFrame) -> pd.DataFrame:
    """兼容 Tushare 原始字段及已生成映射，统一为项目内部的字段名。"""
    if data.empty or "ts_code" not in data.columns:
        return _empty_membership()
    aliases = {
        "sw_l1_code": ("sw_l1_code", "l1_code"),
        "sw_l1_name": ("sw_l1_name", "l1_name"),
        "sw_l2_code": ("sw_l2_code", "l2_code"),
        "sw_l2_name": ("sw_l2_name", "l2_name"),
        "sw_l3_code": ("sw_l3_code", "l3_code"),
        "sw_l3_name": ("sw_l3_name", "l3_name"),
    }
    result = pd.DataFrame({"ts_code": data["ts_code"].astype(str)})
    for target, choices in aliases.items():
        source = next((column for column in choices if column in data.columns), None)
        result[target] = data[source].fillna("").astype(str) if source else ""
    # 同一股票若有历史成分记录，优先保留三级信息完整且未标注退出的一行。
    if "out_date" in data.columns:
        result["_current"] = data["out_date"].isna() | (data["out_date"].fillna("").astype(str) == "")
    else:
        result["_current"] = True
    result["_depth"] = (result[["sw_l1_code", "sw_l2_code", "sw_l3_code"]] != "").sum(axis=1)
    result = result.sort_values(["ts_code", "_current", "_depth"], ascending=[True, False, False])
    result = result.drop_duplicates("ts_code", keep="first")
    return result[["ts_code", *SW_LEVEL_COLUMNS]].reset_index(drop=True)


def _empty_membership() -> pd.DataFrame:
    """提供带固定列的空映射，令下游能保留信息缺口而不中断。"""
    return pd.DataFrame(columns=["ts_code", *SW_LEVEL_COLUMNS])


def _current_listed_members(members: pd.DataFrame, listed_codes: set[str]) -> pd.DataFrame:
    current = members[members["ts_code"].astype(str).isin(listed_codes)].copy()
    if "is_new" in current.columns:
        marker = current["is_new"].fillna("").astype(str).str.upper()
        current = current[marker.isin({"Y", "1", "TRUE"})]
    if "out_date" in current.columns:
        current = current[current["out_date"].isna() | current["out_date"].fillna("").astype(str).eq("")]
    return current


def _validate_sw_classes(classes: pd.DataFrame) -> None:
    required = {"index_code", "industry_name", "level", "parent_code"}
    missing = sorted(required - set(classes.columns))
    if missing:
        raise ValueError(f"index_classify is missing columns: {missing}")
    sources = set(classes.get("src", pd.Series(dtype=str)).dropna().astype(str))
    if sources and sources != {"SW2021"}:
        raise ValueError(f"index_classify is not exclusively SW2021: {sorted(sources)}")


def _write_industry_stock_map(
    industry_map: dict[str, dict[str, Any]],
    root: Path,
    *,
    member_rows: int,
) -> Path:
    path = sw_industry_stock_map_path(cache_root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "Tushare index_classify SW2021 + index_member_all by l1_code + listed stock_basic",
        "industry_count": len(industry_map),
        "member_rows_before_listed_filter": member_rows,
        "industries": industry_map,
    }
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return path


def _clean_text(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text if text and text != "0" else None
