"""导出华泰 Mac 客户端当前显示的可融券列表。

运行前需要在客户端中登录信用账户，并打开：
“信用委托 -> 其他查询 -> 可卖出标的券”。
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import datetime
import io
import json
from pathlib import Path
import subprocess
import sys
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = ROOT / "data_cache" / "htsc_margin_inventory"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


APPLE_SCRIPT = r'''
on run argv
    set processName to item 1 of argv
    tell application "System Events"
        if not (exists application process processName) then
            error "未找到正在运行的华泰 Mac 客户端。" number 2
        end if

        tell application process processName
            if (count of windows) is 0 then
                error "华泰 Mac 客户端没有可读取的窗口。" number 3
            end if

            set targetTable to missing value
            repeat with aScrollArea in scroll areas of window 1
                try
                    set candidateTable to table 1 of aScrollArea
                    if (count of columns of candidateTable) is 5 and (count of rows of candidateTable) > 0 then
                        set firstCode to name of UI element 1 of row 1 of candidateTable
                        set firstCodeNumber to firstCode as integer
                        if (count of characters of firstCode) is 6 and firstCodeNumber >= 0 then
                            set targetTable to candidateTable
                            exit repeat
                        end if
                    end if
                end try
            end repeat

            if targetTable is missing value then
                error "未找到可融券表格。请打开“其他查询 -> 可卖出标的券”后重试。" number 4
            end if

            set tableRows to name of every UI element of every row of targetTable
        end tell
    end tell

    set previousDelimiters to AppleScript's text item delimiters
    set AppleScript's text item delimiters to tab
    set outputText to ""
    repeat with rowCells in tableRows
        set outputText to outputText & (rowCells as text) & linefeed
    end repeat
    set AppleScript's text item delimiters to previousDelimiters
    return outputText
end run
'''


@dataclass(frozen=True)
class MarginInventoryRow:
    trade_date: str
    captured_at: str
    security_code: str
    ts_code: str
    security_name: str
    market: str
    lendable_qty: int
    note: str


def _ts_code(security_code: str, market: str) -> str:
    normalized = market.replace("Ａ", "A").replace(" ", "")
    suffixes = {
        "上海A": "SH",
        "深圳A": "SZ",
        "北京A": "BJ",
    }
    suffix = suffixes.get(normalized)
    return f"{security_code}.{suffix}" if suffix else security_code


def parse_accessibility_tsv(
    raw: str,
    *,
    trade_date: str,
    captured_at: str,
) -> list[MarginInventoryRow]:
    rows: list[MarginInventoryRow] = []
    seen_codes: set[str] = set()

    for line_number, cells in enumerate(csv.reader(io.StringIO(raw), delimiter="\t"), start=1):
        if not cells or all(not cell.strip() for cell in cells):
            continue
        if len(cells) != 5:
            raise ValueError(f"第 {line_number} 行应有 5 列，实际为 {len(cells)} 列。")

        security_code, security_name, market, quantity_text, note = (
            cell.strip() for cell in cells
        )
        if len(security_code) != 6 or not security_code.isdigit():
            raise ValueError(f"第 {line_number} 行的证券代码无效：{security_code!r}。")
        if security_code in seen_codes:
            raise ValueError(f"第 {line_number} 行出现重复证券代码：{security_code}。")

        try:
            lendable_qty = int(quantity_text.replace(",", ""))
        except ValueError as exc:
            raise ValueError(
                f"第 {line_number} 行的可融券数量无效：{quantity_text!r}。"
            ) from exc
        if lendable_qty < 0:
            raise ValueError(f"第 {line_number} 行的可融券数量不能为负数。")

        seen_codes.add(security_code)
        rows.append(
            MarginInventoryRow(
                trade_date=trade_date,
                captured_at=captured_at,
                security_code=security_code,
                ts_code=_ts_code(security_code, market),
                security_name=security_name,
                market=market,
                lendable_qty=lendable_qty,
                note=note,
            )
        )

    if not rows:
        raise ValueError("可融券表格为空，未写入文件。")
    return rows


def read_client_table(process_name: str, *, timeout: int = 90) -> str:
    try:
        result = subprocess.run(
            ["osascript", "-e", APPLE_SCRIPT, process_name],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"读取华泰客户端超时（{timeout} 秒）。") from exc

    if result.returncode != 0:
        detail = result.stderr.strip() or "未知错误"
        raise RuntimeError(f"无法读取华泰客户端：{detail}")
    return result.stdout


def _write_csv(path: Path, rows: list[MarginInventoryRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(asdict(rows[0]).keys())
    with NamedTemporaryFile(
        "w",
        encoding="utf-8-sig",
        newline="",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)
        temporary_path = Path(handle.name)
    temporary_path.replace(path)


def export_rows(
    rows: list[MarginInventoryRow],
    *,
    output_root: Path,
) -> tuple[Path, Path]:
    dated_path = output_root / f"{rows[0].trade_date}.csv"
    latest_path = output_root / "latest.csv"
    _write_csv(dated_path, rows)
    _write_csv(latest_path, rows)
    return dated_path, latest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export HTSC Mac lendable securities to CSV.")
    parser.add_argument("--process-name", default="专业版Ⅲ(MAC)")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--date", help="Output date in YYYYMMDD; defaults to today in Asia/Shanghai.")
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--dry-run", action="store_true", help="Validate data without writing CSV files.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    now = datetime.now(SHANGHAI_TZ)
    trade_date = args.date or now.strftime("%Y%m%d")
    if len(trade_date) != 8 or not trade_date.isdigit():
        print("--date 必须为 YYYYMMDD 格式。", file=sys.stderr)
        return 2

    try:
        raw = read_client_table(args.process_name, timeout=args.timeout)
        rows = parse_accessibility_tsv(
            raw,
            trade_date=trade_date,
            captured_at=now.isoformat(timespec="seconds"),
        )
        summary: dict[str, object] = {
            "trade_date": trade_date,
            "captured_at": rows[0].captured_at,
            "row_count": len(rows),
            "total_lendable_qty": sum(row.lendable_qty for row in rows),
        }
        if not args.dry_run:
            dated_path, latest_path = export_rows(rows, output_root=args.output_root)
            summary["path"] = str(dated_path)
            summary["latest_path"] = str(latest_path)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
