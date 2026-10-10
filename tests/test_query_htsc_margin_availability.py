from __future__ import annotations

from pathlib import Path

import pytest

from scripts.export_htsc_margin_inventory import MarginInventoryRow
from scripts.query_htsc_margin_availability import (
    MarginAvailabilityRow,
    _append_checkpoint,
    parse_sellable_quantity,
    scan_candidates,
)


def _candidate(code: str, name: str) -> MarginInventoryRow:
    return MarginInventoryRow(
        trade_date="20261009",
        captured_at="2026-10-09T09:30:00+08:00",
        security_code=code,
        ts_code=f"{code}.SH",
        security_name=name,
        market="上海Ａ",
        lendable_qty=100,
        note="--",
    )


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("可卖数量:0股", 0),
        ("可卖数量：497股", 497),
        (" 可卖数量: 1,234 股 ", 1234),
    ],
)
def test_parse_sellable_quantity(label: str, expected: int) -> None:
    assert parse_sellable_quantity(label) == expected


def test_parse_sellable_quantity_rejects_unexpected_label() -> None:
    with pytest.raises(ValueError, match="无法解析"):
        parse_sellable_quantity("可用余额:100股")


def test_scan_candidates_resumes_completed_checkpoint(tmp_path: Path) -> None:
    first = _candidate("600004", "白云机场")
    second = _candidate("600157", "永泰能源")
    checkpoint = tmp_path / ".20261009.partial.csv"
    _append_checkpoint(
        checkpoint,
        MarginAvailabilityRow(
            **first.__dict__,
            queried_at="2026-10-09T09:31:00+08:00",
            actual_sellable_qty=0,
            verification_status="zero",
            verification_error="",
        ),
    )
    queried: list[str] = []

    def query(_process_name: str, candidate: MarginInventoryRow) -> int:
        queried.append(candidate.security_code)
        return 20

    rows, failures = scan_candidates(
        [first, second],
        process_name="test",
        checkpoint_path=checkpoint,
        retries=0,
        query=query,
    )

    assert queried == ["600157"]
    assert failures == []
    assert [row.security_code for row in rows] == ["600004", "600157"]
    assert [row.actual_sellable_qty for row in rows] == [0, 20]


def test_scan_candidates_retries_and_records_failure(tmp_path: Path) -> None:
    candidate = _candidate("600004", "白云机场")
    attempts = 0

    def query(_process_name: str, _candidate: MarginInventoryRow) -> int:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("temporary failure")

    rows, failures = scan_candidates(
        [candidate],
        process_name="test",
        checkpoint_path=tmp_path / ".partial.csv",
        retries=1,
        query=query,
    )

    assert attempts == 2
    assert rows == []
    assert failures == ["600004"]
