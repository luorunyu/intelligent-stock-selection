from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_selection.data.after_close import build_after_close_report_context, prepare_after_close_data
from stock_selection.context.theme_discovery import discover_active_themes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare formal after-close cache and emit report-generation context."
    )
    parser.add_argument("--date", default=None, help="Requested date in YYYYMMDD or YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--cache-root", default=None, help="Override formal Tushare cache root.")
    parser.add_argument("--records-root", default="analysis_records", help="Analysis record root.")
    parser.add_argument("--recent", type=int, default=5, help="Number of recent records to list.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing cached datasets.")
    parser.add_argument("--lookback-days", type=int, default=10)
    parser.add_argument("--available-only", action="store_true", help="Respect available_apis.json filtering.")
    parser.add_argument("--required-retries", type=int, default=3, help="Retries for required daily APIs before fallback.")
    parser.add_argument(
        "--required-retry-wait-seconds",
        type=float,
        default=60.0,
        help="Seconds to wait between required daily API retries.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    requested_date = _compact_date(args.date or date.today().strftime("%Y%m%d"))
    records_root = Path(args.records_root)
    manifest = prepare_after_close_data(
        requested_date,
        cache_root=args.cache_root,
        force=args.force,
        lookback_days=args.lookback_days,
        available_only=args.available_only,
        required_retries=args.required_retries,
        required_retry_wait_seconds=args.required_retry_wait_seconds,
    )
    context = build_after_close_report_context(manifest, cache_root=args.cache_root)
    report_date = context.report_date
    hotspot_discovery = _theme_discovery_payload(
        context.trade_date,
        cache_root=args.cache_root,
        records_root=records_root,
    )

    payload = {
        "mode": "after_close_formal_cache_context",
        "rules": [
            "Use this context as the report-generation input after formal Tushare cache preparation.",
            "Read structured data only from data_cache/tushare formal cache paths listed here.",
            "Do not write or depend on analysis_records/_tmp_automation_* for daily production.",
            "If requested_date differs from trade_date, state the fallback explicitly in the report.",
            "Review any pre-open overseas mapping against A-share close data: confirm, partially confirm, leave unconfirmed, or falsify with market evidence.",
            "Do not generate buy/sell/hold, position, or target-price language.",
        ],
        "after_close": asdict(context),
        "hotspot_discovery": hotspot_discovery,
        "theme_discovery": hotspot_discovery,
        "relationship_map_context": hotspot_discovery.get("relationship_map_context", {}),
        "stock_role_context": {"theme_stock_roles": hotspot_discovery.get("theme_stock_roles", [])},
        "theme_rotation_context": hotspot_discovery.get("theme_rotation_context", {}),
        "recent_records": {
            "sector_analysis": _recent_records(records_root / "sector_analysis", args.recent),
            "stock_selection": _recent_records(records_root / "stock_selection", args.recent),
            "stock_relationship_map": _recent_records(records_root / "stock_relationship_map", args.recent),
        },
        "output": {
            "stock_selection_report_path": str(
                records_root / "stock_selection" / report_date[:7] / f"{report_date}.md"
            ),
            "stock_relationship_map_path": str(
                records_root / "stock_relationship_map" / report_date[:7] / f"{report_date}.md"
            ),
            "append_if_exists": True,
            "stock_selection_email_subject": f"A股收盘后观察池复盘 {report_date}",
            "relationship_map_email_subject": f"A股股票关系地图 {report_date}",
        },
        "information_gaps": _information_gaps(context),
    }

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _compact_date(value: str) -> str:
    compact = value.replace("-", "")
    if len(compact) != 8 or not compact.isdigit():
        raise ValueError(f"invalid date: {value}")
    return compact


def _recent_records(root: Path, limit: int) -> list[str]:
    if not root.exists():
        return []
    files = sorted(root.glob("*/*.md"), key=lambda path: path.stat().st_mtime, reverse=True)
    return [str(path) for path in files[:limit]]


def _information_gaps(context) -> list[str]:
    gaps: list[str] = []
    if context.missing_datasets:
        gaps.append("Missing formal cache datasets: " + ", ".join(context.missing_datasets))
    readiness = context.readiness or {}
    if readiness.get("fallback_reason"):
        gaps.append("Data readiness fallback reason: " + str(readiness["fallback_reason"]))
    if not context.data_ready_for_requested_date:
        gaps.append(
            f"Requested date {context.requested_date} is not fully ready; "
            f"using latest complete trade date {context.trade_date}."
        )
    return gaps


def _theme_discovery_payload(trade_date: str, *, cache_root: str | None, records_root: Path) -> dict:
    try:
        result = discover_active_themes(
            trade_date,
            cache_root=cache_root,
            records_root=records_root,
            write=True,
        )
        payload = result.to_dict()
        payload["active_pool"] = payload.get("active_pool", [])[:60]
        payload["market_hotspots"] = payload.get("market_hotspots", [])[:20]
        payload["industry_hotspots"] = payload.get("industry_hotspots", [])[:20]
        payload["unseeded_themes"] = payload.get("unseeded_themes", [])[:20]
        payload["seeded_theme_matches"] = payload.get("seeded_theme_matches", [])[:20]
        payload["theme_lifecycle"] = payload.get("theme_lifecycle", [])[:20]
        payload["theme_stock_roles"] = payload.get("theme_stock_roles", [])[:8]
        payload["map_related_candidates"] = payload.get("map_related_candidates", [])[:80]
        if payload.get("relationship_map_context"):
            payload["relationship_map_context"]["companies"] = payload["relationship_map_context"].get("companies", [])[:80]
            payload["relationship_map_context"]["pending_diffusion"] = payload["relationship_map_context"].get("pending_diffusion", [])[:40]
            payload["relationship_map_context"]["not_started"] = payload["relationship_map_context"].get("not_started", [])[:40]
        if payload.get("theme_rotation_context"):
            payload["theme_rotation_context"]["theme_timeline"] = payload["theme_rotation_context"].get("theme_timeline", [])[-80:]
            payload["theme_rotation_context"]["theme_transitions"] = payload["theme_rotation_context"].get("theme_transitions", [])[:40]
        payload["prompt_hint"] = (
            "Use hotspot_discovery.market_hotspots and hotspot_discovery.unseeded_themes first "
            "to identify today's active themes. Then use relationship_map_context to complete related "
            "companies, value-chain nodes, pending diffusion, not-started, downgraded, and falsified names. "
            "Use theme_stock_roles for leader/middle-army/diffuser/follower/laggard/falsified roles, and "
            "theme_rotation_context for old-theme retreat, repair, return, and cross-theme rotation. Treat "
            "seeded_theme_matches as known-theme validation only."
        )
        return payload
    except Exception as exc:
        return {
            "trade_date": trade_date,
            "market_hotspots": [],
            "industry_hotspots": [],
            "unseeded_themes": [],
            "seeded_theme_matches": [],
            "theme_lifecycle": [],
            "relationship_map_context": {},
            "theme_stock_roles": [],
            "map_related_candidates": [],
            "theme_rotation_context": {},
            "themes": [],
            "tag_clusters": [],
            "active_pool": [],
            "error": str(exc),
            "prompt_hint": "Hotspot discovery failed; continue with standard report context and explicitly mark the gap.",
        }


if __name__ == "__main__":
    raise SystemExit(main())
