"""基于申万三级行业和全市场行情的无种子热点发现。

正式行业归属来自 Tushare SW2021；本模块不维护概念主题、产业链标签或预设股票。
如果市场出现跨行业共振但尚无公司业务证据，只输出“未命名动态共振簇”。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any

import pandas as pd

from stock_selection.context.relationship_map_context import build_relationship_map_context
from stock_selection.context.theme_rotation import build_theme_rotation_context
from stock_selection.context.theme_stock_roles import classify_market_hotspot_roles, summarize_map_related_candidates
from stock_selection.data.industry_taxonomy import attach_sw_industry, load_sw_industry_membership
from stock_selection.data.trading_calendar import cached_trade_dates
from stock_selection.data.tushare_cache import DEFAULT_CACHE_ROOT, read_dataset


@dataclass(frozen=True)
class ThemeDiscoveryResult:
    """一次无种子主题发现的完整结果及其申万分类来源。"""

    trade_date: str
    lookback_dates: list[str]
    market_hotspots: list[dict[str, Any]] = field(default_factory=list)
    industry_hotspots: list[dict[str, Any]] = field(default_factory=list)
    dynamic_clusters: list[dict[str, Any]] = field(default_factory=list)
    theme_lifecycle: list[dict[str, Any]] = field(default_factory=list)
    active_pool: list[dict[str, Any]] = field(default_factory=list)
    relationship_map_context: dict[str, Any] = field(default_factory=dict)
    theme_stock_roles: list[dict[str, Any]] = field(default_factory=list)
    map_related_candidates: list[dict[str, Any]] = field(default_factory=list)
    theme_rotation_context: dict[str, Any] = field(default_factory=dict)
    taxonomy: dict[str, Any] = field(default_factory=dict)
    output_path: str | None = None
    missing_datasets: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """保留 JSON 兼容字段，方便历史轮动模块读取新旧记录。"""
        payload = asdict(self)
        # ``themes`` 是历史轮动模块使用的兼容字段，内容为当前市场热点而非预设概念。
        payload["themes"] = list(self.market_hotspots)
        payload["tag_clusters"] = []
        return payload


def discover_active_themes(
    trade_date: str | None = None,
    *,
    cache_root: str | Path | None = None,
    records_root: str | Path = "analysis_records",
    lookback: int = 6,
    top_n_pct: int = 120,
    top_n_amount: int = 120,
    min_theme_score: float = 6.0,
    write: bool = False,
) -> ThemeDiscoveryResult:
    """从全市场行情出发，先发现申万行业热点，再输出未命名共振簇。

    ``min_theme_score`` 是透明的筛选参数，不对应任何主题名或股票代码。概念名称
    只能由后续公司主营、公告和新闻证据确认，本函数不擅自给簇命名。
    """
    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    dates = cached_trade_dates(cache_root=root, api_name="daily")
    if not dates:
        raise RuntimeError(f"no cached daily datasets found under {root}")
    end_date = _compact_date(trade_date) if trade_date else dates[-1]
    if end_date not in dates:
        raise RuntimeError(f"trade_date {end_date} is not cached in daily datasets")
    end_index = dates.index(end_date)
    lookback_dates = dates[max(0, end_index - lookback + 1) : end_index + 1]

    taxonomy = load_sw_industry_membership(cache_root=root)
    active_pool = build_active_pool(
        end_date,
        lookback_dates=lookback_dates,
        membership=taxonomy.data,
        cache_root=root,
        top_n_pct=top_n_pct,
        top_n_amount=top_n_amount,
    )
    industry_hotspots = discover_industry_hotspots(
        end_date,
        lookback_dates=lookback_dates,
        membership=taxonomy.data,
        cache_root=root,
    )
    dynamic_clusters = discover_dynamic_clusters(active_pool, min_theme_score=min_theme_score)
    market_hotspots = rank_market_hotspots(industry_hotspots, dynamic_clusters, min_theme_score=min_theme_score)
    lifecycle = build_theme_lifecycle(market_hotspots)

    # 历史地图仅补充已记录的关系和风险，不能把旧概念反向变成当天热点。
    relationship_context = build_relationship_map_context(
        records_root=records_root,
        end_date=end_date,
        lookback=max(lookback, 20),
        recent_limit=8,
    ).to_dict()
    roles = classify_market_hotspot_roles(
        market_hotspots,
        active_pool=active_pool,
        relationship_map_context=relationship_context,
        limit=8,
    )
    map_candidates = summarize_map_related_candidates(
        market_hotspots,
        relationship_map_context=relationship_context,
        limit_per_theme=20,
    )
    rotation = build_theme_rotation_context(
        end_date=end_date,
        records_root=records_root,
        lookback=max(lookback, 20),
    ).to_dict()
    warnings = list(taxonomy.warnings)
    if taxonomy.data.empty:
        warnings.append("No SW2021 membership is available; no formal-industry hotspot is emitted.")
    warnings.extend(
        [
            "No runtime theme seeds, stock lists, industry keyword families, or concept tags are used.",
            "A dynamic cluster is market co-movement only; its concept label remains unassigned until company-business evidence is collected.",
            "Historical relationship-map entries enrich risk review only and do not override current market confirmation.",
        ]
    )
    result = ThemeDiscoveryResult(
        trade_date=end_date,
        lookback_dates=lookback_dates,
        market_hotspots=market_hotspots[:30],
        industry_hotspots=industry_hotspots[:60],
        dynamic_clusters=dynamic_clusters[:30],
        theme_lifecycle=lifecycle[:30],
        active_pool=active_pool[:200],
        relationship_map_context=_trim_relationship_map_context(relationship_context),
        theme_stock_roles=roles[:8],
        map_related_candidates=map_candidates[:120],
        theme_rotation_context=_trim_theme_rotation_context(rotation),
        taxonomy=taxonomy.summary(),
        missing_datasets=[] if not taxonomy.data.empty else ["sw_industry_membership"],
        warnings=warnings,
    )
    if not write:
        return result
    output_path = write_theme_discovery(result, records_root=records_root)
    return ThemeDiscoveryResult(**{**result.__dict__, "output_path": str(output_path)})


def build_active_pool(
    trade_date: str,
    *,
    lookback_dates: list[str],
    membership: pd.DataFrame,
    cache_root: str | Path | None = None,
    top_n_pct: int = 120,
    top_n_amount: int = 120,
) -> list[dict[str, Any]]:
    """以市场强度筛出活跃股票，并附上每股申万一级、二级、三级归属。"""
    latest = _daily_snapshot(trade_date, membership=membership, cache_root=cache_root)
    if latest.empty:
        return []
    latest = latest[latest["ts_code"].notna()].copy()
    latest["amount_yi"] = latest["amount"].astype(float) / 100000.0
    latest["limit_up_hit"] = _limit_up_mask(latest)
    candidates: set[str] = set()
    candidates.update(latest.nlargest(top_n_pct, "pct_chg")["ts_code"].astype(str))
    candidates.update(latest.nlargest(top_n_amount, "amount")["ts_code"].astype(str))
    candidates.update(latest.loc[latest["limit_up_hit"], "ts_code"].astype(str))
    if "turnover_rate" in latest.columns:
        candidates.update(latest.nlargest(80, "turnover_rate")["ts_code"].astype(str))

    history = _stock_pct_history(lookback_dates, cache_root=cache_root)
    rows: list[dict[str, Any]] = []
    for _, item in latest[latest["ts_code"].astype(str).isin(candidates)].iterrows():
        code = str(item["ts_code"])
        path = history.get(code, [])
        pre_cum = _compound_pct(path[:-1])
        pct = _float(item.get("pct_chg"))
        amount = _float(item.get("amount_yi"))
        turnover = _float(item.get("turnover_rate"))
        reasons: list[str] = []
        if bool(item.get("limit_up_hit")):
            reasons.append("涨停/接近涨停")
        if pct is not None and pct >= 7:
            reasons.append("涨幅前列")
        if amount is not None and amount >= 30:
            reasons.append("高成交")
        if turnover is not None and turnover >= 10:
            reasons.append("高换手")
        if pre_cum is not None and pre_cum >= 8:
            reasons.append("前期已有强度")
        rows.append(
            {
                "ts_code": code,
                "name": _string(item.get("name")) or code,
                # ``industry`` 仅是兼容旧消费者的三级申万显示名；正式字段完整保留。
                "industry": _string(item.get("sw_l3_name")),
                "tushare_industry": _string(item.get("tushare_industry")),
                "sw_l1_code": _string(item.get("sw_l1_code")),
                "sw_l1_name": _string(item.get("sw_l1_name")),
                "sw_l2_code": _string(item.get("sw_l2_code")),
                "sw_l2_name": _string(item.get("sw_l2_name")),
                "sw_l3_code": _string(item.get("sw_l3_code")),
                "sw_l3_name": _string(item.get("sw_l3_name")),
                "market": _string(item.get("market")),
                "pct_chg": pct,
                "amount_yi": amount,
                "turnover_rate": turnover,
                "volume_ratio": _float(item.get("volume_ratio")),
                "limit_up_hit": bool(item.get("limit_up_hit")),
                "pct_path": path,
                "pre_cum_pct": pre_cum,
                "lookback_cum_pct": _compound_pct(path),
                "active_reasons": reasons,
            }
        )
    return sorted(rows, key=lambda row: (_none_low(row["pct_chg"]), _none_low(row["amount_yi"])), reverse=True)


def discover_industry_hotspots(
    trade_date: str,
    *,
    lookback_dates: list[str],
    membership: pd.DataFrame,
    cache_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    """按申万三级计算热度，并在每行展示其完整一级、二级、三级路径。"""
    latest = _daily_snapshot(trade_date, membership=membership, cache_root=cache_root)
    if latest.empty or "sw_l3_code" not in latest.columns:
        return []
    latest = latest[latest["sw_l3_code"].fillna("").astype(str) != ""].copy()
    if latest.empty:
        return []
    latest = _attach_limit_and_moneyflow(latest, trade_date, cache_root=cache_root)
    latest["amount_yi"] = latest["amount"].astype(float) / 100000.0
    latest["limit_up_hit"] = _limit_up_mask(latest)
    history = _industry_history(lookback_dates, membership=membership, cache_root=cache_root)
    rows: list[dict[str, Any]] = []
    for code, group in latest.groupby("sw_l3_code"):
        pct = group["pct_chg"].astype(float)
        amount = float(group["amount_yi"].sum())
        up_ratio = float((pct > 0).mean())
        limit_up = int(group["limit_up_hit"].sum())
        limit_down = int(_limit_down_mask(group).sum())
        amount_path = [history.get(date, {}).get(str(code), {}).get("amount_yi", 0.0) for date in lookback_dates]
        pct_path = [history.get(date, {}).get(str(code), {}).get("avg_pct", 0.0) for date in lookback_dates]
        up_path = [history.get(date, {}).get(str(code), {}).get("up_ratio", 0.0) for date in lookback_dates]
        prev_amount = _avg(amount_path[:-1])
        amount_ratio = amount / prev_amount if prev_amount else None
        avg_pct = float(pct.mean())
        score = _industry_hotspot_score(
            avg_pct=avg_pct,
            up_ratio=up_ratio,
            amount_yi=amount,
            amount_ratio=amount_ratio,
            limit_up=limit_up,
            limit_down=limit_down,
            high_amount_count=int((group["amount_yi"] >= 10).sum()),
            high_turnover_count=int((group.get("turnover_rate", pd.Series(dtype=float)).fillna(0).astype(float) >= 8).sum()),
            net_mf_wan=_sum_optional(group, "net_mf_amount"),
            count=len(group),
        )
        first = group.iloc[0]
        l1, l2, l3 = _formal_path_from_row(first)
        top_stocks = _top_stock_rows(group, limit=12)
        rows.append(
            {
                "theme": f"申万三级：{l3['name']}",
                "source_type": "sw_l3_hotspot",
                "concept_label": None,
                "concept_status": "正式行业热点",
                "formal_industry": {"l1": l1, "l2": l2, "l3": l3},
                "industry": l3["name"],
                "industries": [l3["name"]],
                "score": round(score, 2),
                "status": _industry_hotspot_status(avg_pct, up_ratio, limit_up, amount_ratio, sum(pct_path[:-1])),
                "latest_avg_pct": round(avg_pct, 2),
                "latest_up_ratio": round(up_ratio, 3),
                "latest_amount_yi": round(amount, 2),
                "amount_ratio_vs_prev": round(amount_ratio, 2) if amount_ratio is not None else None,
                "limit_up": limit_up,
                "limit_down": limit_down,
                "count": int(len(group)),
                "avg_pct_path": [round(value, 2) for value in pct_path],
                "up_ratio_path": [round(value, 3) for value in up_path],
                "stocks": top_stocks,
                "top_stocks": top_stocks,
                "evidence": _industry_evidence(avg_pct, up_ratio, amount, amount_ratio, limit_up, int((group["amount_yi"] >= 10).sum())),
                "information_gaps": [],
            }
        )
    return sorted(rows, key=lambda row: row["score"], reverse=True)


def discover_dynamic_clusters(active_pool: list[dict[str, Any]], *, min_theme_score: float) -> list[dict[str, Any]]:
    """从当日活跃股中发现二级行业内的未命名共振簇。

    这里故意不尝试把簇命名为 CPO、PCB 等概念：价格共振只能证明共同交易，
    不能证明共同业务。名称需在报告阶段取得候选公司主营/公告证据后再确认。
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for stock in active_pool:
        code = str(stock.get("sw_l2_code") or "")
        if code:
            groups.setdefault(code, []).append(stock)
    rows: list[dict[str, Any]] = []
    for l2_code, stocks in groups.items():
        if len(stocks) < 3:
            continue
        l3_codes = {str(stock.get("sw_l3_code") or "") for stock in stocks if stock.get("sw_l3_code")}
        if len(l3_codes) < 2:
            continue
        pct = [_none_zero(stock.get("pct_chg")) for stock in stocks]
        amount = sum(_none_zero(stock.get("amount_yi")) for stock in stocks)
        up = sum(value > 0 for value in pct)
        limit_up = sum(bool(stock.get("limit_up_hit")) for stock in stocks)
        avg_pct = sum(pct) / len(pct)
        up_ratio = up / len(stocks)
        score = max(avg_pct, 0) * 2.0 + up_ratio * 5.0 + limit_up * 2.0 + min(amount / 100.0, 8.0) + min(len(l3_codes), 5) * 0.6
        if score < min_theme_score:
            continue
        first = stocks[0]
        l1 = {"code": str(first.get("sw_l1_code") or ""), "name": str(first.get("sw_l1_name") or "")}
        l2 = {"code": l2_code, "name": str(first.get("sw_l2_name") or "")}
        constituents = sorted(stocks, key=lambda row: (_none_low(row.get("pct_chg")), _none_low(row.get("amount_yi"))), reverse=True)
        rows.append(
            {
                "theme": f"未命名动态共振簇：{l1['name']} > {l2['name']}",
                "source_type": "dynamic_sw_l2_cluster",
                "concept_label": None,
                "concept_status": "未命名共振簇",
                "formal_industry": {"l1": l1, "l2": l2, "l3": None},
                "industry": l2["name"],
                "industries": sorted({str(stock.get("sw_l3_name") or "") for stock in stocks if stock.get("sw_l3_name")}),
                "score": round(score, 2),
                "status": "跨三级行业共振" if limit_up >= 2 else "二级行业内共振",
                "latest_avg_pct": round(avg_pct, 2),
                "latest_up_ratio": round(up_ratio, 3),
                "latest_amount_yi": round(amount, 2),
                "limit_up": limit_up,
                "limit_down": 0,
                "count": len(stocks),
                "stocks": [_theme_like_stock_row(stock) for stock in constituents[:12]],
                "evidence": f"{len(l3_codes)}个申万三级行业的{up}涨/{len(stocks) - up}跌，成交约{amount:.1f}亿元，涨停/接近涨停{limit_up}只",
                "information_gaps": ["未读取候选公司主营与公告证据，概念名称暂不分配。"],
            }
        )
    return sorted(rows, key=lambda row: row["score"], reverse=True)


def rank_market_hotspots(
    industry_hotspots: list[dict[str, Any]],
    dynamic_clusters: list[dict[str, Any]],
    *,
    min_theme_score: float,
) -> list[dict[str, Any]]:
    """合并正式三级行业热点和未命名共振簇，不引入旧概念优先级。"""
    rows = [row for row in [*industry_hotspots, *dynamic_clusters] if _none_zero(row.get("score")) >= min_theme_score]
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: item["score"], reverse=True):
        key = (str(row.get("source_type") or ""), str(row.get("theme") or ""))
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result


def build_theme_lifecycle(market_hotspots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """仅根据当前市场宽度与成交强度标记行业或共振簇的生命周期。"""
    rows: list[dict[str, Any]] = []
    for item in market_hotspots:
        avg_pct = _none_zero(item.get("latest_avg_pct"))
        up_ratio = _none_zero(item.get("latest_up_ratio"))
        limit_up = int(item.get("limit_up") or 0)
        if avg_pct >= 2 and up_ratio >= 0.6 and limit_up >= 2:
            state = "扩散/确认"
        elif avg_pct >= 1 and up_ratio >= 0.55:
            state = "萌芽/修复"
        elif avg_pct <= -0.5 or up_ratio <= 0.4:
            state = "削弱/退潮"
        else:
            state = "分化/观察"
        rows.append({"theme": item.get("theme"), "state": state, "score": item.get("score"), "source_type": item.get("source_type"), "evidence": item.get("evidence", ""), "next_check": _lifecycle_next_check(state)})
    return rows


def write_theme_discovery(result: ThemeDiscoveryResult, *, records_root: str | Path = "analysis_records") -> Path:
    """按交易日写入结构化热点记录，为轮动和报告复盘提供事实基线。"""
    dashed = _dash_date(result.trade_date)
    path = Path(records_root) / "theme_discovery" / dashed[:7] / f"{dashed}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = result.to_dict()
    payload["output_path"] = str(path)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _daily_snapshot(trade_date: str, *, membership: pd.DataFrame, cache_root: str | Path | None) -> pd.DataFrame:
    """合并日线、指标、股票基础信息和正式申万映射，组成全市场横截面。"""
    daily = read_dataset("daily", trade_date, cache_root=cache_root)
    basic = _read_optional_dataset("daily_basic", trade_date, cache_root=cache_root)
    stock = _read_optional_dataset("stock_basic", trade_date, cache_root=cache_root)
    data = daily.copy()
    if not basic.empty:
        keep = [column for column in ["ts_code", "turnover_rate", "volume_ratio", "pe", "pe_ttm", "pb", "total_mv", "circ_mv"] if column in basic.columns]
        data = data.merge(basic[keep], on="ts_code", how="left")
    if not stock.empty:
        keep = [column for column in ["ts_code", "name", "industry", "market", "list_date"] if column in stock.columns]
        data = data.merge(stock[keep], on="ts_code", how="left")
        if "industry" in data.columns:
            data = data.rename(columns={"industry": "tushare_industry"})
    if "name" not in data.columns:
        data["name"] = data["ts_code"]
    if "tushare_industry" not in data.columns:
        data["tushare_industry"] = ""
    return attach_sw_industry(data, membership)


def _attach_limit_and_moneyflow(data: pd.DataFrame, trade_date: str, *, cache_root: str | Path | None) -> pd.DataFrame:
    result = data.copy()
    money = _read_optional_dataset("moneyflow", trade_date, cache_root=cache_root)
    if not money.empty and {"ts_code", "net_mf_amount"}.issubset(money.columns):
        result = result.merge(money[["ts_code", "net_mf_amount"]], on="ts_code", how="left")
    limit_list = _read_optional_dataset("limit_list_d", trade_date, cache_root=cache_root)
    if not limit_list.empty and {"ts_code", "limit"}.issubset(limit_list.columns):
        result = result.merge(limit_list[["ts_code", "limit"]].drop_duplicates("ts_code"), on="ts_code", how="left")
    return result


def _industry_history(lookback_dates: list[str], *, membership: pd.DataFrame, cache_root: str | Path | None) -> dict[str, dict[str, dict[str, float]]]:
    rows: dict[str, dict[str, dict[str, float]]] = {}
    for trade_date in lookback_dates:
        snapshot = _daily_snapshot(trade_date, membership=membership, cache_root=cache_root)
        if snapshot.empty:
            continue
        snapshot = snapshot[snapshot["sw_l3_code"].fillna("").astype(str) != ""].copy()
        snapshot["amount_yi"] = snapshot["amount"].astype(float) / 100000.0
        snapshot["limit_up_hit"] = _limit_up_mask(snapshot)
        stats: dict[str, dict[str, float]] = {}
        for code, group in snapshot.groupby("sw_l3_code"):
            pct = group["pct_chg"].astype(float)
            stats[str(code)] = {"avg_pct": float(pct.mean()), "up_ratio": float((pct > 0).mean()), "amount_yi": float(group["amount_yi"].sum())}
        rows[trade_date] = stats
    return rows


def _stock_pct_history(lookback_dates: list[str], *, cache_root: str | Path | None) -> dict[str, list[float]]:
    history: dict[str, list[float]] = {}
    for trade_date in lookback_dates:
        daily = read_dataset("daily", trade_date, cache_root=cache_root)
        for _, row in daily.iterrows():
            history.setdefault(str(row["ts_code"]), []).append(_none_zero(row.get("pct_chg")))
    return history


def _top_stock_rows(group: pd.DataFrame, *, limit: int) -> list[dict[str, Any]]:
    data = group.sort_values(["pct_chg", "amount"], ascending=[False, False])
    rows: list[dict[str, Any]] = []
    for _, item in data.head(limit).iterrows():
        row = {
            "ts_code": str(item.get("ts_code")), "name": _string(item.get("name")) or str(item.get("ts_code")),
            "industry": _string(item.get("sw_l3_name")), "pct_chg": _float(item.get("pct_chg")),
            "amount_yi": _float(item.get("amount_yi")), "turnover_rate": _float(item.get("turnover_rate")),
            "volume_ratio": _float(item.get("volume_ratio")), "limit_up_hit": bool(item.get("limit_up_hit")),
        }
        row.update({column: _string(item.get(column)) for column in ["sw_l1_code", "sw_l1_name", "sw_l2_code", "sw_l2_name", "sw_l3_code", "sw_l3_name"]})
        rows.append(row)
    return rows


def _theme_like_stock_row(item: dict[str, Any]) -> dict[str, Any]:
    keep = ["ts_code", "name", "industry", "pct_chg", "amount_yi", "turnover_rate", "pre_cum_pct", "lookback_cum_pct", "active_reasons", "sw_l1_code", "sw_l1_name", "sw_l2_code", "sw_l2_name", "sw_l3_code", "sw_l3_name"]
    return {column: item.get(column) for column in keep}


def _formal_path_from_row(row: pd.Series) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    return (
        {"code": _string(row.get("sw_l1_code")), "name": _string(row.get("sw_l1_name"))},
        {"code": _string(row.get("sw_l2_code")), "name": _string(row.get("sw_l2_name"))},
        {"code": _string(row.get("sw_l3_code")), "name": _string(row.get("sw_l3_name"))},
    )


def _industry_hotspot_score(*, avg_pct: float, up_ratio: float, amount_yi: float, amount_ratio: float | None, limit_up: int, limit_down: int, high_amount_count: int, high_turnover_count: int, net_mf_wan: float | None, count: int) -> float:
    score = max(avg_pct, 0) * 2.0 + up_ratio * 5.0 + min(amount_yi / 80.0, 8.0) + limit_up * 2.0
    score += high_amount_count * 0.45 + high_turnover_count * 0.25 - limit_down * 1.5
    if amount_ratio is not None and amount_ratio >= 1.15:
        score += min((amount_ratio - 1.0) * 4.0, 3.0)
    if net_mf_wan is not None and net_mf_wan > 0:
        score += min(net_mf_wan / 200000.0, 2.0)
    return score - 2.0 if count < 3 else score


def _industry_hotspot_status(avg_pct: float, up_ratio: float, limit_up: int, amount_ratio: float | None, pre_cum_pct: float) -> str:
    if avg_pct >= 2 and up_ratio >= 0.6 and limit_up >= 2:
        return "强势扩散"
    if avg_pct >= 1 and up_ratio >= 0.55 and (limit_up >= 1 or (amount_ratio or 0) >= 1.15):
        return "新热点确认"
    if avg_pct > 0 and pre_cum_pct > 3:
        return "已有预热后延续"
    return "局部活跃" if avg_pct > 0 else "待验证"


def _industry_evidence(avg_pct: float, up_ratio: float, amount: float, amount_ratio: float | None, limit_up: int, high_amount_count: int) -> str:
    parts = [f"行业均涨{avg_pct:+.2f}%", f"上涨占比{up_ratio:.0%}", f"成交约{amount:.1f}亿元", f"涨停/接近涨停{limit_up}只"]
    if amount_ratio is not None:
        parts.append(f"成交较前均值{amount_ratio:.2f}倍")
    if high_amount_count:
        parts.append(f"10亿以上成交核心{high_amount_count}只")
    return "；".join(parts)


def _limit_up_mask(data: pd.DataFrame) -> pd.Series:
    if "pct_chg" not in data.columns:
        return pd.Series(False, index=data.index)
    pct = data["pct_chg"].astype(float)
    code = data.get("ts_code", pd.Series("", index=data.index)).fillna("").astype(str)
    market = data.get("market", pd.Series("", index=data.index)).fillna("").astype(str)
    threshold = pd.Series(9.8, index=data.index)
    threshold = threshold.mask(code.str.startswith(("300", "301", "688")) | market.str.contains("创业|科创", regex=True), 19.5)
    threshold = threshold.mask(code.str.endswith(".BJ") | market.str.contains("北交", regex=True), 29.0)
    return pct >= threshold


def _limit_down_mask(data: pd.DataFrame) -> pd.Series:
    if "pct_chg" not in data.columns:
        return pd.Series(False, index=data.index)
    pct = data["pct_chg"].astype(float)
    code = data.get("ts_code", pd.Series("", index=data.index)).fillna("").astype(str)
    market = data.get("market", pd.Series("", index=data.index)).fillna("").astype(str)
    threshold = pd.Series(-9.8, index=data.index)
    threshold = threshold.mask(code.str.startswith(("300", "301", "688")) | market.str.contains("创业|科创", regex=True), -19.5)
    threshold = threshold.mask(code.str.endswith(".BJ") | market.str.contains("北交", regex=True), -29.0)
    return pct <= threshold


def _trim_relationship_map_context(context: dict[str, Any]) -> dict[str, Any]:
    result = dict(context)
    for key, limit in {"companies": 160, "pending_diffusion": 80, "not_started": 80, "falsified": 80, "map_updates": 80}.items():
        result[key] = (result.get(key) or [])[:limit]
    return result


def _trim_theme_rotation_context(context: dict[str, Any]) -> dict[str, Any]:
    result = dict(context)
    for key, limit in {"theme_timeline": 120, "theme_transitions": 60, "rotation_edges": 40, "membership_changes": 80}.items():
        result[key] = (result.get(key) or [])[:limit]
    return result


def _read_optional_dataset(api_name: str, trade_date: str, *, cache_root: str | Path | None) -> pd.DataFrame:
    try:
        return read_dataset(api_name, trade_date, cache_root=cache_root)
    except Exception:
        return pd.DataFrame()


def _sum_optional(data: pd.DataFrame, column: str) -> float | None:
    return float(data[column].fillna(0).astype(float).sum()) if column in data.columns else None


def _avg(values: list[float]) -> float | None:
    values = [value for value in values if value is not None]
    return sum(values) / len(values) if values else None


def _lifecycle_next_check(state: str) -> str:
    if state == "扩散/确认":
        return "观察行业成交、涨停数量和后排股票是否持续扩散。"
    if state == "萌芽/修复":
        return "观察是否放量并由三级行业扩散到二级行业。"
    if state == "削弱/退潮":
        return "观察核心股承接和成交是否收缩，避免把反抽误判为新主线。"
    return "观察强度、宽度和公司业务证据是否进一步明确。"


def _compound_pct(values: list[float]) -> float | None:
    if not values:
        return None
    product = 1.0
    for value in values:
        product *= 1.0 + _none_zero(value) / 100.0
    return (product - 1.0) * 100.0


def _compact_date(value: str) -> str:
    compact = value.replace("-", "")
    if len(compact) != 8 or not compact.isdigit():
        raise ValueError(f"invalid date: {value}")
    return compact


def _dash_date(value: str) -> str:
    compact = _compact_date(value)
    return f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"


def _float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _string(value: Any) -> str:
    return "" if value is None or pd.isna(value) else str(value)


def _none_zero(value: Any) -> float:
    return _float(value) or 0.0


def _none_low(value: Any) -> float:
    number = _float(value)
    return number if number is not None else -999999.0
