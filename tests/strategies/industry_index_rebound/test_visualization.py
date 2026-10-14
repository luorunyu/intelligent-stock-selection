import json
from pathlib import Path

import pandas as pd
import pytest


pytest.importorskip("plotly")

from stock_selection.strategies.industry_index_rebound.visualization import (
    build_industry_rebound_figure,
    build_stock_search_records,
    load_industry_catalog,
    write_industry_chart_index,
)


def _bars() -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=8, freq="D")
    return pd.DataFrame(
        {
            "ts_code": ["801010.SI"] * 8,
            "name": ["农林牧渔"] * 8,
            "trade_date": dates.strftime("%Y%m%d"),
            "open": [100, 99, 97, 96, 98, 99, 101, 103],
            "high": [101, 100, 98, 98, 100, 102, 104, 105],
            "low": [99, 97, 95, 95, 97, 98, 100, 102],
            "close": [100, 98, 96, 97, 99, 101, 103, 104],
            "vol": [10, 12, 11, 9, 13, 15, 14, 16],
            "pct_change": [0, -2, -2.0408, 1.0417, 2.0619, 2.0202, 1.9802, 0.9709],
        }
    )


def _dictionary(path: Path) -> None:
    payload = {
        "level1": {
            "801010.SI": {
                "name": "农林牧渔",
                "children": {
                    "801016.SI": {
                        "name": "种植业",
                        "children": {
                            "850111.SI": {
                                "name": "种子",
                                "stock_codes": ["000998.SZ", "000713.SZ"],
                            },
                            "850112.SI": {
                                "name": "粮食种植",
                                "stock_codes": ["000998.SZ"],
                            },
                        },
                    }
                },
            }
        }
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_catalog_keeps_all_levels_and_multiple_stock_paths(tmp_path: Path):
    path = tmp_path / "dictionary.json"
    _dictionary(path)

    catalog, stock_paths = load_industry_catalog(path)

    assert [node["level"] for node in catalog] == ["L1", "L2", "L3", "L3"]
    assert [item["name"] for item in stock_paths["000713.SZ"][0]] == [
        "农林牧渔",
        "种植业",
        "种子",
    ]
    assert len(stock_paths["000998.SZ"]) == 2


def test_builds_kline_with_window_low_and_latest_markers():
    figure = build_industry_rebound_figure(
        _bars(),
        index_code="801010.SI",
        index_name="农林牧渔",
        level="L1",
        window=8,
    )

    assert len(figure.data) == 4
    assert len(figure.layout.shapes) == 2
    assert "反弹" in figure.layout.annotations[0].text


def test_static_index_contains_stock_search_and_all_levels(tmp_path: Path):
    dictionary = tmp_path / "dictionary.json"
    _dictionary(dictionary)
    catalog, stock_paths = load_industry_catalog(dictionary)
    basic = pd.DataFrame(
        [{"ts_code": "000998.SZ", "symbol": "000998", "name": "隆平高科"}]
    )
    records = build_stock_search_records(
        basic,
        stock_paths,
        {"801010.SI", "801016.SI", "850111.SI"},
    )

    path = write_industry_chart_index(
        tmp_path,
        catalog=catalog,
        stock_records=records,
        available_codes={"801010.SI", "801016.SI", "850111.SI"},
        metric_by_code={},
        as_of_date="20261009",
        window=20,
    )
    html = path.read_text(encoding="utf-8")

    assert "隆平高科" in html
    assert "L1 一级行业" in html
    assert "L2 二级行业" in html
    assert "L3 三级行业" in html
    assert "L3_850111_SI.html" in html
    assert "无缓存行情" in html
