"""申万 SW2021 行业成分字典的采集、构建与保存。"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from stock_selection.data.industry_taxonomy import load_sw_industry_membership
from stock_selection.data.tushare_cache import DEFAULT_CACHE_ROOT, write_dataset
from stock_selection.data.tushare_client import call_api


SW_DICTIONARY_FILENAME = "sw_industry_stock_dictionary.json"
LEVEL_KEYS = ("L1", "L2", "L3")


def collect_sw_industry_data(
    *,
    caller: Callable[[str, dict[str, Any]], pd.DataFrame] = call_api,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """按申万三级行业逐一采集，避免全量成分接口的单次行数上限。"""
    classes = caller("index_classify", {"src": "SW2021"})
    catalogue = normalise_sw_catalogue(classes)
    level3_codes = sorted(catalogue.loc[catalogue["level"] == "L3", "code"].unique())
    if not level3_codes:
        raise RuntimeError("index_classify returned no SW2021 level-3 industries")

    member_frames: list[pd.DataFrame] = []
    for level3_code in level3_codes:
        frame = caller("index_member_all", {"l3_code": level3_code})
        if frame is None or frame.empty:
            continue
        member_frames.append(frame)

    if not member_frames:
        raise RuntimeError("index_member_all returned no SW2021 constituents")
    members = pd.concat(member_frames, ignore_index=True).drop_duplicates().reset_index(drop=True)
    return classes, members


def build_sw_industry_dictionary(
    classes: pd.DataFrame,
    members: pd.DataFrame,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """构建一级、二级、三级平铺字典和层级树。"""
    catalogue = normalise_sw_catalogue(classes)
    current_members = normalise_current_members(members)
    levels = {
        "level1": _build_level_dictionary(catalogue, current_members, "L1"),
        "level2": _build_level_dictionary(catalogue, current_members, "L2"),
        "level3": _build_level_dictionary(catalogue, current_members, "L3"),
    }
    hierarchy = _build_hierarchy(catalogue, levels)
    stock_codes = sorted(current_members["ts_code"].unique().tolist())
    multi_mapped = (
        current_members.groupby("ts_code")["l3_code"].nunique().loc[lambda values: values > 1].index.tolist()
    )
    timestamp = generated_at or datetime.now().astimezone().isoformat(timespec="seconds")
    return {
        "metadata": {
            "source": "Tushare Pro index_classify + index_member_all",
            "classification": "SW2021",
            "generated_at": timestamp,
            "membership_scope": "current memberships only",
            "stock_code_format": "Tushare ts_code",
            "counts": {
                "level1_industries": len(levels["level1"]),
                "level2_industries": len(levels["level2"]),
                "level3_industries": len(levels["level3"]),
                "unique_stocks": len(stock_codes),
                "current_membership_rows": int(len(current_members)),
                "stocks_with_multiple_level3_memberships": len(multi_mapped),
            },
        },
        "level1": hierarchy,
        "level1_index": levels["level1"],
        "level2_index": levels["level2"],
        "level3_index": levels["level3"],
    }


def save_sw_industry_dictionary(
    payload: dict[str, Any],
    *,
    output_path: str | Path | None = None,
    cache_root: str | Path | None = None,
) -> Path:
    """以 UTF-8 JSON 保存行业成分大字典。"""
    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    path = Path(output_path) if output_path is not None else root / "static" / SW_DICTIONARY_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")
    return path


def collect_build_and_save_sw_dictionary(
    *,
    output_path: str | Path | None = None,
    cache_root: str | Path | None = None,
    caller: Callable[[str, dict[str, Any]], pd.DataFrame] = call_api,
) -> tuple[Path, dict[str, Any]]:
    """采集完整静态表，写入项目缓存，并生成行业成分大字典。"""
    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    classes, members = collect_sw_industry_data(caller=caller)
    write_dataset(classes, "index_classify", static=True, cache_root=root)
    write_dataset(members, "index_member_all", static=True, cache_root=root)
    load_sw_industry_membership(cache_root=root, rebuild=True)
    payload = build_sw_industry_dictionary(classes, members)
    path = save_sw_industry_dictionary(payload, output_path=output_path, cache_root=root)
    return path, payload


def normalise_sw_catalogue(classes: pd.DataFrame) -> pd.DataFrame:
    """把 Tushare 分类目录统一为 code/name/level/parent_code。"""
    if classes is None or classes.empty:
        return pd.DataFrame(columns=["code", "name", "level", "industry_code", "parent_code"])
    code_column = _first_column(classes, ("index_code", "industry_code", "code"))
    name_column = _first_column(classes, ("industry_name", "name"))
    level_column = _first_column(classes, ("level",))
    industry_code_column = _first_column(classes, ("industry_code",))
    parent_column = _first_column(classes, ("parent_code",))
    if not code_column or not name_column or not level_column:
        raise ValueError("index_classify lacks code, name, or level columns")

    result = pd.DataFrame(
        {
            "code": classes[code_column].fillna("").astype(str).str.strip(),
            "name": classes[name_column].fillna("").astype(str).str.strip(),
            "level": classes[level_column].map(_normalise_level),
            "industry_code": (
                classes[industry_code_column].fillna("").astype(str).str.strip()
                if industry_code_column
                else ""
            ),
            "parent_code": (
                classes[parent_column].fillna("").astype(str).str.strip() if parent_column else ""
            ),
        }
    )
    result = result[result["code"].ne("") & result["level"].isin(LEVEL_KEYS)]
    return result.drop_duplicates("code", keep="first").sort_values(["level", "code"]).reset_index(drop=True)


def normalise_current_members(members: pd.DataFrame) -> pd.DataFrame:
    """保留当前有效成分，并统一三级行业路径字段。"""
    columns = [
        "ts_code",
        "l1_code",
        "l1_name",
        "l2_code",
        "l2_name",
        "l3_code",
        "l3_name",
    ]
    if members is None or members.empty or "ts_code" not in members.columns:
        return pd.DataFrame(columns=columns)

    data = members.copy()
    if "out_date" in data.columns:
        out_date = data["out_date"].fillna("").astype(str).str.strip()
        data = data[out_date.eq("")]
    aliases = {
        "ts_code": ("ts_code",),
        "l1_code": ("l1_code", "sw_l1_code"),
        "l1_name": ("l1_name", "sw_l1_name"),
        "l2_code": ("l2_code", "sw_l2_code"),
        "l2_name": ("l2_name", "sw_l2_name"),
        "l3_code": ("l3_code", "sw_l3_code"),
        "l3_name": ("l3_name", "sw_l3_name"),
    }
    result = pd.DataFrame(index=data.index)
    for target, choices in aliases.items():
        source = _first_column(data, choices)
        result[target] = data[source].fillna("").astype(str).str.strip() if source else ""
    result = result[
        result["ts_code"].str.fullmatch(r"\d{6}\.(?:SZ|SH|BJ)", na=False)
        & result["l3_code"].ne("")
    ]
    return result.drop_duplicates(["ts_code", "l3_code"]).sort_values(["l1_code", "l2_code", "l3_code", "ts_code"]).reset_index(drop=True)


def _build_level_dictionary(
    catalogue: pd.DataFrame,
    members: pd.DataFrame,
    level: str,
) -> dict[str, dict[str, Any]]:
    level_number = level[-1]
    code_column = f"l{level_number}_code"
    name_column = f"l{level_number}_name"
    entries: dict[str, dict[str, Any]] = {}
    for row in catalogue[catalogue["level"] == level].itertuples(index=False):
        entries[row.code] = {"name": row.name, "stock_codes": []}
    if not members.empty:
        for code, group in members[members[code_column].ne("")].groupby(code_column):
            names = sorted(name for name in group[name_column].unique().tolist() if name)
            entry = entries.setdefault(str(code), {"name": names[0] if names else "", "stock_codes": []})
            if not entry["name"] and names:
                entry["name"] = names[0]
            entry["stock_codes"] = sorted(group["ts_code"].unique().tolist())
    return dict(sorted(entries.items()))


def _build_hierarchy(
    catalogue: pd.DataFrame,
    levels: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    industry_code_to_code = catalogue.set_index("industry_code")["code"].to_dict()
    parent_by_code = {
        row.code: industry_code_to_code.get(row.parent_code, row.parent_code)
        for row in catalogue.itertuples(index=False)
    }
    hierarchy: dict[str, dict[str, Any]] = {}
    for level1_code, level1 in levels["level1"].items():
        hierarchy[level1_code] = {
            "name": level1["name"],
            "children": {},
        }
    for level2_code, level2 in levels["level2"].items():
        parent = parent_by_code.get(level2_code, "")
        if parent not in hierarchy:
            parent = _infer_parent_from_members(level2_code, levels["level1"], levels["level2"])
        if parent not in hierarchy:
            continue
        hierarchy[parent]["children"][level2_code] = {
            "name": level2["name"],
            "children": {},
        }
    for level3_code, level3 in levels["level3"].items():
        parent = parent_by_code.get(level3_code, "")
        target = _find_level2_node(hierarchy, parent)
        if target is None:
            continue
        target["children"][level3_code] = {
            "name": level3["name"],
            "stock_codes": level3["stock_codes"],
        }
    return hierarchy


def _infer_parent_from_members(
    child_code: str,
    parents: dict[str, dict[str, Any]],
    children: dict[str, dict[str, Any]],
) -> str:
    child_stocks = set(children.get(child_code, {}).get("stock_codes", []))
    if not child_stocks:
        return ""
    candidates = [
        parent_code
        for parent_code, parent in parents.items()
        if child_stocks.issubset(set(parent.get("stock_codes", [])))
    ]
    return sorted(candidates)[0] if len(candidates) == 1 else ""


def _find_level2_node(
    hierarchy: dict[str, dict[str, Any]],
    level2_code: str,
) -> dict[str, Any] | None:
    for level1 in hierarchy.values():
        if level2_code in level1["children"]:
            return level1["children"][level2_code]
    return None


def _normalise_level(value: Any) -> str:
    text = str(value).strip().upper()
    aliases = {
        "1": "L1",
        "一级": "L1",
        "LEVEL1": "L1",
        "2": "L2",
        "二级": "L2",
        "LEVEL2": "L2",
        "3": "L3",
        "三级": "L3",
        "LEVEL3": "L3",
    }
    return aliases.get(text, text)


def _first_column(data: pd.DataFrame, choices: tuple[str, ...]) -> str | None:
    return next((column for column in choices if column in data.columns), None)
