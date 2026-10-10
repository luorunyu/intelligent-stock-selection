from __future__ import annotations

from pathlib import Path

import pandas as pd

from scripts.backfill_tushare_history import backfill_selected_indexes, open_trade_dates
from stock_selection.data.tushare_cache import read_dataset, write_dataset


class FakeCollector:
    def __init__(self, cache_root: Path, responses: dict[tuple[str, str], pd.DataFrame]) -> None:
        self.cache_root = cache_root
        self.responses = responses
        self.calls: list[tuple[str, dict[str, str]]] = []

    def fetch(self, api_name: str, params: dict[str, str]) -> pd.DataFrame:
        self.calls.append((api_name, params))
        key = (api_name, params.get("ts_code", ""))
        return self.responses[key].copy()


def test_open_trade_dates_sorts_calendar_values(tmp_path) -> None:
    collector = FakeCollector(
        tmp_path,
        {
            ("trade_cal", ""): pd.DataFrame(
                {"cal_date": ["20260106", "20260105", "20260106"]}
            )
        },
    )

    assert open_trade_dates(collector, "20260101", "20260110") == ["20260105", "20260106"]
    assert collector.calls[0][1]["is_open"] == "1"


def test_index_backfill_preserves_cache_and_writes_complete_missing_date(tmp_path) -> None:
    existing = pd.DataFrame({"ts_code": ["KEEP.SH"], "trade_date": ["20260105"]})
    write_dataset(existing, "index_daily_selected", "20260105", cache_root=tmp_path)
    responses = {
        ("index_daily", "A.SH"): pd.DataFrame(
            {"ts_code": ["A.SH"], "trade_date": ["20260106"], "close": [10.0]}
        ),
        ("index_daily", "B.SZ"): pd.DataFrame(
            {"ts_code": ["B.SZ"], "trade_date": ["20260106"], "close": [20.0]}
        ),
    }
    collector = FakeCollector(tmp_path, responses)

    result = backfill_selected_indexes(
        collector,
        ["20260105", "20260106"],
        index_codes=["A.SH", "B.SZ"],
    )

    assert result.written_dates == ["20260106"]
    assert result.skipped_dates == ["20260105"]
    assert result.incomplete_dates == {}
    assert read_dataset("index_daily_selected", "20260105", cache_root=tmp_path).equals(existing)
    written = read_dataset("index_daily_selected", "20260106", cache_root=tmp_path)
    assert set(written["ts_code"]) == {"A.SH", "B.SZ"}


def test_index_backfill_does_not_cache_incomplete_dates(tmp_path) -> None:
    collector = FakeCollector(
        tmp_path,
        {
            ("index_daily", "A.SH"): pd.DataFrame(
                {"ts_code": ["A.SH"], "trade_date": ["20260106"]}
            ),
            ("index_daily", "B.SZ"): pd.DataFrame(columns=["ts_code", "trade_date"]),
        },
    )

    result = backfill_selected_indexes(
        collector,
        ["20260106"],
        index_codes=["A.SH", "B.SZ"],
    )

    assert result.incomplete_dates == {"20260106": ["B.SZ"]}
    assert not (tmp_path / "index_daily_selected" / "20260106.parquet").exists()
