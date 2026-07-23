from __future__ import annotations

from stock_selection.context.theme_stock_roles import classify_hotspot_stock_roles


def test_classifies_diffuser_from_pending_map_node():
    hotspot = {
        "theme": "医药链活跃",
        "source_type": "unseeded_family_cluster",
        "latest_avg_pct": 3.0,
        "stocks": [{"ts_code": "300759.SZ", "name": "康龙化成", "pct_chg": 8.0, "amount_yi": 40.0}],
    }
    active_pool = [
        {"ts_code": "300759.SZ", "name": "康龙化成", "pct_chg": 8.0, "amount_yi": 40.0, "turnover_rate": 11.0, "limit_up_hit": False}
    ]
    map_context = {
        "companies": [
            {
                "ts_code": "300759.SZ",
                "name": "康龙化成",
                "theme": "医药链",
                "subline": "CRO/CDMO",
                "chain": "中游",
                "relationship_strength": "相关",
                "market_status": "待扩散",
                "role_in_map": "待验证",
            }
        ]
    }

    result = classify_hotspot_stock_roles(hotspot, active_pool=active_pool, relationship_map_context=map_context)

    assert result.roles["diffusers"]
    assert result.roles["diffusers"][0]["ts_code"] == "300759.SZ"


def test_marks_falsified_when_map_status_is_falsified():
    hotspot = {"theme": "AI硬件链活跃", "source_type": "seeded_theme_match", "latest_avg_pct": 2.0, "stocks": [{"ts_code": "300308.SZ"}]}
    active_pool = [{"ts_code": "300308.SZ", "name": "中际旭创", "pct_chg": 5.0, "amount_yi": 80.0, "turnover_rate": 8.0}]
    map_context = {
        "companies": [
            {
                "ts_code": "300308.SZ",
                "name": "中际旭创",
                "theme": "CPO光通信",
                "subline": "光模块",
                "chain": "中游",
                "relationship_strength": "证伪",
                "market_status": "证伪",
                "role_in_map": "掉队",
            }
        ]
    }

    result = classify_hotspot_stock_roles(hotspot, active_pool=active_pool, relationship_map_context=map_context)

    assert result.roles["falsified"]
    assert result.roles["falsified"][0]["ts_code"] == "300308.SZ"
