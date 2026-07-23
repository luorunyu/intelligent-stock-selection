from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
import re
from typing import Any


STOCK_CODE_RE = re.compile(r"\b\d{6}\.(?:SH|SZ|BJ)\b")
HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$")


@dataclass(frozen=True)
class RelationshipMapContext:
    end_date: str | None
    source_files: list[str]
    themes: list[dict[str, Any]]
    companies: list[dict[str, Any]]
    theme_company_index: dict[str, list[dict[str, Any]]]
    company_theme_index: dict[str, list[dict[str, Any]]]
    subline_index: dict[str, list[dict[str, Any]]]
    chain_index: dict[str, list[dict[str, Any]]]
    map_updates: list[dict[str, Any]]
    falsified: list[dict[str, Any]]
    pending_diffusion: list[dict[str, Any]]
    not_started: list[dict[str, Any]]
    gaps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_relationship_map_context(
    *,
    records_root: str | Path = "analysis_records",
    end_date: str | None = None,
    lookback: int = 20,
    recent_limit: int = 8,
) -> RelationshipMapContext:
    root = Path(records_root) / "stock_relationship_map"
    paths = _relationship_map_paths(root, end_date=end_date, lookback=lookback, limit=recent_limit)
    companies: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    gaps: list[str] = []

    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="ignore")
            gaps.append(f"decoded with errors: {path}")
        parsed = parse_relationship_map_text(text, source_path=path)
        companies.extend(parsed["companies"])
        updates.extend(parsed["map_updates"])

    companies = _dedupe_companies(companies)
    theme_company_index = _index_by(companies, "theme")
    company_theme_index = _index_by(companies, "ts_code")
    subline_index = _index_by(companies, "subline")
    chain_index = _index_by(companies, "chain")
    themes = _theme_rows(theme_company_index)
    falsified = [item for item in companies if _has_status(item, ["证伪", "降级"])]
    pending = [item for item in companies if _has_status(item, ["待扩散"])]
    not_started = [item for item in companies if _has_status(item, ["未启动"])]

    return RelationshipMapContext(
        end_date=end_date,
        source_files=[str(path) for path in paths],
        themes=themes,
        companies=companies,
        theme_company_index=theme_company_index,
        company_theme_index=company_theme_index,
        subline_index=subline_index,
        chain_index=chain_index,
        map_updates=updates[:80],
        falsified=falsified[:80],
        pending_diffusion=pending[:80],
        not_started=not_started[:80],
        gaps=gaps,
    )


def parse_relationship_map_text(text: str, *, source_path: str | Path | None = None) -> dict[str, list[dict[str, Any]]]:
    source = str(source_path) if source_path is not None else ""
    source_date = _path_date(Path(source)) if source else None
    companies: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    current_theme = ""
    current_subline = ""
    current_chain = ""
    current_section = ""
    current_card: dict[str, Any] | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        heading = HEADING_RE.match(line)
        if heading:
            title = _clean_heading(heading.group(1))
            current_section = title
            inferred_theme = _theme_from_heading(title)
            if inferred_theme:
                current_theme = inferred_theme
            inferred_chain = _chain_from_text(title)
            if inferred_chain:
                current_chain = inferred_chain
            card = _company_card_from_heading(title, source=source, source_date=source_date, theme=current_theme, chain=current_chain)
            if card:
                if current_card:
                    companies.append(current_card)
                current_card = card
            continue

        if "历史地图更新" in current_section or any(word in line for word in ["新增公司", "之前遗漏", "之前错误", "降级", "证伪"]):
            updates.append(_update_row(line, source=source, source_date=source_date))

        if current_card is not None:
            _update_card_from_line(current_card, line)
            if current_card.get("theme"):
                current_theme = current_card["theme"]
            if current_card.get("subline"):
                current_subline = current_card["subline"]
            if current_card.get("chain"):
                current_chain = current_card["chain"]

        chain = _chain_from_text(line)
        if chain:
            current_chain = chain
        subline = _subline_from_line(line)
        if subline:
            current_subline = subline

        for code in STOCK_CODE_RE.findall(line):
            if current_card and current_card.get("ts_code") == code:
                continue
            name = _name_before_code(line, code)
            companies.append(
                {
                    "ts_code": code,
                    "name": name or code,
                    "theme": current_theme,
                    "subline": current_subline,
                    "chain": current_chain,
                    "relationship_strength": _relationship_strength(line),
                    "market_status": _market_status(line or current_section),
                    "role_in_map": _role_from_text(line),
                    "evidence": [line[:220]],
                    "gaps": [],
                    "source_path": source,
                    "source_date": source_date.strftime("%Y-%m-%d") if source_date else None,
                }
            )

    if current_card:
        companies.append(current_card)
    return {"companies": companies, "map_updates": updates}


def related_map_entries_for_hotspot(hotspot: dict[str, Any], context: dict[str, Any], *, limit: int = 40) -> list[dict[str, Any]]:
    terms = _hotspot_terms(hotspot)
    companies = context.get("companies") or []
    rows: list[dict[str, Any]] = []
    for item in companies:
        haystack = " ".join(
            str(item.get(field) or "")
            for field in ["theme", "subline", "chain", "name", "role_in_map", "market_status"]
        )
        if any(term and term in haystack for term in terms):
            rows.append(item)
    return _dedupe_companies(rows)[:limit]


def _relationship_map_paths(root: Path, *, end_date: str | None, lookback: int, limit: int) -> list[Path]:
    if not root.exists():
        return []
    dated: list[tuple[Path, datetime]] = []
    for path in root.glob("*/*.md"):
        item_date = _path_date(path)
        if item_date is not None:
            dated.append((path, item_date))
    if not dated:
        return sorted(root.glob("*/*.md"), key=lambda path: path.stat().st_mtime, reverse=True)[:limit]

    anchor = _parse_date(end_date) if end_date else None
    if anchor is None:
        anchor = max(item[1] for item in dated)
    start = anchor - timedelta(days=max(lookback, 1) * 2)
    filtered = [(path, date) for path, date in dated if start <= date <= anchor]
    filtered = sorted(filtered, key=lambda item: item[1], reverse=True)
    return [path for path, _ in filtered[:limit]]


def _company_card_from_heading(title: str, *, source: str, source_date: datetime | None, theme: str, chain: str) -> dict[str, Any] | None:
    codes = STOCK_CODE_RE.findall(title)
    if not codes:
        return None
    code = codes[0]
    return {
        "ts_code": code,
        "name": _name_before_code(title, code) or code,
        "theme": theme,
        "subline": "",
        "chain": chain,
        "relationship_strength": "",
        "market_status": "",
        "role_in_map": "",
        "evidence": [],
        "gaps": [],
        "source_path": source,
        "source_date": source_date.strftime("%Y-%m-%d") if source_date else None,
    }


def _update_card_from_line(card: dict[str, Any], line: str) -> None:
    field_map = {
        "所属主线": "theme",
        "所属子线": "subline",
        "主题角色": "role_in_map",
        "市场状态": "market_status",
        "产业链位置": "chain",
    }
    for label, field in field_map.items():
        if label in line:
            value = _value_after_label(line, label)
            if value:
                card[field] = value
    strength = _relationship_strength(line)
    if strength and not card.get("relationship_strength"):
        card["relationship_strength"] = strength
    if any(word in line for word in ["主营", "产品", "客户", "催化", "财务验证", "市场验证", "信息缺口", "观察条件", "失效条件"]):
        card.setdefault("evidence", []).append(line[:220])
    if "缺口" in line:
        card.setdefault("gaps", []).append(line[:220])


def _theme_from_heading(title: str) -> str:
    if "主线" not in title:
        return ""
    text = re.sub(r"^主线\s*\d*\s*[：:、.-]*\s*", "", title).strip()
    text = re.sub(r"（.*?）", "", text).strip()
    return text if 2 <= len(text) <= 50 else ""


def _subline_from_line(line: str) -> str:
    if "所属子线" in line:
        return _value_after_label(line, "所属子线")
    if line.startswith("|") and "已异动" in line:
        parts = [part.strip() for part in line.strip("|").split("|")]
        return parts[0] if parts else ""
    return ""


def _chain_from_text(text: str) -> str:
    for chain in ["上游", "中游", "下游", "配套服务"]:
        if chain in text:
            return chain
    return ""


def _relationship_strength(text: str) -> str:
    for value in ["核心", "相关", "映射", "弱相关", "证伪", "待验证"]:
        if value in text:
            return value
    return ""


def _market_status(text: str) -> str:
    for value in ["已异动", "待扩散", "未启动", "掉队", "证伪", "降级", "修复", "回流"]:
        if value in text:
            return value
    return ""


def _role_from_text(text: str) -> str:
    for value in ["龙头", "中军", "核心", "弹性", "跟随", "补涨", "掉队", "证伪"]:
        if value in text:
            return value
    return ""


def _update_row(line: str, *, source: str, source_date: datetime | None) -> dict[str, Any]:
    return {
        "date": source_date.strftime("%Y-%m-%d") if source_date else None,
        "source_path": source,
        "type": _update_type(line),
        "line": line.strip("- ")[:240],
        "stocks": STOCK_CODE_RE.findall(line),
    }


def _update_type(line: str) -> str:
    for value in ["新增", "遗漏", "错误", "降级", "证伪", "增强", "减弱", "更新"]:
        if value in line:
            return value
    return "map_update"


def _theme_rows(theme_index: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for theme, items in theme_index.items():
        if not theme:
            continue
        rows.append(
            {
                "theme": theme,
                "company_count": len(items),
                "subline_count": len({item.get("subline") for item in items if item.get("subline")}),
                "pending_diffusion": sum(1 for item in items if _has_status(item, ["待扩散"])),
                "not_started": sum(1 for item in items if _has_status(item, ["未启动"])),
                "falsified": sum(1 for item in items if _has_status(item, ["证伪", "降级"])),
            }
        )
    return sorted(rows, key=lambda item: item["company_count"], reverse=True)


def _index_by(companies: list[dict[str, Any]], field: str) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for item in companies:
        key = str(item.get(field) or "")
        if not key:
            continue
        index.setdefault(key, []).append(item)
    return index


def _dedupe_companies(companies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str]] = set()
    result: list[dict[str, Any]] = []
    for item in companies:
        key = (
            str(item.get("ts_code") or ""),
            str(item.get("theme") or ""),
            str(item.get("subline") or ""),
            str(item.get("source_path") or ""),
        )
        if key in seen or not key[0]:
            continue
        seen.add(key)
        result.append(item)
    return result


def _has_status(item: dict[str, Any], values: list[str]) -> bool:
    text = " ".join(str(item.get(field) or "") for field in ["market_status", "role_in_map", "relationship_strength"])
    return any(value in text for value in values)


def _hotspot_terms(hotspot: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for field in ["theme", "family"]:
        value = str(hotspot.get(field) or "")
        if value:
            terms.append(value.replace("活跃", "").strip())
    terms.extend(str(item) for item in hotspot.get("industries") or [])
    terms.extend(str(item) for item in hotspot.get("aliases") or [])
    return [term for term in dict.fromkeys(terms) if len(term) >= 2]


def _value_after_label(line: str, label: str) -> str:
    text = line.strip("- ").strip()
    text = re.sub(rf"^{re.escape(label)}\s*[：:]\s*", "", text)
    return text.strip("。；; ")


def _name_before_code(text: str, code: str) -> str:
    left = text.split(code, 1)[0]
    left = re.sub(r"^[#\-\s|`>*]+", "", left)
    left = left.strip(" ：:（(")
    parts = re.split(r"[，,、|：:\s]+", left)
    return parts[-1].strip() if parts else ""


def _clean_heading(value: str) -> str:
    return value.strip().strip("#").strip()


def _path_date(path: Path) -> datetime | None:
    return _parse_date(path.stem)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    compact = value.replace("-", "")
    if len(compact) != 8 or not compact.isdigit():
        return None
    try:
        return datetime.strptime(compact, "%Y%m%d")
    except ValueError:
        return None
