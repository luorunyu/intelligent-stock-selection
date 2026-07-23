from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import json
from typing import Any

from stock_selection.context.relationship_map_context import build_relationship_map_context


@dataclass(frozen=True)
class ThemeRotationContext:
    end_date: str
    source_files: list[str]
    theme_timeline: list[dict[str, Any]]
    theme_transitions: list[dict[str, Any]]
    rotation_edges: list[dict[str, Any]]
    rising_themes: list[dict[str, Any]]
    fading_themes: list[dict[str, Any]]
    returning_themes: list[dict[str, Any]]
    retreating_themes: list[dict[str, Any]]
    falsified_themes: list[dict[str, Any]]
    membership_changes: list[dict[str, Any]]
    gaps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_theme_rotation_context(
    *,
    end_date: str,
    records_root: str | Path = "analysis_records",
    lookback: int = 20,
) -> ThemeRotationContext:
    records = _load_theme_discovery_records(Path(records_root), end_date=end_date, lookback=lookback)
    timeline = _theme_timeline(records)
    transitions = _theme_transitions(timeline)
    rising = [item for item in transitions if item["transition"] in {"new_rising", "strengthening"}]
    fading = [item for item in transitions if item["transition"] in {"fading", "retreating"}]
    returning = [item for item in transitions if item["transition"] == "returning"]
    retreating = [item for item in transitions if item["state"] in {"retreating", "fading"}]
    rotation_edges = _rotation_edges(rising, fading)
    relationship_context = build_relationship_map_context(records_root=records_root, end_date=end_date, lookback=lookback)
    falsified = _falsified_theme_rows(relationship_context.to_dict())
    membership_changes = _membership_changes(relationship_context.to_dict(), records)
    return ThemeRotationContext(
        end_date=end_date,
        source_files=[item["path"] for item in records],
        theme_timeline=timeline,
        theme_transitions=transitions,
        rotation_edges=rotation_edges,
        rising_themes=rising[:20],
        fading_themes=fading[:20],
        returning_themes=returning[:20],
        retreating_themes=retreating[:20],
        falsified_themes=falsified[:20],
        membership_changes=membership_changes[:80],
        gaps=[] if records else ["No theme_discovery JSON records found for rotation context."],
    )


def _load_theme_discovery_records(records_root: Path, *, end_date: str, lookback: int) -> list[dict[str, Any]]:
    root = records_root / "theme_discovery"
    if not root.exists():
        return []
    paths = sorted(root.glob("*/*.json"), key=lambda path: path.stem)
    paths = [path for path in paths if _compact(path.stem) <= _compact(end_date)]
    paths = paths[-lookback:]
    records: list[dict[str, Any]] = []
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        records.append({"date": _compact(path.stem), "path": str(path), "payload": payload})
    return records


def _theme_timeline(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        payload = record["payload"]
        hotspots = payload.get("market_hotspots") or payload.get("themes") or []
        for rank, item in enumerate(hotspots[:20], start=1):
            rows.append(
                {
                    "date": record["date"],
                    "theme": item.get("theme"),
                    "rank": rank,
                    "score": _num(item.get("score")),
                    "state": _state_from_hotspot(item),
                    "source_type": item.get("source_type"),
                    "seed_based": bool(item.get("seed_based")),
                    "latest_avg_pct": item.get("latest_avg_pct") if "latest_avg_pct" in item else item.get("avg_pct"),
                    "latest_amount_yi": item.get("latest_amount_yi") if "latest_amount_yi" in item else item.get("amount_yi"),
                    "limit_up": item.get("limit_up", 0),
                }
            )
    return rows


def _theme_transitions(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_theme: dict[str, list[dict[str, Any]]] = {}
    for item in timeline:
        theme = str(item.get("theme") or "")
        if theme:
            by_theme.setdefault(theme, []).append(item)

    rows: list[dict[str, Any]] = []
    for theme, items in by_theme.items():
        items = sorted(items, key=lambda row: row["date"])
        if not items:
            continue
        latest = items[-1]
        previous = items[-2] if len(items) >= 2 else None
        transition = _transition(previous, latest, seen_before=len(items) > 1)
        rows.append(
            {
                "theme": theme,
                "date": latest["date"],
                "state": latest["state"],
                "transition": transition,
                "latest_rank": latest["rank"],
                "previous_rank": previous["rank"] if previous else None,
                "latest_score": latest.get("score"),
                "previous_score": previous.get("score") if previous else None,
                "evidence": _transition_evidence(previous, latest),
            }
        )
    return sorted(rows, key=lambda item: (item["date"], -(item.get("latest_score") or 0)), reverse=True)


def _rotation_edges(rising: list[dict[str, Any]], fading: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    latest_rising = rising[:5]
    latest_fading = fading[:5]
    for down in latest_fading:
        for up in latest_rising:
            if down["theme"] == up["theme"] or down["date"] != up["date"]:
                continue
            strength = min(max((up.get("latest_score") or 0) - (down.get("latest_score") or 0), 0) / 20.0 + 0.4, 1.0)
            rows.append(
                {
                    "date": up["date"],
                    "from_theme": down["theme"],
                    "to_theme": up["theme"],
                    "strength": round(strength, 2),
                    "evidence": f"{down['theme']} {down['transition']} while {up['theme']} {up['transition']}.",
                }
            )
    return rows[:20]


def _membership_changes(relationship_context: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    active_codes = {
        str(stock.get("ts_code"))
        for record in records[-3:]
        for hotspot in (record["payload"].get("market_hotspots") or [])
        for stock in (hotspot.get("stocks") or [])
        if stock.get("ts_code")
    }
    for item in relationship_context.get("pending_diffusion") or []:
        if str(item.get("ts_code")) in active_codes:
            rows.append({"change": "pending_diffusion_started", **_compact_map_item(item)})
    for item in relationship_context.get("not_started") or []:
        if str(item.get("ts_code")) in active_codes:
            rows.append({"change": "not_started_activated", **_compact_map_item(item)})
    for item in relationship_context.get("falsified") or []:
        if str(item.get("ts_code")) in active_codes:
            rows.append({"change": "falsified_stock_moved_needs_recheck", **_compact_map_item(item)})
    return rows


def _falsified_theme_rows(relationship_context: dict[str, Any]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for item in relationship_context.get("falsified") or []:
        theme = str(item.get("theme") or "")
        if theme:
            counts[theme] = counts.get(theme, 0) + 1
    return [{"theme": theme, "falsified_count": count} for theme, count in sorted(counts.items(), key=lambda pair: pair[1], reverse=True)]


def _state_from_hotspot(item: dict[str, Any]) -> str:
    status = str(item.get("status") or "")
    score = _num(item.get("score")) or 0
    avg = _num(item.get("latest_avg_pct") if "latest_avg_pct" in item else item.get("avg_pct")) or 0
    up_ratio = _num(item.get("latest_up_ratio")) or 0
    if any(word in status for word in ["退潮", "削弱", "证伪", "掉队"]):
        return "retreating"
    if any(word in status for word in ["扩散", "确认", "强势"]) or (score >= 15 and avg > 1.5 and up_ratio >= 0.55):
        return "diffusing"
    if any(word in status for word in ["修复", "回流"]):
        return "returning"
    if score >= 8 and avg > 0:
        return "emerging"
    return "watching"


def _transition(previous: dict[str, Any] | None, latest: dict[str, Any], *, seen_before: bool) -> str:
    if previous is None:
        return "new_rising" if latest["state"] in {"emerging", "diffusing"} else "new_watch"
    rank_change = previous["rank"] - latest["rank"]
    score_change = (latest.get("score") or 0) - (previous.get("score") or 0)
    if latest["state"] == "returning":
        return "returning"
    if latest["state"] == "retreating" or score_change <= -5 or rank_change <= -5:
        return "retreating" if latest["state"] == "retreating" else "fading"
    if rank_change >= 3 or score_change >= 5:
        return "strengthening"
    if not seen_before and latest["state"] in {"emerging", "diffusing"}:
        return "new_rising"
    return "continuing"


def _transition_evidence(previous: dict[str, Any] | None, latest: dict[str, Any]) -> str:
    if previous is None:
        return f"First appeared in recent theme records with rank {latest['rank']} and score {latest.get('score')}."
    return (
        f"rank {previous['rank']} -> {latest['rank']}, "
        f"score {previous.get('score')} -> {latest.get('score')}, state {latest['state']}."
    )


def _compact_map_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "ts_code": item.get("ts_code"),
        "name": item.get("name"),
        "theme": item.get("theme"),
        "subline": item.get("subline"),
        "chain": item.get("chain"),
        "map_status": item.get("market_status"),
        "source_path": item.get("source_path"),
    }


def _compact(value: str) -> str:
    return value.replace("-", "")


def _num(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
