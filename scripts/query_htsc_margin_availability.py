"""逐只核验华泰 Mac 客户端中的实际可卖数量。

默认先读取“可卖出标的券”表格，再在左侧“融券卖出”代码框中逐只查询。
脚本不会填写价格或委托数量，也不会点击“融券卖出”按钮。
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import NamedTemporaryFile
import time
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.export_htsc_margin_inventory import (
    DEFAULT_OUTPUT_ROOT as DEFAULT_INVENTORY_ROOT,
    SHANGHAI_TZ,
    MarginInventoryRow,
    export_rows as export_inventory_rows,
    parse_accessibility_tsv,
    read_client_table,
)


DEFAULT_OUTPUT_ROOT = ROOT / "data_cache" / "htsc_margin_availability"
SUCCESS_STATUSES = {"available", "zero"}


PREFLIGHT_APPLESCRIPT = r'''
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

            set orderButtons to every UI element of window 1 whose role is "AXButton" and name is "融券卖出"
            if (count of orderButtons) is not 1 then
                error "当前不是“融券卖出”界面。" number 4
            end if

            set codeFields to every UI element of window 1 whose role is "AXTextField" and size is {138, 20}
            if (count of codeFields) is not 1 then
                error "无法唯一定位证券代码输入框。" number 5
            end if
            set codeField to item 1 of codeFields

            set compactFields to every UI element of window 1 whose role is "AXTextField" and size is {89, 20}
            if (count of compactFields) is not 2 then
                error "无法确认价格框和委托数量框，已停止。" number 6
            end if
            set quantityField to item 1 of compactFields
            set greatestY to -1
            repeat with aField in compactFields
                set fieldPosition to position of aField
                set fieldY to item 2 of fieldPosition
                if fieldY > greatestY then
                    set greatestY to fieldY
                    set quantityField to aField
                end if
            end repeat
            set quantityValue to value of quantityField
            if quantityValue is not missing value and (quantityValue as text) is not "" then
                error "委托数量框不是空的。请先清空后再运行。" number 7
            end if

            set originalCode to value of codeField
            if originalCode is missing value then return ""
            return originalCode as text
        end tell
    end tell
end run
'''


QUERY_APPLESCRIPT = r'''
on run argv
    set processName to item 1 of argv
    set securityCode to item 2 of argv
    set expectedName to item 3 of argv
    set pollCount to item 4 of argv as integer

    tell application "System Events"
        tell application process processName
            set frontmost to true
            set codeFields to every UI element of window 1 whose role is "AXTextField" and size is {138, 20}
            if (count of codeFields) is not 1 then
                error "无法唯一定位证券代码输入框。"
            end if
            set codeField to item 1 of codeFields

            set focused of codeField to true
            delay 0.1
            keystroke "a" using command down
            keystroke securityCode
            key code 48

            set loaded to false
            repeat with attempt from 1 to pollCount
                delay 0.1
                if (value of codeField as text) is securityCode then
                    if (count of (every UI element of window 1 whose name is expectedName)) > 0 then
                        set loaded to true
                        exit repeat
                    end if
                end if
            end repeat
            if loaded is false then
                error "证券信息未及时刷新：" & securityCode & " " & expectedName
            end if

            delay 0.5
            set quantityLabels to every UI element of window 1 whose name contains "可卖数量"
            if (count of quantityLabels) is not 1 then
                error "无法唯一定位可卖数量。"
            end if
            return securityCode & tab & (name of item 1 of quantityLabels as text)
        end tell
    end tell
end run
'''


RESTORE_APPLESCRIPT = r'''
on run argv
    set processName to item 1 of argv
    set securityCode to item 2 of argv
    tell application "System Events"
        if not (exists application process processName) then return
        tell application process processName
            if (count of windows) is 0 then return
            set codeFields to every UI element of window 1 whose role is "AXTextField" and size is {138, 20}
            if (count of codeFields) is not 1 then return
            set frontmost to true
            set codeField to item 1 of codeFields
            set focused of codeField to true
            delay 0.1
            keystroke "a" using command down
            keystroke securityCode
            key code 48
            delay 0.3
        end tell
    end tell
end run
'''


@dataclass(frozen=True)
class MarginAvailabilityRow:
    trade_date: str
    captured_at: str
    security_code: str
    ts_code: str
    security_name: str
    market: str
    lendable_qty: int
    note: str
    queried_at: str
    actual_sellable_qty: int | None
    verification_status: str
    verification_error: str


def parse_sellable_quantity(label: str) -> int:
    match = re.fullmatch(r"\s*可卖数量\s*[:：]\s*([\d,]+)\s*股\s*", label)
    if not match:
        raise ValueError(f"无法解析可卖数量：{label!r}。")
    quantity = int(match.group(1).replace(",", ""))
    if quantity < 0:
        raise ValueError("可卖数量不能为负数。")
    return quantity


def _run_osascript(script: str, *args: str, timeout: float) -> str:
    try:
        result = subprocess.run(
            ["osascript", "-e", script, *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"操作华泰客户端超时（{timeout:g} 秒）。") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or "未知错误"
        raise RuntimeError(detail)
    return result.stdout.strip()


def preflight_client(process_name: str) -> str:
    return _run_osascript(PREFLIGHT_APPLESCRIPT, process_name, timeout=10)


def restore_client_code(process_name: str, security_code: str) -> None:
    if len(security_code) != 6 or not security_code.isdigit():
        return
    _run_osascript(RESTORE_APPLESCRIPT, process_name, security_code, timeout=10)


def query_sellable_quantity(
    process_name: str,
    candidate: MarginInventoryRow,
    *,
    refresh_timeout: float,
) -> int:
    poll_count = max(1, round(refresh_timeout / 0.1))
    raw = _run_osascript(
        QUERY_APPLESCRIPT,
        process_name,
        candidate.security_code,
        candidate.security_name,
        str(poll_count),
        timeout=refresh_timeout + 8,
    )
    parts = raw.split("\t", maxsplit=1)
    if len(parts) != 2 or parts[0] != candidate.security_code:
        raise RuntimeError(f"客户端返回了无法识别的查询结果：{raw!r}。")
    return parse_sellable_quantity(parts[1])


def _write_csv(path: Path, rows: list[MarginAvailabilityRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(MarginAvailabilityRow.__dataclass_fields__)
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


def _append_checkpoint(path: Path, row: MarginAvailabilityRow) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not path.exists() or path.stat().st_size == 0
    encoding = "utf-8-sig" if needs_header else "utf-8"
    with path.open("a", encoding=encoding, newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(MarginAvailabilityRow.__dataclass_fields__),
        )
        if needs_header:
            writer.writeheader()
        writer.writerow(asdict(row))
        handle.flush()
        os.fsync(handle.fileno())


def _load_completed_checkpoint(path: Path) -> dict[str, MarginAvailabilityRow]:
    if not path.exists():
        return {}
    completed: dict[str, MarginAvailabilityRow] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            if raw.get("verification_status") not in SUCCESS_STATUSES:
                continue
            quantity_text = raw.get("actual_sellable_qty", "")
            if not quantity_text.isdigit():
                continue
            row = MarginAvailabilityRow(
                trade_date=raw["trade_date"],
                captured_at=raw["captured_at"],
                security_code=raw["security_code"],
                ts_code=raw["ts_code"],
                security_name=raw["security_name"],
                market=raw["market"],
                lendable_qty=int(raw["lendable_qty"]),
                note=raw["note"],
                queried_at=raw["queried_at"],
                actual_sellable_qty=int(quantity_text),
                verification_status=raw["verification_status"],
                verification_error=raw.get("verification_error", ""),
            )
            completed[row.security_code] = row
    return completed


QueryFunction = Callable[[str, MarginInventoryRow], int]


def scan_candidates(
    candidates: list[MarginInventoryRow],
    *,
    process_name: str,
    checkpoint_path: Path,
    retries: int,
    query: QueryFunction,
) -> tuple[list[MarginAvailabilityRow], list[str]]:
    completed = _load_completed_checkpoint(checkpoint_path)
    candidate_codes = {row.security_code for row in candidates}
    completed = {
        code: row
        for code, row in completed.items()
        if code in candidate_codes and row.trade_date == candidates[0].trade_date
    }
    failures: list[str] = []

    for index, candidate in enumerate(candidates, start=1):
        if candidate.security_code in completed:
            continue

        error = ""
        quantity: int | None = None
        for attempt in range(retries + 1):
            try:
                quantity = query(process_name, candidate)
                break
            except (RuntimeError, ValueError) as exc:
                error = str(exc)
                if attempt < retries:
                    time.sleep(0.5)

        queried_at = datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds")
        status = "error"
        if quantity is not None:
            status = "available" if quantity > 0 else "zero"
            error = ""
        result = MarginAvailabilityRow(
            **asdict(candidate),
            queried_at=queried_at,
            actual_sellable_qty=quantity,
            verification_status=status,
            verification_error=error,
        )
        _append_checkpoint(checkpoint_path, result)

        if status in SUCCESS_STATUSES:
            completed[candidate.security_code] = result
        else:
            failures.append(candidate.security_code)
        shown_quantity = "ERROR" if quantity is None else str(quantity)
        print(
            f"[{index}/{len(candidates)}] {candidate.security_code} "
            f"{candidate.security_name}: {shown_quantity}",
            file=sys.stderr,
            flush=True,
        )

    ordered = [completed[row.security_code] for row in candidates if row.security_code in completed]
    return ordered, failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify actual HTSC Mac sellable quantities one security at a time."
    )
    parser.add_argument("--process-name", default="专业版Ⅲ(MAC)")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--inventory-output-root", type=Path, default=DEFAULT_INVENTORY_ROOT)
    parser.add_argument("--table-timeout", type=int, default=120)
    parser.add_argument("--refresh-timeout", type=float, default=5.0)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rescan even if today's completed output already exists.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.refresh_timeout <= 0 or args.retries < 0:
        print("--refresh-timeout 必须大于 0，--retries 不能为负数。", file=sys.stderr)
        return 2

    now = datetime.now(SHANGHAI_TZ)
    trade_date = now.strftime("%Y%m%d")
    dated_path = args.output_root / f"{trade_date}.csv"
    latest_path = args.output_root / "latest.csv"
    checkpoint_path = args.output_root / f".{trade_date}.partial.csv"

    if dated_path.exists() and not args.force:
        print(
            json.dumps(
                {
                    "trade_date": trade_date,
                    "status": "already_complete",
                    "path": str(dated_path),
                    "hint": "如需重新查询，请添加 --force。",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    original_code = ""
    try:
        raw = read_client_table(args.process_name, timeout=args.table_timeout)
        candidates = parse_accessibility_tsv(
            raw,
            trade_date=trade_date,
            captured_at=now.isoformat(timespec="seconds"),
        )
        export_inventory_rows(candidates, output_root=args.inventory_output_root)
        original_code = preflight_client(args.process_name)

        def live_query(process_name: str, candidate: MarginInventoryRow) -> int:
            return query_sellable_quantity(
                process_name,
                candidate,
                refresh_timeout=args.refresh_timeout,
            )

        results, failures = scan_candidates(
            candidates,
            process_name=args.process_name,
            checkpoint_path=checkpoint_path,
            retries=args.retries,
            query=live_query,
        )
        if failures or len(results) != len(candidates):
            print(
                json.dumps(
                    {
                        "trade_date": trade_date,
                        "status": "incomplete",
                        "candidate_count": len(candidates),
                        "completed_count": len(results),
                        "failed_codes": failures,
                        "checkpoint_path": str(checkpoint_path),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                file=sys.stderr,
            )
            return 1

        _write_csv(dated_path, results)
        _write_csv(latest_path, results)
        checkpoint_path.unlink(missing_ok=True)
        available = sorted(
            (row for row in results if (row.actual_sellable_qty or 0) > 0),
            key=lambda row: row.actual_sellable_qty or 0,
            reverse=True,
        )
        available_path = args.output_root / f"{trade_date}_available.csv"
        latest_available_path = args.output_root / "latest_available.csv"
        _write_csv(available_path, available)
        _write_csv(latest_available_path, available)
        print(
            json.dumps(
                {
                    "trade_date": trade_date,
                    "status": "complete",
                    "candidate_count": len(results),
                    "actually_available_count": len(available),
                    "total_actual_sellable_qty": sum(
                        row.actual_sellable_qty or 0 for row in results
                    ),
                    "path": str(dated_path),
                    "latest_path": str(latest_path),
                    "available_path": str(available_path),
                    "latest_available_path": str(latest_available_path),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except KeyboardInterrupt:
        print("已中断；下次运行会从断点继续。", file=sys.stderr)
        return 130
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if original_code:
            try:
                restore_client_code(args.process_name, original_code)
            except RuntimeError as exc:
                print(f"未能恢复原证券代码：{exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
