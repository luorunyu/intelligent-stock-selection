from __future__ import annotations

import csv
from pathlib import Path

import pytest

from scripts.export_htsc_margin_inventory import export_rows, parse_accessibility_tsv


CAPTURED_AT = "2026-10-09T09:31:00+08:00"


def test_parse_accessibility_tsv_maps_markets_and_quantities() -> None:
    raw = (
        "510500\t中证500ETF南方\t上海Ａ\t1,662,900\t--\n"
        "000725\t京东方Ａ\t深圳Ａ\t189100\t--\n"
    )

    rows = parse_accessibility_tsv(
        raw,
        trade_date="20261009",
        captured_at=CAPTURED_AT,
    )

    assert [row.ts_code for row in rows] == ["510500.SH", "000725.SZ"]
    assert [row.lendable_qty for row in rows] == [1_662_900, 189_100]
    assert rows[0].captured_at == CAPTURED_AT


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("600000\t浦发银行\t上海Ａ\t100\n", "5 列"),
        ("60000X\t浦发银行\t上海Ａ\t100\t--\n", "证券代码无效"),
        ("600000\t浦发银行\t上海Ａ\tN/A\t--\n", "可融券数量无效"),
    ],
)
def test_parse_accessibility_tsv_rejects_invalid_rows(raw: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_accessibility_tsv(
            raw,
            trade_date="20261009",
            captured_at=CAPTURED_AT,
        )


def test_export_rows_writes_dated_and_latest_csv(tmp_path: Path) -> None:
    rows = parse_accessibility_tsv(
        "600157\t永泰能源\t上海Ａ\t436128\t--\n",
        trade_date="20261009",
        captured_at=CAPTURED_AT,
    )

    dated_path, latest_path = export_rows(rows, output_root=tmp_path)

    assert dated_path == tmp_path / "20261009.csv"
    assert latest_path == tmp_path / "latest.csv"
    with dated_path.open(encoding="utf-8-sig", newline="") as handle:
        written = list(csv.DictReader(handle))
    assert written[0]["ts_code"] == "600157.SH"
    assert written[0]["lendable_qty"] == "436128"
