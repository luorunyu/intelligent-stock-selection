"""申万行业分类的缓存映射与校验。

本模块只处理 Tushare ``SW2021`` 正式分类，不维护任何市场概念、主题名或
预设股票名单。主题发现需要的每股一级、二级、三级行业均从这里取得。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from stock_selection.data.tushare_cache import DEFAULT_CACHE_ROOT, read_dataset, write_dataset


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
        """返回适合写入热点发现 JSON 的小型来源说明。"""
        return {
            "source": self.source,
            "rows": int(len(self.data)),
            "mapped_stocks": int(self.data["ts_code"].nunique()) if "ts_code" in self.data else 0,
            "warnings": list(self.warnings),
            "columns": list(SW_LEVEL_COLUMNS),
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
