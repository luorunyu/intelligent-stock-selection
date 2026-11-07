"""Use the full lending roster while retaining the latest verified quantities."""

from pathlib import Path

import pandas as pd
import pytest

from stock_selection.strategies.stock_short_high_state.stock_pool import (
    read_lending_stock_pool,
    resolve_stock_pool_file,
)


def _write(path, **overrides):
    record = {
        "trade_date": "20261026", "ts_code": "000001.SZ", "lendable_qty": "1000",
        "actual_sellable_qty": "100", "verification_status": "available",
        "queried_at": "2026-10-26T09:00:00+08:00", "verification_error": "",
    }
    record.update(overrides)
    pd.DataFrame([record]).to_csv(path, index=False)
    return path


def test_newest_partial_is_used_even_with_older_completed_alias_and_positive_only_files(tmp_path):
    _write(tmp_path / "20261024.csv", trade_date="20261024")
    expected = _write(tmp_path / ".20261026.partial.csv")
    _write(tmp_path / "latest.csv", trade_date="20261024")
    _write(tmp_path / "20261027_available.csv", trade_date="20261027")
    _write(tmp_path / "latest_available.csv", trade_date="20261027")
    _write(tmp_path / "20261028.csv", trade_date="20261028")
    assert resolve_stock_pool_file(as_of_date="2026-10-27", lending_root=tmp_path) == expected
    assert resolve_stock_pool_file(as_of_date="20261025", lending_root=tmp_path).name == "20261024.csv"


def test_completed_snapshot_wins_over_same_day_checkpoint(tmp_path):
    _write(tmp_path / ".20261026.partial.csv")
    expected = _write(tmp_path / "20261026.csv")
    assert resolve_stock_pool_file(as_of_date="20261026", lending_root=tmp_path) == expected


def test_latest_alias_used_when_no_dated_snapshot_and_date_checked(tmp_path):
    expected = _write(tmp_path / "latest.csv")
    assert resolve_stock_pool_file(as_of_date="20261026", lending_root=tmp_path) == expected
    with pytest.raises(FileNotFoundError):
        resolve_stock_pool_file(as_of_date="20261025", lending_root=tmp_path)


def test_positive_only_export_is_not_an_automatic_snapshot(tmp_path):
    _write(tmp_path / "20261026_available.csv")
    with pytest.raises(FileNotFoundError):
        resolve_stock_pool_file(as_of_date="20261026", lending_root=tmp_path)


def test_pool_includes_zero_and_failed_queries_while_errors_remain_unknown(tmp_path):
    path = _write(tmp_path / "20261026.csv")
    rows = pd.read_csv(path, dtype=str)
    data = pd.concat([rows] * 3, ignore_index=True)
    data["ts_code"] = ["000001.SZ", "000002.SZ", "000003.SZ"]
    data["actual_sellable_qty"] = ["100", "0", "999"]
    data["lendable_qty"] = ["1000", "0", "1000"]
    data["verification_status"] = ["available", "zero", "error"]
    data.to_csv(path, index=False)
    result, rule = read_lending_stock_pool(path)
    assert result["available_quantity"].tolist() == [1000, 0, 1000]
    assert result["eligible"].tolist() == [True, True, True]
    assert result.iloc[1]["actual_sellable_qty"] == 0
    assert pd.isna(result.iloc[2]["actual_sellable_qty"])
    assert "不按可融数量、实际可融数量或核验状态筛选" in rule


@pytest.mark.parametrize(("status", "quantity"), [
    ("available", "0"), ("available", ""), ("available", "--"),
    ("available", "-1"), ("available", "1.5"), ("available", "inf"),
    ("zero", "100"), ("zero", ""),
])
def test_success_status_must_have_consistent_nonnegative_integer_quantity(tmp_path, status, quantity):
    path = _write(tmp_path / "20261026.csv", verification_status=status, actual_sellable_qty=quantity)
    with pytest.raises(ValueError, match="actual_sellable_qty"):
        read_lending_stock_pool(path)


@pytest.mark.parametrize(("status", "quantity"), [("zero", "0"), ("error", "")])
def test_latest_query_overrides_earlier_positive_even_if_file_is_unsorted(tmp_path, status, quantity):
    path = _write(tmp_path / ".20261026.partial.csv")
    older = pd.read_csv(path, dtype=str, keep_default_na=False)
    newer = older.assign(
        actual_sellable_qty=quantity, verification_status=status,
        queried_at="2026-10-26T10:00:00+08:00",
    )
    pd.concat([newer, older], ignore_index=True).to_csv(path, index=False)
    result, _ = read_lending_stock_pool(path)
    assert len(result) == 1
    assert result.iloc[0]["eligible"]
    assert result.iloc[0]["verification_status"] == status
    if status == "zero":
        assert result.iloc[0]["actual_sellable_qty"] == 0
    else:
        assert pd.isna(result.iloc[0]["actual_sellable_qty"])


@pytest.mark.parametrize("quantity", [0, 100])
def test_legacy_inventory_still_supported_as_explicit_file(tmp_path, quantity):
    path = tmp_path / "20261026.csv"
    pd.DataFrame({"trade_date": ["20261026"], "ts_code": ["000001.SZ"], "lendable_qty": [quantity]}).to_csv(path, index=False)
    result, rule = read_lending_stock_pool(path)
    assert result.iloc[0]["eligible"]
    assert "actual_sellable_qty" not in result
    assert "不按可融券数量筛选" in rule


@pytest.mark.parametrize("code", ["", "invalid", "000001", "000001.XX"])
def test_full_roster_still_rejects_invalid_codes_from_pool(tmp_path, code):
    path = _write(tmp_path / "20261026.csv", ts_code=code)
    result, _ = read_lending_stock_pool(path)
    assert not result.iloc[0]["eligible"]
