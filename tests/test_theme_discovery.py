from __future__ import annotations

from pathlib import Path

import pandas as pd

from stock_selection.context.theme_discovery import discover_active_themes


def _write_dataset(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)


def test_discovers_cross_industry_switch_theme(tmp_path: Path):
    dates = ["20260629", "20260630", "20260701", "20260702", "20260703", "20260706"]
    stock_rows = [
        {"ts_code": "000938.SZ", "name": "紫光股份", "industry": "IT设备", "market": "主板", "list_date": "19991104"},
        {"ts_code": "301191.SZ", "name": "菲菱科思", "industry": "通信设备", "market": "创业板", "list_date": "20220526"},
        {"ts_code": "002396.SZ", "name": "星网锐捷", "industry": "通信设备", "market": "主板", "list_date": "20100623"},
        {"ts_code": "301165.SZ", "name": "锐捷网络", "industry": "通信设备", "market": "创业板", "list_date": "20221121"},
        {"ts_code": "688629.SH", "name": "华丰科技", "industry": "元器件", "market": "科创板", "list_date": "20230627"},
        {"ts_code": "600000.SH", "name": "浦发银行", "industry": "银行", "market": "主板", "list_date": "19991110"},
    ]
    pct_by_date = {
        "20260629": [2.42, -3.32, -1.53, 5.57, 18.08, 0.1],
        "20260630": [9.98, 17.06, 9.98, 20.00, 9.65, -0.2],
        "20260701": [3.71, 3.04, 10.02, 6.80, -3.71, 0.3],
        "20260702": [-3.01, -4.41, 0.85, -0.78, -0.64, -0.4],
        "20260703": [4.31, 0.60, 10.01, 11.31, 0.33, 0.5],
        "20260706": [10.01, 20.00, 10.00, 7.49, 11.81, -0.1],
    }
    amount_by_code = {
        "000938.SZ": 9_933_778.0,
        "301191.SZ": 1_699_984.0,
        "002396.SZ": 1_155_247.0,
        "301165.SZ": 4_678_950.0,
        "688629.SH": 6_678_793.0,
        "600000.SH": 500_000.0,
    }
    for date in dates:
        daily_rows = []
        basic_rows = []
        for stock, pct in zip(stock_rows, pct_by_date[date]):
            daily_rows.append(
                {
                    "ts_code": stock["ts_code"],
                    "trade_date": date,
                    "close": 10.0,
                    "pct_chg": pct,
                    "amount": amount_by_code[stock["ts_code"]],
                }
            )
            basic_rows.append(
                {
                    "ts_code": stock["ts_code"],
                    "trade_date": date,
                    "turnover_rate": 10.0,
                    "volume_ratio": 1.0,
                }
            )
        _write_dataset(tmp_path / "daily" / f"{date}.parquet", daily_rows)
        _write_dataset(tmp_path / "daily_basic" / f"{date}.parquet", basic_rows)
        _write_dataset(tmp_path / "stock_basic" / f"{date}.parquet", stock_rows)

    result = discover_active_themes("20260706", cache_root=tmp_path, records_root=tmp_path, write=True)
    themes = {item["theme"]: item for item in result.themes}

    assert "AI集群网络/交换机" in themes
    switch_theme = themes["AI集群网络/交换机"]
    assert switch_theme["cross_industry"] is True
    assert set(switch_theme["industries"]) >= {"通信设备", "IT设备", "元器件"}
    assert switch_theme["matched_count"] >= 5
    assert switch_theme["status"] in {"强势确认", "已有预热后加强"}
    assert result.output_path
    assert Path(result.output_path).exists()


def test_discovers_unseeded_pharma_theme(tmp_path: Path):
    dates = ["20260708", "20260709", "20260710", "20260713", "20260714", "20260715"]
    stocks = [
        {"ts_code": "688192.SH", "name": "迪哲医药", "industry": "化学制药", "market": "科创板", "list_date": "20211210"},
        {"ts_code": "603259.SH", "name": "药明康德", "industry": "医疗保健", "market": "主板", "list_date": "20180508"},
        {"ts_code": "300759.SZ", "name": "康龙化成", "industry": "医疗保健", "market": "创业板", "list_date": "20190128"},
        {"ts_code": "300558.SZ", "name": "贝达药业", "industry": "化学制药", "market": "创业板", "list_date": "20161107"},
        {"ts_code": "300502.SZ", "name": "新易盛", "industry": "通信设备", "market": "创业板", "list_date": "20160303"},
        {"ts_code": "002463.SZ", "name": "沪电股份", "industry": "元器件", "market": "主板", "list_date": "20100818"},
    ]
    pct_by_date = {
        "20260708": [0.5, 0.2, 0.3, -0.2, 1.0, 0.8],
        "20260709": [1.0, 0.8, 0.6, 0.4, 0.5, 0.6],
        "20260710": [2.0, 1.2, 1.0, 0.9, -0.5, -0.3],
        "20260713": [3.0, 2.2, 1.8, 1.5, 0.2, 0.1],
        "20260714": [4.5, 3.5, 3.0, 2.8, -1.0, -0.8],
        "20260715": [20.0, 6.0, 8.0, 8.2, 0.6, 0.4],
    }
    amount_by_code = {
        "688192.SH": 3_000_000.0,
        "603259.SH": 9_000_000.0,
        "300759.SZ": 4_000_000.0,
        "300558.SZ": 2_000_000.0,
        "300502.SZ": 1_500_000.0,
        "002463.SZ": 1_200_000.0,
    }
    for date in dates:
        daily_rows = []
        basic_rows = []
        for stock, pct in zip(stocks, pct_by_date[date]):
            daily_rows.append(
                {
                    "ts_code": stock["ts_code"],
                    "trade_date": date,
                    "close": 10.0,
                    "pct_chg": pct,
                    "amount": amount_by_code[stock["ts_code"]],
                }
            )
            basic_rows.append(
                {
                    "ts_code": stock["ts_code"],
                    "trade_date": date,
                    "turnover_rate": 9.0,
                    "volume_ratio": 1.5,
                }
            )
        _write_dataset(tmp_path / "daily" / f"{date}.parquet", daily_rows)
        _write_dataset(tmp_path / "daily_basic" / f"{date}.parquet", basic_rows)
        _write_dataset(tmp_path / "stock_basic" / f"{date}.parquet", stocks)

    result = discover_active_themes("20260715", cache_root=tmp_path, records_root=tmp_path, write=True)

    assert any("医药" in item["theme"] or "制药" in item["theme"] for item in result.unseeded_themes)
    assert any("医药" in item["theme"] or "制药" in item["theme"] for item in result.market_hotspots)
    assert isinstance(result.relationship_map_context, dict)
    assert isinstance(result.theme_stock_roles, list)
    assert isinstance(result.map_related_candidates, list)
    assert isinstance(result.theme_rotation_context, dict)


def test_seeded_theme_does_not_override_stronger_unseeded_theme(tmp_path: Path):
    dates = ["20260708", "20260709", "20260710", "20260713", "20260714", "20260715"]
    stocks = [
        {"ts_code": "688192.SH", "name": "迪哲医药", "industry": "化学制药", "market": "科创板", "list_date": "20211210"},
        {"ts_code": "603259.SH", "name": "药明康德", "industry": "医疗保健", "market": "主板", "list_date": "20180508"},
        {"ts_code": "300759.SZ", "name": "康龙化成", "industry": "医疗保健", "market": "创业板", "list_date": "20190128"},
        {"ts_code": "300558.SZ", "name": "贝达药业", "industry": "化学制药", "market": "创业板", "list_date": "20161107"},
        {"ts_code": "300502.SZ", "name": "新易盛", "industry": "通信设备", "market": "创业板", "list_date": "20160303"},
        {"ts_code": "300308.SZ", "name": "中际旭创", "industry": "通信设备", "market": "创业板", "list_date": "20121010"},
        {"ts_code": "002463.SZ", "name": "沪电股份", "industry": "元器件", "market": "主板", "list_date": "20100818"},
    ]
    for date in dates:
        daily_rows = []
        basic_rows = []
        for stock in stocks:
            is_pharma = stock["industry"] in {"化学制药", "医疗保健"}
            pct = 7.0 if is_pharma and date == "20260715" else (1.0 if is_pharma else 0.4)
            amount = 4_500_000.0 if is_pharma else 800_000.0
            daily_rows.append(
                {
                    "ts_code": stock["ts_code"],
                    "trade_date": date,
                    "close": 10.0,
                    "pct_chg": pct,
                    "amount": amount,
                }
            )
            basic_rows.append(
                {
                    "ts_code": stock["ts_code"],
                    "trade_date": date,
                    "turnover_rate": 8.0 if is_pharma else 2.0,
                    "volume_ratio": 1.5,
                }
            )
        _write_dataset(tmp_path / "daily" / f"{date}.parquet", daily_rows)
        _write_dataset(tmp_path / "daily_basic" / f"{date}.parquet", basic_rows)
        _write_dataset(tmp_path / "stock_basic" / f"{date}.parquet", stocks)

    result = discover_active_themes("20260715", cache_root=tmp_path, records_root=tmp_path, write=True)

    assert result.market_hotspots
    assert "医药" in result.market_hotspots[0]["theme"] or "制药" in result.market_hotspots[0]["theme"]
    assert result.market_hotspots[0]["seed_based"] is False
