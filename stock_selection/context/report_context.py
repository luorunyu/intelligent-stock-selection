from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
import re
from typing import Any


STOCK_CODE_RE = re.compile(r"\b\d{6}\.(?:SH|SZ|BJ)\b")
THEME_HEADING_RE = re.compile(r"^#{2,4}\s+(?:\d+[.、]\s*)?(.+)$")
JUDGEMENT_WORDS = ["强化", "延续", "修复", "分化", "削弱", "退潮", "证伪", "待验证", "主线", "降级"]
KEYWORD_STOPWORDS = {
    "局部修复",
    "待验证",
    "主线",
    "主题",
    "观察",
    "更新",
    "历史",
    "公司",
    "行业",
    "分支",
    "核心",
    "映射",
}


@dataclass(frozen=True)
class ReportContext:
    recent_reports: list[dict[str, Any]]
    mentioned_themes: list[str]
    mentioned_stocks: list[str]
    judgement_lines: list[dict[str, str]]
    topic_backtracks: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_report_context(
    *,
    records_root: str | Path = "analysis_records",
    limit: int = 5,
    end_date: str | None = None,
    topic_lookback_days: int = 30,
) -> ReportContext:
    root = Path(records_root)
    report_paths = _recent_report_paths(root, limit)
    backtrack_paths = _backtrack_report_paths(root, end_date=end_date, days=topic_lookback_days)
    themes: list[str] = []
    stocks: list[str] = []
    judgements: list[dict[str, str]] = []
    reports: list[dict[str, Any]] = []

    for path in report_paths:
        text = path.read_text(encoding="utf-8")
        codes = sorted(set(STOCK_CODE_RE.findall(text)))
        file_themes = _extract_themes(text)
        file_judgements = _extract_judgement_lines(text, path)
        stocks.extend(codes)
        themes.extend(file_themes)
        judgements.extend(file_judgements)
        reports.append(
            {
                "path": str(path),
                "date": path.stem,
                "category": path.parent.parent.name,
                "themes": file_themes[:12],
                "stock_count": len(codes),
            }
        )

    mentioned_themes = _unique(themes)[:40]
    return ReportContext(
        recent_reports=reports,
        mentioned_themes=mentioned_themes,
        mentioned_stocks=_unique(stocks),
        judgement_lines=judgements[:80],
        topic_backtracks=_build_keyword_backtracks(backtrack_paths, mentioned_themes),
    )


def _recent_report_paths(root: Path, limit: int) -> list[Path]:
    paths: list[Path] = []
    for category in ["stock_selection", "sector_analysis", "stock_relationship_map"]:
        directory = root / category
        if directory.exists():
            paths.extend(directory.glob("*/*.md"))
    return sorted(paths, key=lambda path: path.stat().st_mtime, reverse=True)[:limit]


def _backtrack_report_paths(root: Path, *, end_date: str | None, days: int) -> list[Path]:
    paths: list[Path] = []
    for category in ["stock_selection", "sector_analysis", "stock_relationship_map"]:
        directory = root / category
        if directory.exists():
            paths.extend(directory.glob("*/*.md"))

    dated = [(path, _path_date(path)) for path in paths]
    dated = [(path, item_date) for path, item_date in dated if item_date is not None]
    if not dated:
        return sorted(paths, key=lambda path: path.stat().st_mtime, reverse=True)

    anchor = _parse_compact_date(end_date) if end_date else max(item_date for _, item_date in dated)
    if anchor is None:
        anchor = max(item_date for _, item_date in dated)
    start = anchor - timedelta(days=days)
    return [
        path
        for path, item_date in sorted(dated, key=lambda item: item[1])
        if start <= item_date <= anchor
    ]


def _extract_themes(text: str) -> list[str]:
    themes: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        match = THEME_HEADING_RE.match(stripped)
        if not match:
            continue
        title = match.group(1).strip()
        if any(
            skip in title
            for skip in [
                "分析范围",
                "说明",
                "信息缺口",
                "候选观察池",
                "市场环境门控",
                "数据来源",
                "公司级卡片",
                "股票关系图",
                "产业链拆解",
                "历史地图更新",
                "已异动",
                "待扩散",
                "未启动",
                "掉队",
                "证伪",
                "更新版本",
            ]
        ):
            continue
        theme_match = re.match(r"^(?:主题|主线)\s*\d*\s*[：:]\s*(.+)$", title)
        if theme_match:
            title = theme_match.group(1).strip()
        title = re.sub(r"（.*?）", "", title)
        title = title.strip(" -")
        if 2 <= len(title) <= 30 and not STOCK_CODE_RE.search(title):
            themes.append(title)
    return _unique(themes)


def _extract_judgement_lines(text: str, path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip("- ").strip()
        if not stripped:
            continue
        if any(word in stripped for word in JUDGEMENT_WORDS):
            rows.append({"date": path.stem, "source": str(path), "line": stripped[:220]})
    return rows


def _build_keyword_backtracks(paths: list[Path], themes: list[str]) -> list[dict[str, Any]]:
    keywords = _keywords_from_themes(themes)
    groups: list[dict[str, Any]] = []
    for keyword in keywords:
        hits: list[dict[str, Any]] = []
        for path in paths:
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = path.read_text(encoding="utf-8", errors="ignore")
            hits.extend(_keyword_hits(text, path, keyword))
        if hits:
            groups.append({"keyword": keyword, "hits": hits[:10]})
    return groups


def _keywords_from_themes(themes: list[str]) -> list[str]:
    keywords: list[str] = []
    for theme in themes:
        cleaned = _clean_keyword_source(theme)
        if not cleaned:
            continue
        keywords.append(cleaned)
        keywords.extend(re.split(r"[/／、,，;；\s]+", cleaned))
    return _unique([keyword for keyword in (_clean_keyword(item) for item in keywords) if keyword])[:30]


def _clean_keyword_source(value: str) -> str:
    text = re.sub(r"^(主题|主线)\s*\d+\s*[：:、.-]*\s*", "", value).strip()
    text = re.sub(r"[（(].*?[）)]", "", text).strip()
    if "?" in text or re.search(r"\d{4}-\d{2}-\d{2}", text):
        return ""
    return text.strip(" -")


def _clean_keyword(value: str) -> str:
    keyword = value.strip().strip(" -")
    keyword = re.sub(r"(局部)?(修复|退潮|分化|强化|观察|证伪|降级|待确认|待验证)$", "", keyword).strip()
    if not (2 <= len(keyword) <= 24):
        return ""
    if keyword in KEYWORD_STOPWORDS:
        return ""
    if keyword.isdigit():
        return ""
    return keyword


def _keyword_hits(text: str, path: Path, keyword: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if keyword not in stripped:
            continue
        is_heading = stripped.startswith("#")
        has_signal = is_heading or any(word in stripped for word in JUDGEMENT_WORDS)
        if not has_signal:
            continue
        rows.append(
            {
                "date": path.stem,
                "category": path.parent.parent.name,
                "source": str(path),
                "keyword": keyword,
                "line": stripped.strip("- ")[:220],
            }
        )
    return _dedupe_events(rows)


def _dedupe_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        key = (row["date"], row.get("keyword", ""), row["line"])
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _path_date(path: Path) -> datetime | None:
    return _parse_compact_date(path.stem)


def _parse_compact_date(value: str | None) -> datetime | None:
    if not value:
        return None
    compact = value.replace("-", "")
    if len(compact) != 8 or not compact.isdigit():
        return None
    try:
        return datetime.strptime(compact, "%Y%m%d")
    except ValueError:
        return None


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        value = value.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
