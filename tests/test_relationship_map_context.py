from __future__ import annotations

from pathlib import Path

from stock_selection.context.relationship_map_context import build_relationship_map_context


def test_parses_company_cards_from_relationship_map(tmp_path: Path):
    path = tmp_path / "stock_relationship_map" / "2026-07" / "2026-07-15.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """
# 股票关系地图 - 2026-07-15

## 主线 1：医药研发/创新药

### 公司级卡片
#### 药明康德（603259.SH）
- 所属主线：医药研发/创新药
- 所属子线：CRO/CDMO
- 主题角色：中军
- 市场状态：已异动
- 产业链位置：中游
- 主营业务：医药研发服务。

## 待扩散与未启动观察
- 待扩散公司：康龙化成 300759.SZ
- 未启动但相关公司：药易购 300937.SZ
- 证伪公司：某弱映射 000001.SZ
""",
        encoding="utf-8",
    )

    context = build_relationship_map_context(records_root=tmp_path, end_date="20260715", recent_limit=5)

    assert any(item["ts_code"] == "603259.SH" and item["subline"] == "CRO/CDMO" for item in context.companies)
    assert any(item["ts_code"] == "300759.SZ" for item in context.pending_diffusion)
    assert any(item["ts_code"] == "300937.SZ" for item in context.not_started)
    assert any(item["ts_code"] == "000001.SZ" for item in context.falsified)
    assert "医药研发/创新药" in context.theme_company_index
