from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import json
from typing import Any

import pandas as pd

from stock_selection.data.trading_calendar import cached_trade_dates
from stock_selection.data.tushare_cache import DEFAULT_CACHE_ROOT, read_dataset
from stock_selection.context.relationship_map_context import build_relationship_map_context
from stock_selection.context.theme_rotation import build_theme_rotation_context
from stock_selection.context.theme_stock_roles import classify_market_hotspot_roles, summarize_map_related_candidates


DEFAULT_SEED_PATH = Path(__file__).with_name("theme_seed_tags.yaml")
INDUSTRY_FAMILIES = {
    "医药链": ["医药", "制药", "生物", "医疗", "中药", "化学药", "CRO", "CDMO"],
    "资源品": ["有色", "铜", "铝", "铅锌", "黄金", "稀土", "小金属", "煤炭", "钢铁"],
    "能源化工": ["石油", "化工", "化纤", "橡胶", "塑料", "燃气", "电力"],
    "金融地产": ["银行", "证券", "保险", "房地产", "多元金融"],
    "消费": ["食品", "饮料", "家居", "家电", "旅游", "酒店", "商业", "零售", "纺织", "服饰"],
    "汽车链": ["汽车", "摩托车", "电气设备", "电池"],
    "机器人/高端制造": ["机器人", "机械", "机床", "自动化", "专用设备", "通用设备"],
    "军工": ["航空", "航天", "船舶", "兵器", "军工"],
    "电子科技": ["半导体", "元器件", "电子", "通信", "软件", "IT", "互联网", "计算机"],
}
GENERIC_TAGS = {"AI硬件", "半导体", "数据中心", "通信设备", "网络设备"}


@dataclass(frozen=True)
class ThemeDiscoveryResult:
    trade_date: str
    lookback_dates: list[str]
    themes: list[dict[str, Any]] = field(default_factory=list)
    tag_clusters: list[dict[str, Any]] = field(default_factory=list)
    active_pool: list[dict[str, Any]] = field(default_factory=list)
    market_hotspots: list[dict[str, Any]] = field(default_factory=list)
    industry_hotspots: list[dict[str, Any]] = field(default_factory=list)
    unseeded_themes: list[dict[str, Any]] = field(default_factory=list)
    seeded_theme_matches: list[dict[str, Any]] = field(default_factory=list)
    theme_lifecycle: list[dict[str, Any]] = field(default_factory=list)
    relationship_map_context: dict[str, Any] = field(default_factory=dict)
    theme_stock_roles: list[dict[str, Any]] = field(default_factory=list)
    map_related_candidates: list[dict[str, Any]] = field(default_factory=list)
    theme_rotation_context: dict[str, Any] = field(default_factory=dict)
    output_path: str | None = None
    missing_datasets: list[str] | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def discover_active_themes(
    trade_date: str | None = None,
    *,
    cache_root: str | Path | None = None,
    records_root: str | Path = "analysis_records",
    seed_path: str | Path | None = None,
    lookback: int = 6,
    top_n_pct: int = 120,
    top_n_amount: int = 120,
    min_theme_score: float = 6.0,
    write: bool = False,
) -> ThemeDiscoveryResult:
    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    dates = cached_trade_dates(cache_root=root, api_name="daily")
    if not dates:
        raise RuntimeError(f"no cached daily datasets found under {root}")
    end_date = _compact_date(trade_date) if trade_date else dates[-1]
    if end_date not in dates:
        raise RuntimeError(f"trade_date {end_date} is not cached in daily datasets")
    end_index = dates.index(end_date)
    lookback_dates = dates[max(0, end_index - lookback + 1) : end_index + 1]

    seeds = load_theme_seeds(seed_path or DEFAULT_SEED_PATH)
    active_pool = build_active_pool(
        end_date,
        lookback_dates=lookback_dates,
        cache_root=root,
        top_n_pct=top_n_pct,
        top_n_amount=top_n_amount,
    )
    stock_index = build_stock_tag_index(seeds)
    active_with_tags = enrich_active_pool(active_pool, stock_index)
    seeded_theme_matches = score_seed_themes(seeds, active_with_tags, lookback_dates=lookback_dates)
    themes = [theme for theme in seeded_theme_matches if theme["score"] >= min_theme_score]
    tag_clusters = score_tag_clusters(active_with_tags)
    industry_hotspots = discover_industry_hotspots(
        end_date,
        lookback_dates=lookback_dates,
        cache_root=root,
    )
    unseeded_themes = discover_unseeded_themes(
        active_with_tags,
        industry_hotspots=industry_hotspots,
        seeded_theme_matches=seeded_theme_matches,
    )
    market_hotspots = rank_market_hotspots(
        industry_hotspots=industry_hotspots,
        unseeded_themes=unseeded_themes,
        seeded_theme_matches=seeded_theme_matches,
    )
    theme_lifecycle = build_theme_lifecycle(market_hotspots)
    relationship_map_context = build_relationship_map_context(
        records_root=records_root,
        end_date=end_date,
        lookback=max(lookback, 20),
        recent_limit=8,
    ).to_dict()
    theme_stock_roles = classify_market_hotspot_roles(
        market_hotspots,
        active_pool=active_with_tags,
        relationship_map_context=relationship_map_context,
        limit=8,
    )
    map_related_candidates = summarize_map_related_candidates(
        market_hotspots,
        relationship_map_context=relationship_map_context,
        limit_per_theme=20,
    )
    theme_rotation_context = build_theme_rotation_context(
        end_date=end_date,
        records_root=records_root,
        lookback=max(lookback, 20),
    ).to_dict()

    result = ThemeDiscoveryResult(
        trade_date=end_date,
        lookback_dates=lookback_dates,
        themes=themes,
        tag_clusters=tag_clusters[:20],
        active_pool=active_with_tags[:200],
        market_hotspots=market_hotspots[:30],
        industry_hotspots=industry_hotspots[:30],
        unseeded_themes=unseeded_themes[:30],
        seeded_theme_matches=seeded_theme_matches[:30],
        theme_lifecycle=theme_lifecycle[:30],
        relationship_map_context=_trim_relationship_map_context(relationship_map_context),
        theme_stock_roles=theme_stock_roles[:8],
        map_related_candidates=map_related_candidates[:120],
        theme_rotation_context=_trim_theme_rotation_context(theme_rotation_context),
        missing_datasets=[],
        warnings=[
            "theme_seed_tags.yaml is used only for known-theme enrichment; market_hotspots and unseeded_themes drive hotspot discovery.",
            "relationship_map_context enriches related candidates and roles; it does not override current market confirmation.",
        ],
    )
    if write:
        output_path = write_theme_discovery(result, records_root=records_root)
        result = ThemeDiscoveryResult(
            trade_date=result.trade_date,
            lookback_dates=result.lookback_dates,
            themes=result.themes,
            tag_clusters=result.tag_clusters,
            active_pool=result.active_pool,
            market_hotspots=result.market_hotspots,
            industry_hotspots=result.industry_hotspots,
            unseeded_themes=result.unseeded_themes,
            seeded_theme_matches=result.seeded_theme_matches,
            theme_lifecycle=result.theme_lifecycle,
            relationship_map_context=result.relationship_map_context,
            theme_stock_roles=result.theme_stock_roles,
            map_related_candidates=result.map_related_candidates,
            theme_rotation_context=result.theme_rotation_context,
            output_path=str(output_path),
            missing_datasets=result.missing_datasets,
            warnings=result.warnings,
        )
    return result


def load_theme_seeds(path: str | Path = DEFAULT_SEED_PATH) -> dict[str, Any]:
    seed_path = Path(path)
    if not seed_path.exists():
        return {}
    text = seed_path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        payload = yaml.safe_load(text)
        return payload or {}
    except ModuleNotFoundError:
        return _parse_simple_seed_yaml(text)


def build_active_pool(
    trade_date: str,
    *,
    lookback_dates: list[str],
    cache_root: str | Path | None = None,
    top_n_pct: int = 120,
    top_n_amount: int = 120,
) -> list[dict[str, Any]]:
    latest = _daily_snapshot(trade_date, cache_root=cache_root)
    latest = latest[latest["ts_code"].notna()].copy()
    latest["amount_yi"] = latest["amount"].astype(float) / 100000.0
    latest["limit_up_hit"] = _limit_up_mask(latest)
    latest["active_reason"] = ""

    candidates: set[str] = set()
    candidates.update(latest.nlargest(top_n_pct, "pct_chg")["ts_code"].tolist())
    candidates.update(latest.nlargest(top_n_amount, "amount")["ts_code"].tolist())
    candidates.update(latest[latest["limit_up_hit"]]["ts_code"].tolist())
    if "turnover_rate" in latest.columns:
        candidates.update(latest.nlargest(80, "turnover_rate")["ts_code"].tolist())

    history = _stock_pct_history(lookback_dates, cache_root=cache_root)
    records: list[dict[str, Any]] = []
    for _, row in latest[latest["ts_code"].isin(candidates)].iterrows():
        code = str(row["ts_code"])
        pct_path = history.get(code, [])
        pre_path = pct_path[:-1]
        pre_cum = _compound_pct(pre_path)
        cum = _compound_pct(pct_path)
        reasons = []
        pct = _float(row.get("pct_chg"))
        amount_yi = _float(row.get("amount_yi"))
        turnover = _float(row.get("turnover_rate"))
        if bool(row.get("limit_up_hit")):
            reasons.append("涨停/接近涨停")
        if pct is not None and pct >= 7:
            reasons.append("涨幅前列")
        if amount_yi is not None and amount_yi >= 30:
            reasons.append("高成交")
        if turnover is not None and turnover >= 10:
            reasons.append("高换手")
        if pre_cum is not None and pre_cum >= 8:
            reasons.append("前期已有强度")
        records.append(
            {
                "ts_code": code,
                "name": _string(row.get("name")) or code,
                "industry": _string(row.get("industry")),
                "market": _string(row.get("market")),
                "pct_chg": pct,
                "amount_yi": amount_yi,
                "turnover_rate": turnover,
                "volume_ratio": _float(row.get("volume_ratio")),
                "limit_up_hit": bool(row.get("limit_up_hit")),
                "pct_path": pct_path,
                "pre_cum_pct": pre_cum,
                "lookback_cum_pct": cum,
                "active_reasons": reasons,
            }
        )
    return sorted(records, key=lambda item: (_none_low(item["pct_chg"]), _none_low(item["amount_yi"])), reverse=True)


def build_stock_tag_index(seeds: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for theme_name, theme in seeds.items():
        theme_tags = list(theme.get("tags") or [])
        for code, stock in (theme.get("stocks") or {}).items():
            entry = index.setdefault(
                str(code),
                {
                    "seed_themes": [],
                    "tags": set(),
                    "roles": {},
                    "chains": {},
                    "seed_names": {},
                },
            )
            entry["seed_themes"].append(theme_name)
            entry["tags"].update(theme_tags)
            entry["tags"].update(stock.get("tags") or [])
            entry["roles"][theme_name] = stock.get("role", "related")
            entry["chains"][theme_name] = stock.get("chain", "")
            if stock.get("name"):
                entry["seed_names"][theme_name] = stock["name"]

    normalized: dict[str, dict[str, Any]] = {}
    for code, item in index.items():
        normalized[code] = {
            "seed_themes": list(dict.fromkeys(item["seed_themes"])),
            "tags": sorted(item["tags"]),
            "roles": item["roles"],
            "chains": item["chains"],
            "seed_names": item["seed_names"],
        }
    return normalized


def enrich_active_pool(active_pool: list[dict[str, Any]], stock_index: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for item in active_pool:
        code = item["ts_code"]
        tags = stock_index.get(code, {})
        row = dict(item)
        row["seed_themes"] = tags.get("seed_themes", [])
        row["tags"] = tags.get("tags", [])
        row["theme_roles"] = tags.get("roles", {})
        row["theme_chains"] = tags.get("chains", {})
        enriched.append(row)
    return enriched


def score_seed_themes(
    seeds: dict[str, Any],
    active_pool: list[dict[str, Any]],
    *,
    lookback_dates: list[str],
) -> list[dict[str, Any]]:
    by_code = {item["ts_code"]: item for item in active_pool}
    rows: list[dict[str, Any]] = []
    for theme_name, theme in seeds.items():
        seed_stocks = theme.get("stocks") or {}
        matched = [by_code[code] for code in seed_stocks if code in by_code]
        if not matched:
            continue
        up = sum(1 for item in matched if _none_low(item.get("pct_chg")) > 0)
        down = sum(1 for item in matched if _none_low(item.get("pct_chg")) < 0)
        limit_up = sum(1 for item in matched if item.get("limit_up_hit"))
        amount = sum(_none_zero(item.get("amount_yi")) for item in matched)
        avg_pct = sum(_none_zero(item.get("pct_chg")) for item in matched) / len(matched)
        avg_pre_cum = sum(_none_zero(item.get("pre_cum_pct")) for item in matched) / len(matched)
        industries = sorted({item.get("industry") or "" for item in matched if item.get("industry")})
        cross_industry = len(industries) >= 3
        score = (
            limit_up * 3.0
            + up * 1.0
            + min(amount / 80.0, 6.0)
            + max(avg_pct, 0) / 2.0
            + max(avg_pre_cum, 0) / 4.0
            + (2.0 if cross_industry else 0.0)
            - down * 0.5
        )
        rows.append(
            {
                "theme": theme_name,
                "aliases": list(theme.get("aliases") or []),
                "score": round(score, 2),
                "matched_count": len(matched),
                "seed_count": len(seed_stocks),
                "up": up,
                "down": down,
                "limit_up": limit_up,
                "avg_pct": round(avg_pct, 2),
                "avg_pre_cum_pct": round(avg_pre_cum, 2),
                "amount_yi": round(amount, 2),
                "industries": industries,
                "cross_industry": cross_industry,
                "status": _theme_status(limit_up, up, down, avg_pct, avg_pre_cum),
                "evidence": _theme_evidence(limit_up, up, down, avg_pct, avg_pre_cum, amount, cross_industry),
                "stocks": [_theme_stock_row(item, theme_name) for item in matched],
                "lookback_dates": lookback_dates,
            }
        )
    return sorted(rows, key=lambda item: item["score"], reverse=True)


def score_tag_clusters(active_pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clusters: dict[str, list[dict[str, Any]]] = {}
    for item in active_pool:
        for tag in item.get("tags") or []:
            if tag in GENERIC_TAGS:
                continue
            clusters.setdefault(tag, []).append(item)

    rows: list[dict[str, Any]] = []
    for tag, items in clusters.items():
        if len(items) < 2:
            continue
        up = sum(1 for item in items if _none_low(item.get("pct_chg")) > 0)
        limit_up = sum(1 for item in items if item.get("limit_up_hit"))
        amount = sum(_none_zero(item.get("amount_yi")) for item in items)
        avg_pct = sum(_none_zero(item.get("pct_chg")) for item in items) / len(items)
        industries = sorted({item.get("industry") or "" for item in items if item.get("industry")})
        score = limit_up * 2.5 + up + min(amount / 100.0, 4.0) + max(avg_pct, 0) / 3.0 + (1.5 if len(industries) >= 3 else 0)
        rows.append(
            {
                "tag": tag,
                "score": round(score, 2),
                "stock_count": len(items),
                "up": up,
                "limit_up": limit_up,
                "avg_pct": round(avg_pct, 2),
                "amount_yi": round(amount, 2),
                "industries": industries,
                "stocks": [{"ts_code": item["ts_code"], "name": item["name"], "pct_chg": item["pct_chg"]} for item in items[:20]],
            }
        )
    return sorted(rows, key=lambda item: item["score"], reverse=True)


def discover_industry_hotspots(
    trade_date: str,
    *,
    lookback_dates: list[str],
    cache_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    latest = _daily_snapshot(trade_date, cache_root=cache_root)
    if latest.empty or "industry" not in latest.columns:
        return []
    latest = latest[latest["industry"].fillna("").astype(str) != ""].copy()
    latest = _attach_limit_and_moneyflow(latest, trade_date, cache_root=cache_root)
    latest["amount_yi"] = latest["amount"].astype(float) / 100000.0 if "amount" in latest.columns else 0.0
    latest["limit_up_hit"] = _limit_up_mask(latest)

    by_date = _industry_history(lookback_dates, cache_root=cache_root)
    rows: list[dict[str, Any]] = []
    for industry, group in latest.groupby("industry"):
        industry = str(industry)
        pct = group["pct_chg"].astype(float)
        amount = float(group["amount_yi"].sum()) if "amount_yi" in group.columns else 0.0
        avg_pct = float(pct.mean())
        up_ratio = float((pct > 0).mean())
        limit_up = int(group["limit_up_hit"].sum())
        limit_down = int((_limit_down_mask(group)).sum())
        net_mf = _sum_optional(group, "net_mf_amount")
        high_amount_count = int((group["amount_yi"].astype(float) >= 10).sum()) if "amount_yi" in group.columns else 0
        turnover = group["turnover_rate"].astype(float) if "turnover_rate" in group.columns else pd.Series(dtype=float)
        high_turnover_count = int((turnover >= 8).sum()) if not turnover.empty else 0
        stats_path = [by_date.get(date, {}).get(industry, {}) for date in lookback_dates]
        avg_pct_path = [_float_or_zero(item.get("avg_pct")) for item in stats_path if item]
        amount_path = [_float_or_zero(item.get("amount_yi")) for item in stats_path if item]
        up_ratio_path = [_float_or_zero(item.get("up_ratio")) for item in stats_path if item]
        prev_amount = _avg(amount_path[:-1])
        amount_ratio = amount / prev_amount if prev_amount and prev_amount > 0 else None
        pre_cum_pct = sum(avg_pct_path[:-1]) if len(avg_pct_path) > 1 else 0.0
        score = _industry_hotspot_score(
            avg_pct=avg_pct,
            up_ratio=up_ratio,
            amount_yi=amount,
            amount_ratio=amount_ratio,
            limit_up=limit_up,
            limit_down=limit_down,
            high_amount_count=high_amount_count,
            high_turnover_count=high_turnover_count,
            net_mf_wan=net_mf,
            count=len(group),
        )
        top_stocks = _top_stock_rows(group, limit=12)
        rows.append(
            {
                "theme": f"{industry}活跃",
                "industry": industry,
                "family": _industry_family(industry),
                "score": round(score, 2),
                "status": _industry_hotspot_status(avg_pct, up_ratio, limit_up, amount_ratio, pre_cum_pct),
                "latest_avg_pct": round(avg_pct, 2),
                "latest_up_ratio": round(up_ratio, 3),
                "latest_amount_yi": round(amount, 2),
                "amount_ratio_vs_prev": round(amount_ratio, 2) if amount_ratio is not None else None,
                "latest_net_mf_wan": round(net_mf, 2) if net_mf is not None else None,
                "limit_up": limit_up,
                "limit_down": limit_down,
                "high_amount_count": high_amount_count,
                "high_turnover_count": high_turnover_count,
                "count": int(len(group)),
                "avg_pct_path": [round(value, 2) for value in avg_pct_path],
                "up_ratio_path": [round(value, 3) for value in up_ratio_path],
                "amount_path": [round(value, 2) for value in amount_path],
                "top_stocks": top_stocks,
                "evidence": _industry_evidence(avg_pct, up_ratio, amount, amount_ratio, limit_up, high_amount_count),
                "source_type": "industry_hotspot",
                "seed_based": False,
            }
        )
    return sorted(rows, key=lambda item: item["score"], reverse=True)


def discover_unseeded_themes(
    active_pool: list[dict[str, Any]],
    *,
    industry_hotspots: list[dict[str, Any]],
    seeded_theme_matches: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seeded_codes = {
        stock["ts_code"]
        for theme in seeded_theme_matches
        for stock in theme.get("stocks", [])
        if stock.get("ts_code")
    }
    rows: list[dict[str, Any]] = []
    for item in industry_hotspots:
        stocks = item.get("top_stocks") or []
        seed_overlap = sum(1 for stock in stocks if stock.get("ts_code") in seeded_codes)
        seed_ratio = seed_overlap / len(stocks) if stocks else 0.0
        if item["score"] < 6.0 and item.get("latest_avg_pct", 0) < 1.0 and item.get("limit_up", 0) < 1:
            continue
        if seed_ratio >= 0.6:
            continue
        rows.append(
            {
                "theme": item["theme"],
                "family": item.get("family"),
                "score": item["score"],
                "status": item["status"],
                "industries": [item["industry"]],
                "matched_count": len(stocks),
                "seed_overlap_count": seed_overlap,
                "latest_avg_pct": item.get("latest_avg_pct"),
                "latest_up_ratio": item.get("latest_up_ratio"),
                "latest_amount_yi": item.get("latest_amount_yi"),
                "limit_up": item.get("limit_up", 0),
                "evidence": item.get("evidence", ""),
                "stocks": stocks,
                "source_type": "unseeded_industry",
                "seed_based": False,
            }
        )

    family_rows = _family_clusters(active_pool, seeded_codes)
    rows.extend(family_rows)
    return sorted(_dedupe_theme_rows(rows), key=lambda item: item["score"], reverse=True)


def rank_market_hotspots(
    *,
    industry_hotspots: list[dict[str, Any]],
    unseeded_themes: list[dict[str, Any]],
    seeded_theme_matches: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in unseeded_themes:
        rows.append(
            {
                "theme": item["theme"],
                "score": item["score"],
                "status": item.get("status", "待验证"),
                "source_type": item.get("source_type", "unseeded"),
                "seed_based": False,
                "industries": item.get("industries", []),
                "latest_avg_pct": item.get("latest_avg_pct"),
                "latest_up_ratio": item.get("latest_up_ratio"),
                "latest_amount_yi": item.get("latest_amount_yi"),
                "limit_up": item.get("limit_up", 0),
                "evidence": item.get("evidence", ""),
                "stocks": item.get("stocks", [])[:12],
            }
        )
    for item in seeded_theme_matches:
        rows.append(
            {
                "theme": item["theme"],
                "score": item["score"],
                "status": item.get("status", "待验证"),
                "source_type": "seeded_theme_match",
                "seed_based": True,
                "industries": item.get("industries", []),
                "latest_avg_pct": item.get("avg_pct"),
                "latest_up_ratio": _ratio(item.get("up", 0), item.get("up", 0) + item.get("down", 0)),
                "latest_amount_yi": item.get("amount_yi"),
                "limit_up": item.get("limit_up", 0),
                "evidence": item.get("evidence", ""),
                "stocks": item.get("stocks", [])[:12],
            }
        )
    for item in industry_hotspots[:15]:
        rows.append(
            {
                "theme": item["theme"],
                "score": item["score"],
                "status": item.get("status", "待验证"),
                "source_type": "industry_hotspot",
                "seed_based": False,
                "industries": [item.get("industry", "")],
                "latest_avg_pct": item.get("latest_avg_pct"),
                "latest_up_ratio": item.get("latest_up_ratio"),
                "latest_amount_yi": item.get("latest_amount_yi"),
                "limit_up": item.get("limit_up", 0),
                "evidence": item.get("evidence", ""),
                "stocks": item.get("top_stocks", [])[:12],
            }
        )
    return sorted(_dedupe_theme_rows(rows), key=lambda item: item["score"], reverse=True)


def build_theme_lifecycle(market_hotspots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in market_hotspots:
        avg_pct = _none_zero(item.get("latest_avg_pct"))
        up_ratio = _none_zero(item.get("latest_up_ratio"))
        limit_up = int(item.get("limit_up") or 0)
        score = _none_zero(item.get("score"))
        if avg_pct >= 2 and up_ratio >= 0.6 and limit_up >= 2:
            state = "扩散/确认"
        elif avg_pct >= 1 and up_ratio >= 0.55:
            state = "萌芽/修复"
        elif avg_pct <= -0.5 or up_ratio <= 0.4:
            state = "削弱/退潮"
        elif item.get("seed_based") and score < 6:
            state = "老主题待验证"
        else:
            state = "分化/观察"
        rows.append(
            {
                "theme": item["theme"],
                "state": state,
                "score": item.get("score"),
                "source_type": item.get("source_type"),
                "seed_based": item.get("seed_based", False),
                "evidence": item.get("evidence", ""),
                "next_check": _lifecycle_next_check(state),
            }
        )
    return rows


def _trim_relationship_map_context(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "end_date": context.get("end_date"),
        "source_files": (context.get("source_files") or [])[:8],
        "themes": (context.get("themes") or [])[:30],
        "companies": (context.get("companies") or [])[:160],
        "map_updates": (context.get("map_updates") or [])[:40],
        "falsified": (context.get("falsified") or [])[:40],
        "pending_diffusion": (context.get("pending_diffusion") or [])[:60],
        "not_started": (context.get("not_started") or [])[:60],
        "gaps": context.get("gaps") or [],
    }


def _trim_theme_rotation_context(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "end_date": context.get("end_date"),
        "source_files": (context.get("source_files") or [])[-20:],
        "theme_timeline": (context.get("theme_timeline") or [])[-120:],
        "theme_transitions": (context.get("theme_transitions") or [])[:60],
        "rotation_edges": (context.get("rotation_edges") or [])[:30],
        "rising_themes": (context.get("rising_themes") or [])[:20],
        "fading_themes": (context.get("fading_themes") or [])[:20],
        "returning_themes": (context.get("returning_themes") or [])[:20],
        "retreating_themes": (context.get("retreating_themes") or [])[:20],
        "falsified_themes": (context.get("falsified_themes") or [])[:20],
        "membership_changes": (context.get("membership_changes") or [])[:80],
        "gaps": context.get("gaps") or [],
    }


def write_theme_discovery(
    result: ThemeDiscoveryResult,
    *,
    records_root: str | Path = "analysis_records",
) -> Path:
    dashed = _dash_date(result.trade_date)
    path = Path(records_root) / "theme_discovery" / dashed[:7] / f"{dashed}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = result.to_dict()
    payload["output_path"] = str(path)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _daily_snapshot(trade_date: str, *, cache_root: str | Path | None) -> pd.DataFrame:
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
    if "name" not in data.columns:
        data["name"] = data["ts_code"]
    if "industry" not in data.columns:
        data["industry"] = ""
    return data


def _read_optional_dataset(api_name: str, trade_date: str, *, cache_root: str | Path | None) -> pd.DataFrame:
    try:
        return read_dataset(api_name, trade_date, cache_root=cache_root)
    except Exception:
        return pd.DataFrame()


def _attach_limit_and_moneyflow(data: pd.DataFrame, trade_date: str, *, cache_root: str | Path | None) -> pd.DataFrame:
    result = data.copy()
    money = _read_optional_dataset("moneyflow", trade_date, cache_root=cache_root)
    if not money.empty and {"ts_code", "net_mf_amount"}.issubset(money.columns):
        result = result.merge(money[["ts_code", "net_mf_amount"]], on="ts_code", how="left")
    limit_list = _read_optional_dataset("limit_list_d", trade_date, cache_root=cache_root)
    if not limit_list.empty and {"ts_code", "limit"}.issubset(limit_list.columns):
        result = result.merge(limit_list[["ts_code", "limit"]].drop_duplicates("ts_code"), on="ts_code", how="left")
    return result


def _industry_history(lookback_dates: list[str], *, cache_root: str | Path | None) -> dict[str, dict[str, dict[str, Any]]]:
    rows: dict[str, dict[str, dict[str, Any]]] = {}
    for date in lookback_dates:
        snapshot = _daily_snapshot(date, cache_root=cache_root)
        if snapshot.empty or "industry" not in snapshot.columns:
            continue
        snapshot = snapshot[snapshot["industry"].fillna("").astype(str) != ""].copy()
        snapshot["amount_yi"] = snapshot["amount"].astype(float) / 100000.0 if "amount" in snapshot.columns else 0.0
        snapshot["limit_up_hit"] = _limit_up_mask(snapshot)
        date_stats: dict[str, dict[str, Any]] = {}
        for industry, group in snapshot.groupby("industry"):
            pct = group["pct_chg"].astype(float)
            date_stats[str(industry)] = {
                "avg_pct": round(float(pct.mean()), 2),
                "up_ratio": round(float((pct > 0).mean()), 3),
                "amount_yi": round(float(group["amount_yi"].sum()), 2),
                "limit_up": int(group["limit_up_hit"].sum()),
            }
        rows[date] = date_stats
    return rows


def _stock_pct_history(lookback_dates: list[str], *, cache_root: str | Path | None) -> dict[str, list[float]]:
    history: dict[str, list[float]] = {}
    for date in lookback_dates:
        daily = read_dataset("daily", date, cache_root=cache_root)
        for _, row in daily.iterrows():
            history.setdefault(str(row["ts_code"]), []).append(_none_zero(row.get("pct_chg")))
    return history


def _limit_up_mask(data: pd.DataFrame) -> pd.Series:
    if "pct_chg" not in data.columns:
        return pd.Series(False, index=data.index)
    pct = data["pct_chg"].astype(float)
    code = data.get("ts_code", pd.Series("", index=data.index)).fillna("").astype(str)
    market = data.get("market", pd.Series("", index=data.index)).fillna("").astype(str)
    threshold = pd.Series(9.8, index=data.index)
    wide = code.str.startswith(("300", "301", "688")) | market.str.contains("创业|科创", regex=True)
    bj = code.str.endswith(".BJ") | market.str.contains("北交", regex=True)
    threshold = threshold.mask(wide, 19.5)
    threshold = threshold.mask(bj, 29.0)
    return pct >= threshold
    market = data.get("market", pd.Series("", index=data.index)).fillna("").astype(str)
    threshold = pd.Series(9.8, index=data.index)
    threshold = threshold.mask(market.str.contains("创业板|科创板", regex=True), 19.5)
    threshold = threshold.mask(market.str.contains("北交所", regex=True), 29.0)
    return pct >= threshold


def _theme_stock_row(item: dict[str, Any], theme_name: str) -> dict[str, Any]:
    return {
        "ts_code": item["ts_code"],
        "name": item["name"],
        "industry": item.get("industry"),
        "pct_chg": item.get("pct_chg"),
        "amount_yi": item.get("amount_yi"),
        "turnover_rate": item.get("turnover_rate"),
        "pre_cum_pct": item.get("pre_cum_pct"),
        "lookback_cum_pct": item.get("lookback_cum_pct"),
        "role": (item.get("theme_roles") or {}).get(theme_name),
        "chain": (item.get("theme_chains") or {}).get(theme_name),
        "active_reasons": item.get("active_reasons") or [],
    }


def _theme_status(limit_up: int, up: int, down: int, avg_pct: float, avg_pre_cum: float) -> str:
    if limit_up >= 3 and up >= max(3, down * 2) and avg_pct >= 5:
        return "强势确认"
    if up >= 3 and avg_pct > 0 and avg_pre_cum > 3:
        return "已有预热后加强"
    if up >= 2 and avg_pct > 0:
        return "局部异动"
    return "待验证"


def _theme_evidence(limit_up: int, up: int, down: int, avg_pct: float, avg_pre_cum: float, amount: float, cross_industry: bool) -> str:
    parts = [
        f"{up}涨/{down}跌",
        f"涨停或接近涨停{limit_up}只",
        f"均涨{avg_pct:+.2f}%",
        f"成交约{amount:.1f}亿元",
    ]
    if avg_pre_cum > 3:
        parts.append(f"前期累计强度{avg_pre_cum:+.2f}%")
    if cross_industry:
        parts.append("跨静态行业聚类")
    return "，".join(parts)


def _industry_hotspot_score(
    *,
    avg_pct: float,
    up_ratio: float,
    amount_yi: float,
    amount_ratio: float | None,
    limit_up: int,
    limit_down: int,
    high_amount_count: int,
    high_turnover_count: int,
    net_mf_wan: float | None,
    count: int,
) -> float:
    score = 0.0
    score += max(avg_pct, 0) * 2.0
    score += up_ratio * 5.0
    score += min(amount_yi / 80.0, 8.0)
    score += limit_up * 2.0
    score += high_amount_count * 0.45
    score += high_turnover_count * 0.25
    if amount_ratio is not None and amount_ratio >= 1.15:
        score += min((amount_ratio - 1.0) * 4.0, 3.0)
    if net_mf_wan is not None and net_mf_wan > 0:
        score += min(net_mf_wan / 200000.0, 2.0)
    score -= limit_down * 1.5
    if count < 3:
        score -= 2.0
    return score


def _industry_hotspot_status(avg_pct: float, up_ratio: float, limit_up: int, amount_ratio: float | None, pre_cum_pct: float) -> str:
    expanding_amount = amount_ratio is not None and amount_ratio >= 1.15
    if avg_pct >= 2 and up_ratio >= 0.6 and limit_up >= 2:
        return "强势扩散"
    if avg_pct >= 1 and up_ratio >= 0.55 and (limit_up >= 1 or expanding_amount):
        return "新热点确认"
    if avg_pct > 0 and pre_cum_pct > 3:
        return "已有预热后延续"
    if avg_pct > 0:
        return "局部活跃"
    return "待验证"


def _industry_evidence(avg_pct: float, up_ratio: float, amount: float, amount_ratio: float | None, limit_up: int, high_amount_count: int) -> str:
    parts = [
        f"行业均涨{avg_pct:+.2f}%",
        f"上涨占比{up_ratio:.0%}",
        f"成交约{amount:.1f}亿元",
        f"涨停/接近涨停{limit_up}只",
    ]
    if amount_ratio is not None:
        parts.append(f"成交较前均值{amount_ratio:.2f}倍")
    if high_amount_count:
        parts.append(f"10亿以上成交核心{high_amount_count}只")
    return "；".join(parts)


def _family_clusters(active_pool: list[dict[str, Any]], seeded_codes: set[str]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in active_pool:
        family = _industry_family(item.get("industry") or "")
        if not family:
            continue
        groups.setdefault(family, []).append(item)

    rows: list[dict[str, Any]] = []
    for family, items in groups.items():
        if len(items) < 3:
            continue
        seed_overlap = sum(1 for item in items if item.get("ts_code") in seeded_codes)
        seed_ratio = seed_overlap / len(items) if items else 0.0
        if seed_ratio >= 0.6:
            continue
        up = sum(1 for item in items if _none_low(item.get("pct_chg")) > 0)
        limit_up = sum(1 for item in items if item.get("limit_up_hit"))
        amount = sum(_none_zero(item.get("amount_yi")) for item in items)
        avg_pct = sum(_none_zero(item.get("pct_chg")) for item in items) / len(items)
        up_ratio = up / len(items)
        industries = sorted({item.get("industry") or "" for item in items if item.get("industry")})
        score = max(avg_pct, 0) * 2.0 + up_ratio * 5.0 + limit_up * 2.0 + min(amount / 100.0, 8.0) + min(len(industries), 5) * 0.6
        if score < 7.0:
            continue
        stocks = sorted(items, key=lambda row: (_none_low(row.get("pct_chg")), _none_low(row.get("amount_yi"))), reverse=True)
        rows.append(
            {
                "theme": f"{family}活跃",
                "family": family,
                "score": round(score, 2),
                "status": "跨行业扩散" if len(industries) >= 2 and limit_up >= 2 else "局部活跃",
                "industries": industries,
                "matched_count": len(items),
                "seed_overlap_count": seed_overlap,
                "latest_avg_pct": round(avg_pct, 2),
                "latest_up_ratio": round(up_ratio, 3),
                "latest_amount_yi": round(amount, 2),
                "limit_up": limit_up,
                "evidence": f"{len(industries)}个相关行业、{up}涨{len(items)-up}跌、成交约{amount:.1f}亿元、涨停/接近涨停{limit_up}只",
                "stocks": [_theme_like_stock_row(item) for item in stocks[:12]],
                "source_type": "unseeded_family_cluster",
                "seed_based": False,
            }
        )
    return rows


def _industry_family(industry: str) -> str:
    text = str(industry or "")
    for family, keywords in INDUSTRY_FAMILIES.items():
        if any(keyword in text for keyword in keywords):
            return family
    return ""


def _top_stock_rows(group: pd.DataFrame, *, limit: int) -> list[dict[str, Any]]:
    sort_columns = [column for column in ["pct_chg", "amount"] if column in group.columns]
    data = group.sort_values(sort_columns, ascending=[False] * len(sort_columns)) if sort_columns else group
    rows: list[dict[str, Any]] = []
    for _, item in data.head(limit).iterrows():
        rows.append(
            {
                "ts_code": str(item.get("ts_code")),
                "name": _string(item.get("name")) or str(item.get("ts_code")),
                "industry": _string(item.get("industry")),
                "pct_chg": _float(item.get("pct_chg")),
                "amount_yi": _float(item.get("amount_yi")),
                "turnover_rate": _float(item.get("turnover_rate")),
                "volume_ratio": _float(item.get("volume_ratio")),
                "limit_up_hit": bool(item.get("limit_up_hit")),
            }
        )
    return rows


def _theme_like_stock_row(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "ts_code": item.get("ts_code"),
        "name": item.get("name"),
        "industry": item.get("industry"),
        "pct_chg": item.get("pct_chg"),
        "amount_yi": item.get("amount_yi"),
        "turnover_rate": item.get("turnover_rate"),
        "pre_cum_pct": item.get("pre_cum_pct"),
        "lookback_cum_pct": item.get("lookback_cum_pct"),
        "active_reasons": item.get("active_reasons") or [],
    }


def _dedupe_theme_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        key = (str(row.get("theme", "")), str(row.get("source_type", "")))
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _lifecycle_next_check(state: str) -> str:
    if state == "扩散/确认":
        return "观察成交额、涨停数量和后排是否继续扩散。"
    if state == "萌芽/修复":
        return "观察是否放量扩散并出现稳定核心股。"
    if state == "削弱/退潮":
        return "观察核心股承接和成交是否继续收缩，防止把反抽误判为新主线。"
    if state == "老主题待验证":
        return "仅作为历史主线验证，未获全市场数据确认前不提升为今日主线。"
    return "观察强度、宽度和催化是否进一步明确。"


def _limit_down_mask(data: pd.DataFrame) -> pd.Series:
    if "pct_chg" not in data.columns:
        return pd.Series(False, index=data.index)
    pct = data["pct_chg"].astype(float)
    threshold = pd.Series(-9.8, index=data.index)
    code = data.get("ts_code", pd.Series("", index=data.index)).fillna("").astype(str)
    market = data.get("market", pd.Series("", index=data.index)).fillna("").astype(str)
    wide = code.str.startswith(("300", "301", "688")) | market.str.contains("创业|科创", regex=True)
    bj = code.str.endswith(".BJ") | market.str.contains("北交", regex=True)
    threshold = threshold.mask(wide, -19.5)
    threshold = threshold.mask(bj, -29.0)
    return pct <= threshold


def _sum_optional(data: pd.DataFrame, column: str) -> float | None:
    if column not in data.columns:
        return None
    return float(data[column].fillna(0).astype(float).sum())


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _ratio(numerator: Any, denominator: Any) -> float | None:
    try:
        denominator = float(denominator)
        if denominator == 0:
            return None
        return round(float(numerator) / denominator, 3)
    except (TypeError, ValueError):
        return None


def _float_or_zero(value: Any) -> float:
    number = _float(value)
    return number if number is not None else 0.0


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
    if value is None or pd.isna(value):
        return ""
    return str(value)


def _none_zero(value: Any) -> float:
    number = _float(value)
    return number if number is not None else 0.0


def _none_low(value: Any) -> float:
    number = _float(value)
    return number if number is not None else -999999.0


def _parse_simple_seed_yaml(text: str) -> dict[str, Any]:
    """Tiny parser for this repository's seed YAML shape.

    It supports the subset used by ``theme_seed_tags.yaml`` so PyYAML remains
    optional for the base project.
    """

    result: dict[str, Any] = {}
    current_theme: str | None = None
    current_section: str | None = None
    current_code: str | None = None
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        if indent == 0 and line.endswith(":"):
            current_theme = line[:-1]
            result[current_theme] = {"aliases": [], "tags": [], "stocks": {}}
            current_section = None
            current_code = None
            continue
        if current_theme is None:
            continue
        if indent == 2 and line.endswith(":"):
            current_section = line[:-1]
            current_code = None
            if current_section == "stocks":
                result[current_theme].setdefault("stocks", {})
            else:
                result[current_theme].setdefault(current_section, [])
            continue
        if indent == 4 and current_section == "stocks" and line.endswith(":"):
            current_code = line[:-1]
            result[current_theme]["stocks"][current_code] = {}
            continue
        if line.startswith("- ") and current_section in {"aliases", "tags"}:
            result[current_theme][current_section].append(line[2:].strip())
            continue
        if current_section == "stocks" and current_code and ":" in line:
            key, value = line.split(":", 1)
            value = value.strip()
            if value.startswith("[") and value.endswith("]"):
                parsed: Any = [item.strip() for item in value[1:-1].split(",") if item.strip()]
            else:
                parsed = value
            result[current_theme]["stocks"][current_code][key.strip()] = parsed
    return result
