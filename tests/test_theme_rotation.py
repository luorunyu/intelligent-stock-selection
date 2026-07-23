from __future__ import annotations

import json
from pathlib import Path

from stock_selection.context.theme_rotation import build_theme_rotation_context


def _write_theme_record(root: Path, date: str, hotspots: list[dict]) -> None:
    dashed = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    path = root / "theme_discovery" / dashed[:7] / f"{dashed}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"trade_date": date, "market_hotspots": hotspots}, ensure_ascii=False), encoding="utf-8")


def test_builds_rotation_edge_from_fading_to_rising_theme(tmp_path: Path):
    _write_theme_record(
        tmp_path,
        "20260714",
        [
            {"theme": "AI硬件链", "score": 24.0, "status": "强势扩散", "latest_avg_pct": 3.0, "latest_up_ratio": 0.8},
            {"theme": "医药链", "score": 6.0, "status": "待验证", "latest_avg_pct": 0.5, "latest_up_ratio": 0.5},
        ],
    )
    _write_theme_record(
        tmp_path,
        "20260715",
        [
            {"theme": "医药链", "score": 28.0, "status": "强势扩散", "latest_avg_pct": 4.0, "latest_up_ratio": 0.9},
            {"theme": "AI硬件链", "score": 8.0, "status": "退潮", "latest_avg_pct": -1.0, "latest_up_ratio": 0.3},
        ],
    )

    context = build_theme_rotation_context(end_date="20260715", records_root=tmp_path, lookback=10)

    assert any(item["theme"] == "医药链" and item["transition"] == "strengthening" for item in context.theme_transitions)
    assert any(item["theme"] == "AI硬件链" and item["transition"] == "retreating" for item in context.theme_transitions)
    assert any(edge["from_theme"] == "AI硬件链" and edge["to_theme"] == "医药链" for edge in context.rotation_edges)
