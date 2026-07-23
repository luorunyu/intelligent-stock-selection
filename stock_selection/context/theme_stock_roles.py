from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from stock_selection.context.relationship_map_context import related_map_entries_for_hotspot


@dataclass(frozen=True)
class ThemeStockRoles:
    theme: str
    source_type: str
    roles: dict[str, list[dict[str, Any]]]
    role_evidence: list[str]
    map_related: list[dict[str, Any]]
    gaps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


ROLE_KEYS = [
    "leaders",
    "middle_army",
    "diffusers",
    "fast_followers",
    "laggards",
    "fallen_behind",
    "falsified",
]


def classify_hotspot_stock_roles(
    hotspot: dict[str, Any],
    *,
    active_pool: list[dict[str, Any]],
    relationship_map_context: dict[str, Any] | None = None,
    max_active: int = 20,
) -> ThemeStockRoles:
    map_context = relationship_map_context or {}
    map_entries = related_map_entries_for_hotspot(hotspot, map_context, limit=80)
    hotspot_codes = {str(stock.get("ts_code")) for stock in hotspot.get("stocks") or [] if stock.get("ts_code")}
    map_entries.extend(_map_entries_by_codes(map_context, hotspot_codes))
    map_by_code = _map_by_code(map_entries)
    hotspot_codes.update(map_by_code.keys())
    active_by_code = {str(stock.get("ts_code")): stock for stock in active_pool if stock.get("ts_code")}

    candidates: list[dict[str, Any]] = []
    for code in hotspot_codes:
        active = active_by_code.get(code, {})
        map_entry = map_by_code.get(code, {})
        if not active and not map_entry:
            continue
        candidates.append(_candidate_row(code, active=active, map_entry=map_entry))

    if not candidates:
        for stock in (hotspot.get("stocks") or [])[:max_active]:
            code = str(stock.get("ts_code") or "")
            if code:
                candidates.append(_candidate_row(code, active=stock, map_entry={}))

    roles = {key: [] for key in ROLE_KEYS}
    for row in sorted(candidates, key=_candidate_sort_key, reverse=True):
        role = _classify_role(row, hotspot)
        roles[role].append(row)

    roles = {key: value[:8] for key, value in roles.items()}
    evidence = _role_evidence(hotspot, roles)
    gaps = []
    if not map_entries:
        gaps.append("No relationship-map entries matched this hotspot; role classification relies mainly on market data.")
    return ThemeStockRoles(
        theme=str(hotspot.get("theme") or ""),
        source_type=str(hotspot.get("source_type") or ""),
        roles=roles,
        role_evidence=evidence,
        map_related=map_entries[:30],
        gaps=gaps,
    )


def classify_market_hotspot_roles(
    market_hotspots: list[dict[str, Any]],
    *,
    active_pool: list[dict[str, Any]],
    relationship_map_context: dict[str, Any] | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for hotspot in market_hotspots[:limit]:
        rows.append(
            classify_hotspot_stock_roles(
                hotspot,
                active_pool=active_pool,
                relationship_map_context=relationship_map_context,
            ).to_dict()
        )
    return rows


def summarize_map_related_candidates(
    market_hotspots: list[dict[str, Any]],
    *,
    relationship_map_context: dict[str, Any] | None = None,
    limit_per_theme: int = 20,
) -> list[dict[str, Any]]:
    context = relationship_map_context or {}
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for hotspot in market_hotspots[:8]:
        theme = str(hotspot.get("theme") or "")
        for entry in related_map_entries_for_hotspot(hotspot, context, limit=limit_per_theme):
            key = (theme, str(entry.get("ts_code") or ""))
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "hotspot": theme,
                    "ts_code": entry.get("ts_code"),
                    "name": entry.get("name"),
                    "map_theme": entry.get("theme"),
                    "subline": entry.get("subline"),
                    "chain": entry.get("chain"),
                    "relationship_strength": entry.get("relationship_strength"),
                    "market_status": entry.get("market_status"),
                    "role_in_map": entry.get("role_in_map"),
                    "source_path": entry.get("source_path"),
                }
            )
    return rows[:120]


def _candidate_row(code: str, *, active: dict[str, Any], map_entry: dict[str, Any]) -> dict[str, Any]:
    pct = _num(active.get("pct_chg"))
    amount = _num(active.get("amount_yi"))
    turnover = _num(active.get("turnover_rate"))
    pre_cum = _num(active.get("pre_cum_pct"))
    lookback = _num(active.get("lookback_cum_pct"))
    map_status = str(map_entry.get("market_status") or "")
    relationship_strength = str(map_entry.get("relationship_strength") or "")
    role_in_map = str(map_entry.get("role_in_map") or "")
    score = _market_score(pct, amount, turnover, pre_cum, lookback, bool(active.get("limit_up_hit")))
    score += _map_score(map_status, relationship_strength, role_in_map)
    return {
        "ts_code": code,
        "name": active.get("name") or map_entry.get("name") or code,
        "industry": active.get("industry"),
        "pct_chg": pct,
        "amount_yi": amount,
        "turnover_rate": turnover,
        "pre_cum_pct": pre_cum,
        "lookback_cum_pct": lookback,
        "limit_up_hit": bool(active.get("limit_up_hit")),
        "map_theme": map_entry.get("theme"),
        "subline": map_entry.get("subline"),
        "chain": map_entry.get("chain"),
        "relationship_strength": relationship_strength,
        "map_status": map_status,
        "role_in_map": role_in_map,
        "role_score": round(score, 2),
        "evidence": _candidate_evidence(active, map_entry),
        "invalid_condition": _invalid_condition(map_status),
    }


def _classify_role(row: dict[str, Any], hotspot: dict[str, Any]) -> str:
    map_status = str(row.get("map_status") or "")
    relationship = str(row.get("relationship_strength") or "")
    role_in_map = str(row.get("role_in_map") or "")
    pct = _num(row.get("pct_chg"))
    amount = _num(row.get("amount_yi"))
    turnover = _num(row.get("turnover_rate"))
    if any(word in map_status + relationship + role_in_map for word in ["证伪", "降级"]):
        return "falsified"
    if pct is not None and pct < -3:
        return "fallen_behind"
    if any(word in map_status for word in ["待扩散", "未启动"]) and pct is not None and pct > 2 and (amount or 0) >= 5:
        return "diffusers"
    if row.get("limit_up_hit") or (pct is not None and pct >= 9):
        return "leaders" if any(word in relationship + role_in_map for word in ["核心", "龙头", "中军"]) else "fast_followers"
    if amount is not None and amount >= 30:
        return "middle_army"
    if pct is not None and pct >= 4:
        return "fast_followers"
    if any(word in map_status for word in ["待扩散", "未启动"]) or (turnover is not None and turnover >= 5 and (pct or 0) > 0):
        return "laggards"
    if pct is not None and pct <= 0 and _num(hotspot.get("latest_avg_pct")) > 1:
        return "fallen_behind"
    return "laggards"


def _market_score(pct: float | None, amount: float | None, turnover: float | None, pre_cum: float | None, lookback: float | None, limit_up: bool) -> float:
    score = 0.0
    if pct is not None:
        score += max(pct, 0) * 2.0
    if amount is not None:
        score += min(amount / 10.0, 10.0)
    if turnover is not None:
        score += min(turnover / 2.0, 8.0)
    if pre_cum is not None:
        score += min(max(pre_cum, 0) / 3.0, 6.0)
    if lookback is not None:
        score += min(max(lookback, 0) / 4.0, 6.0)
    if limit_up:
        score += 8.0
    return score


def _map_score(map_status: str, relationship_strength: str, role_in_map: str) -> float:
    text = map_status + relationship_strength + role_in_map
    score = 0.0
    if any(word in text for word in ["核心", "龙头", "中军"]):
        score += 8.0
    if "已异动" in text:
        score += 4.0
    if "待扩散" in text:
        score += 2.0
    if "未启动" in text:
        score += 1.0
    if any(word in text for word in ["证伪", "降级", "掉队"]):
        score -= 10.0
    return score


def _candidate_evidence(active: dict[str, Any], map_entry: dict[str, Any]) -> list[str]:
    evidence: list[str] = []
    if active:
        evidence.append(
            "market: pct={pct}, amount_yi={amount}, turnover={turnover}, limit_up={limit}".format(
                pct=active.get("pct_chg"),
                amount=active.get("amount_yi"),
                turnover=active.get("turnover_rate"),
                limit=active.get("limit_up_hit"),
            )
        )
    if map_entry:
        evidence.append(
            "map: theme={theme}, subline={subline}, chain={chain}, status={status}, strength={strength}".format(
                theme=map_entry.get("theme"),
                subline=map_entry.get("subline"),
                chain=map_entry.get("chain"),
                status=map_entry.get("market_status"),
                strength=map_entry.get("relationship_strength"),
            )
        )
    return evidence


def _invalid_condition(map_status: str) -> str:
    if "待扩散" in map_status or "未启动" in map_status:
        return "Theme remains active but this stock fails to improve volume or relative strength."
    if "证伪" in map_status or "降级" in map_status:
        return "Do not promote unless both business evidence and sustained market confirmation improve."
    return "Role weakens if it underperforms the theme while volume contracts or capital turns negative."


def _role_evidence(hotspot: dict[str, Any], roles: dict[str, list[dict[str, Any]]]) -> list[str]:
    return [
        f"theme={hotspot.get('theme')} score={hotspot.get('score')} source={hotspot.get('source_type')}",
        "leaders={leaders}, middle_army={middle}, diffusers={diffusers}, fallen_behind={fallen}".format(
            leaders=len(roles.get("leaders", [])),
            middle=len(roles.get("middle_army", [])),
            diffusers=len(roles.get("diffusers", [])),
            fallen=len(roles.get("fallen_behind", [])),
        ),
    ]


def _map_by_code(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in entries:
        code = str(item.get("ts_code") or "")
        if not code:
            continue
        current = result.get(code)
        if current is None or _map_priority(item) > _map_priority(current):
            result[code] = item
    return result


def _map_entries_by_codes(context: dict[str, Any], codes: set[str]) -> list[dict[str, Any]]:
    if not codes:
        return []
    rows: list[dict[str, Any]] = []
    for item in context.get("companies") or []:
        if str(item.get("ts_code") or "") in codes:
            rows.append(item)
    return rows


def _map_priority(item: dict[str, Any]) -> int:
    text = " ".join(str(item.get(field) or "") for field in ["market_status", "relationship_strength", "role_in_map"])
    score = 0
    if any(word in text for word in ["核心", "龙头", "中军"]):
        score += 5
    if "已异动" in text:
        score += 3
    if "待扩散" in text:
        score += 2
    if any(word in text for word in ["证伪", "降级"]):
        score -= 4
    return score


def _candidate_sort_key(row: dict[str, Any]) -> tuple[float, float, float]:
    return (_num(row.get("role_score")) or 0.0, _num(row.get("pct_chg")) or -999.0, _num(row.get("amount_yi")) or 0.0)


def _num(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
