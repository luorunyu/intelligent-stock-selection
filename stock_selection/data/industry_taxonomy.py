"""SW2021 行业目录、股票三级归属和派生缓存校验。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from stock_selection.data.tushare_cache import DEFAULT_CACHE_ROOT, dataset_path, read_dataset, write_dataset


SW_MEMBERSHIP_DATASET = "sw_industry_membership"
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
        """返回映射覆盖情况和数据质量摘要。"""
        validation = validate_sw_membership(self.data)
        return {
            "source": self.source,
            "rows": int(len(self.data)),
            "mapped_stocks": int(self.data["ts_code"].nunique()) if "ts_code" in self.data else 0,
            "warnings": list(self.warnings),
            "columns": list(SW_LEVEL_COLUMNS),
            **validation,
        }


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
    derived_path = dataset_path(SW_MEMBERSHIP_DATASET, static=True, cache_root=root)
    if not rebuild and _derived_cache_is_fresh(root, derived_path):
        data = read_dataset(SW_MEMBERSHIP_DATASET, static=True, cache_root=root)
        return SwIndustryMembership(_normalise_membership(data), "derived_cache", warnings)

    return rebuild_sw_industry_membership(cache_root=root)


def load_sw_catalog(
    *,
    level: str | None = None,
    cache_root: str | Path | None = None,
) -> pd.DataFrame:
    """读取 SW2021 行业目录，并可筛选一级、二级或三级。"""
    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    data = read_dataset("index_classify", static=True, cache_root=root).copy()
    if "src" in data.columns:
        data = data[data["src"].fillna("").astype(str) == "SW2021"]
    if level is not None:
        normalised_level = normalise_sw_level(level)
        if "level" not in data.columns:
            return data.iloc[0:0].copy()
        data = data[data["level"].fillna("").astype(str).str.upper() == normalised_level]
    sort_columns = [column for column in ["level", "industry_code", "index_code"] if column in data.columns]
    return data.sort_values(sort_columns).reset_index(drop=True) if sort_columns else data.reset_index(drop=True)


def rebuild_sw_industry_membership(
    *,
    cache_root: str | Path | None = None,
) -> SwIndustryMembership:
    """由最新静态原始表重建股票到申万三级的派生映射。"""
    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    warnings: list[str] = []

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


def validate_sw_membership(data: pd.DataFrame) -> dict[str, int]:
    """统计映射覆盖、行业数量和不完整路径数量。"""
    if data.empty:
        return {
            "l1_industries": 0,
            "l2_industries": 0,
            "l3_industries": 0,
            "incomplete_rows": 0,
        }
    incomplete = data[list(SW_LEVEL_COLUMNS)].fillna("").astype(str).eq("").any(axis=1)
    return {
        "l1_industries": int(data["sw_l1_code"].nunique()),
        "l2_industries": int(data["sw_l2_code"].nunique()),
        "l3_industries": int(data["sw_l3_code"].nunique()),
        "incomplete_rows": int(incomplete.sum()),
    }


def normalise_sw_level(level: str) -> str:
    """将中文或数字行业层级统一为 L1/L2/L3。"""
    value = level.strip().upper()
    aliases = {"1": "L1", "2": "L2", "3": "L3", "一级": "L1", "二级": "L2", "三级": "L3"}
    value = aliases.get(value, value)
    if value not in {"L1", "L2", "L3"}:
        raise ValueError(f"invalid Shenwan level: {level}")
    return value


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


def _derived_cache_is_fresh(root: Path, derived_path: Path) -> bool:
    actual_derived = derived_path if derived_path.exists() else derived_path.with_suffix(".csv")
    if not actual_derived.exists():
        return False
    source_paths = [
        dataset_path("index_classify", static=True, cache_root=root),
        dataset_path("index_member_all", static=True, cache_root=root),
    ]
    actual_sources = [
        path if path.exists() else path.with_suffix(".csv")
        for path in source_paths
    ]
    existing_sources = [path for path in actual_sources if path.exists()]
    if len(existing_sources) != len(source_paths):
        return True
    return actual_derived.stat().st_mtime >= max(path.stat().st_mtime for path in existing_sources)
