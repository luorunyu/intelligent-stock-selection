import pandas as pd
import pytest

from stock_selection.strategies.stock_short_high_state.screen import (
    read_cached_stock_daily,
)


@pytest.mark.parametrize(
    ("start_date", "as_of_date", "expected_dates"),
    [
        (None, None, ["20240830", "20240902", "20240903", "20240904"]),
        (None, "20240903", ["20240830", "20240902", "20240903"]),
        ("20240901", "20240903", ["20240902", "20240903"]),
        ("2024-09-02", "2024-09-03", ["20240902", "20240903"]),
        ("20240903", "20240903", ["20240903"]),
        ("20240903", None, ["20240903", "20240904"]),
    ],
)
def test_cache_reads_only_files_in_inclusive_date_range(
    tmp_path, monkeypatch, start_date, as_of_date, expected_dates,
):
    daily_dir = tmp_path / "daily"
    daily_dir.mkdir()
    for trade_date in ["20240830", "20240902", "20240903", "20240904"]:
        pd.DataFrame({
            "ts_code": ["000068.SZ"], "trade_date": [trade_date], "close": [10.0],
        }).to_parquet(daily_dir / f"{trade_date}.parquet", index=False)
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    expected_basic = pd.DataFrame({"ts_code": ["000068.SZ"], "name": ["测试股票"]})
    expected_basic.to_parquet(static_dir / "stock_basic.parquet", index=False)

    read_paths = []
    original_read = pd.read_parquet

    def record_read(path):
        read_paths.append(path)
        return original_read(path)

    monkeypatch.setattr(pd, "read_parquet", record_read)
    daily, basic = read_cached_stock_daily(
        cache_root=tmp_path, start_date=start_date, as_of_date=as_of_date,
    )

    assert daily["trade_date"].tolist() == expected_dates
    assert [path.stem for path in read_paths if path.parent == daily_dir] == expected_dates
    pd.testing.assert_frame_equal(basic, expected_basic)


@pytest.mark.parametrize(
    ("bounds", "message"),
    [
        ({"start_date": "bad-date"}, "start_date must be a valid date"),
        ({"start_date": "20240230"}, "start_date must be a valid date"),
        ({"as_of_date": "20241301"}, "as_of_date must be a valid date"),
        (
            {"start_date": "20240903", "as_of_date": "20240902"},
            "start_date must be on or before as_of_date",
        ),
    ],
)
def test_cache_rejects_invalid_date_bounds_before_reading(tmp_path, bounds, message):
    with pytest.raises(ValueError, match=message):
        read_cached_stock_daily(cache_root=tmp_path, **bounds)


def test_cache_reports_missing_date_range(tmp_path):
    with pytest.raises(FileNotFoundError, match="date range 20240901 to 20240903"):
        read_cached_stock_daily(
            cache_root=tmp_path, start_date="20240901", as_of_date="20240903",
        )
