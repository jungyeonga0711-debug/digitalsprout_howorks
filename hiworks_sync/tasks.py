from __future__ import annotations

from typing import Any

from .google_sheets import SheetsClient, spreadsheet_id_from_url
from .models import ApprovalTask


def selected_tasks(settings: dict) -> list[ApprovalTask]:
    spreadsheet_settings = settings["spreadsheet"]
    spreadsheet_id = spreadsheet_id_from_url(spreadsheet_settings["url"])
    summary_sheet_name = spreadsheet_settings.get("summary_sheet_name", "하이웍스_종합")

    client = SheetsClient()
    values = client.read_values(spreadsheet_id, f"'{summary_sheet_name}'!A1:AZ10000")
    if not values:
        return []

    headers = [str(value) for value in values[0]]
    tasks: list[ApprovalTask] = []

    for index, row in enumerate(values[1:], start=2):
        record = {header: _cell(row, position) for position, header in enumerate(headers)}
        key = record.get("내부 키") or record.get("키", "")
        sheet_code = record.get("내부 비목코드") or record.get("비목코드", "")
        source_row = record.get("내부 원본행") or record.get("원본행", "")

        if _checked(record.get("품의 업로드")):
            amount = record.get("품의 합계금액", "")
            if _has_payload(record.get("품의 거래처"), amount, record.get("품의 하이웍스 제목")):
                tasks.append(
                    ApprovalTask(
                        key=key,
                        kind="procurement",
                        row_number=index,
                        payload={
                            "sheet_code": sheet_code,
                            "sheet_name": record.get("비목시트", ""),
                            "source_row": source_row,
                            "date": record.get("품의 기안일자", ""),
                            "vendor": record.get("품의 거래처", ""),
                            "amount": amount,
                            "payment_method": record.get("품의 결제", ""),
                            "evidence": record.get("품의 증빙번호", ""),
                            "code": record.get("품의 코드", ""),
                            "number": record.get("품의 번호", ""),
                            "school": record.get("품의 수혜기관명", ""),
                            "title": record.get("품의 하이웍스 제목", ""),
                            "note": record.get("품의 비고", ""),
                            "remaining_budget": record.get("품의 잔여예산", ""),
                            "total_budget": _budget(settings),
                            "estimate_number": "260515-ED-03",
                        },
                    )
                )

        if _checked(record.get("집행 업로드")):
            amount = record.get("집행 합계금액", "")
            if _has_payload(record.get("집행 거래처"), amount, record.get("집행 하이웍스 제목")):
                tasks.append(
                    ApprovalTask(
                        key=key,
                        kind="execution",
                        row_number=index,
                        payload={
                            "sheet_code": sheet_code,
                            "sheet_name": record.get("비목시트", ""),
                            "source_row": source_row,
                            "date": record.get("집행 기안일자", ""),
                            "vendor": record.get("집행 거래처", ""),
                            "supply_amount": record.get("집행 공급가액", ""),
                            "vat": record.get("집행 부가세", ""),
                            "amount": amount,
                            "usage": record.get("내부 집행 사용내역") or record.get("집행 사용내역", ""),
                            "payment_method": record.get("집행 결제", ""),
                            "evidence": record.get("집행 증빙번호", ""),
                            "code": record.get("집행 코드", ""),
                            "number": record.get("집행 번호", ""),
                            "school": record.get("집행 수혜기관명", "") or record.get("집행 학교명", ""),
                            "title": record.get("집행 하이웍스 제목", ""),
                            "remaining_budget": record.get("집행 잔여예산", ""),
                            "total_budget": _budget(settings),
                            "estimate_number": "260515-ED-03",
                        },
                    )
                )
    return tasks


def mark_task(settings: dict, task: ApprovalTask, status: str, url: str = "", error: str = "") -> None:
    spreadsheet_settings = settings["spreadsheet"]
    spreadsheet_id = spreadsheet_id_from_url(spreadsheet_settings["url"])
    summary_sheet_name = spreadsheet_settings.get("summary_sheet_name", "하이웍스_종합")
    client = SheetsClient()
    client.update_status(
        spreadsheet_id,
        summary_sheet_name,
        task.row_number,
        task.kind,
        status,
        url,
        error,
    )
    if status == "기안완료" and url:
        source_row = int(task.payload.get("source_row") or 0)
        client.update_source_url(
            spreadsheet_id,
            task.payload.get("sheet_name", ""),
            source_row,
            task.kind,
            url,
        )
        client.update_source_marker(
            spreadsheet_id,
            task.payload.get("sheet_name", ""),
            source_row,
            task.kind,
            "◎",
        )


def _cell(row: list[Any], index: int) -> str:
    if index >= len(row) or row[index] is None:
        return ""
    return str(row[index]).strip()


def _checked(value: str | None) -> bool:
    return str(value or "").strip() in {"✓", "○"}


def _has_payload(vendor: str | None, amount: str | None, title: str | None) -> bool:
    return bool((title or "").strip() and ((vendor or "").strip() or (amount or "").strip()))


def _budget(settings: dict) -> str:
    total = int(settings.get("budget", {}).get("total", 240_000_000))
    return f"{total:,}"
