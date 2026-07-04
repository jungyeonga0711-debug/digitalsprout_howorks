from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .models import SourceRow


PROCUREMENT_HEADERS = {
    "구분": "selected",
    "제목생성": "selected",
    "순번": "row_no",
    "기안일자": "date",
    "거래처": "vendor",
    "합계금액": "amount",
    "결제": "payment_method",
    "비고\n(증빙번호)": "evidence",
    "코드": "code",
    "번호": "number",
    "수혜기관명": "school",
    "하이웍스 제목": "hiworks_title",
    "하이웍스 URL": "source_url",
    "비고": "note",
}

EXECUTION_HEADERS = {
    "구분": "selected",
    "제목생성": "selected",
    "순번": "row_no",
    "기안일자": "date",
    "거래처": "vendor",
    "공급가액": "supply_amount",
    "부가세": "vat",
    "합계금액": "amount",
    "사용내역(수령인)": "usage",
    "결제": "payment_method",
    "비고\n(증빙번호)": "evidence",
    "코드": "code",
    "번호": "number",
    "학교명": "school",
    "수혜기관명": "school",
    "하이웍스 제목": "hiworks_title",
    "하이웍스 URL": "source_url",
    "실제출금일": "actual_payment_date",
    "실집행일자": "actual_payment_date",
    "실출금금액": "actual_withdrawal_amount",
    "미출금액": "unwithdrawn_amount",
    "대조상태": "reconcile_status",
    "메모": "memo",
}

PLACEHOLDERS = {
    "",
    "-",
    "거래처명",
    "2026-00-00",
    "2025-06-00",
}


def parse_sheet_rows(sheet_name: str, values: list[list[Any]], data_start_row: int) -> list[SourceRow]:
    sheet_code = sheet_name.split("-", 1)[0]
    header_row = _get_row(values, 19)
    subheader_row = _get_row(values, 20)

    procurement_map = _build_header_map(header_row, subheader_row, 0, 13, PROCUREMENT_HEADERS)
    execution_map = _build_header_map(header_row, subheader_row, 13, 33, EXECUTION_HEADERS)

    rows: list[SourceRow] = []
    for row_number in range(data_start_row, len(values) + 1):
        row = _get_row(values, row_number)
        procurement = _extract(row, procurement_map)
        execution = _extract(row, execution_map)
        row_no = procurement.get("row_no") or execution.get("row_no") or str(row_number)
        if _is_summary_row(row_no):
            continue

        procurement_selected = _selected(procurement.get("selected"))
        execution_selected = _selected(execution.get("selected"))
        if not (
            procurement_selected
            and _is_active(procurement, "procurement")
            or execution_selected
            and _is_active(execution, "execution")
        ):
            continue

        key = f"{sheet_code}:{row_no}:{row_number}"
        rows.append(
            SourceRow(
                sheet_name=sheet_name,
                sheet_code=sheet_code,
                source_row=row_number,
                key=key,
                procurement=procurement,
                execution=execution,
            )
        )
    return rows


def _get_row(values: list[list[Any]], row_number: int) -> list[Any]:
    index = row_number - 1
    if index < 0 or index >= len(values):
        return []
    return values[index]


def _cell(row: list[Any], column_index: int) -> str:
    if column_index >= len(row):
        return ""
    value = row[column_index]
    if value is None:
        return ""
    return str(value).strip()


def _build_header_map(
    header_row: list[Any],
    subheader_row: list[Any],
    start: int,
    end: int,
    labels: dict[str, str],
) -> dict[str, int]:
    result: dict[str, int] = {}
    for index in range(start, end):
        candidates = [_cell(header_row, index), _cell(subheader_row, index)]
        for candidate in candidates:
            key = labels.get(candidate)
            if key and key not in result:
                result[key] = index
    return result


def _extract(row: list[Any], header_map: dict[str, int]) -> dict[str, str]:
    return {field: _cell(row, index) for field, index in header_map.items()}


def _is_active(data: dict[str, str], kind: str) -> bool:
    if _number(data.get("amount")) > 0:
        return True
    if kind == "execution" and (
        _number(data.get("supply_amount")) > 0 or _number(data.get("vat")) > 0
    ):
        return True
    if _clean(data.get("hiworks_title")):
        return True

    vendor = _clean(data.get("vendor"))
    date = _clean(data.get("date"))
    has_real_vendor = bool(vendor and vendor not in PLACEHOLDERS)
    has_real_date = bool(date and "00" not in date)
    return has_real_vendor and has_real_date


def _is_summary_row(row_no: str | None) -> bool:
    text = _clean(row_no)
    return any(keyword in text for keyword in ("소계", "합계", "총계"))


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text in PLACEHOLDERS else text


def _number(value: Any) -> int:
    if value is None:
        return 0
    text = str(value).replace(",", "").replace("원", "").strip()
    if not text or text == "-":
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def flatten_values(
    rows: Iterable[SourceRow],
    existing: dict[str, dict[str, str]] | None = None,
    total_budget: int = 240_000_000,
) -> list[list[Any]]:
    existing = existing or {}
    output = [SUMMARY_HEADERS]
    procurement_balance = total_budget
    execution_balance = total_budget
    for item in rows:
        previous = existing.get(item.key, {})
        procurement_active = _selected(item.procurement.get("selected")) and _is_active(item.procurement, "procurement")
        execution_active = _selected(item.execution.get("selected")) and _is_active(item.execution, "execution")
        procurement_status = previous.get("품의 상태", "")
        execution_status = previous.get("집행 상태", "")
        procurement_url = previous.get("품의 URL", "")
        procurement_error = previous.get("품의 오류", "")
        execution_url = previous.get("집행 URL", "")
        execution_error = previous.get("집행 오류", "")
        if _is_pending_selection(item.procurement.get("selected")) and procurement_active:
            procurement_status = ""
            procurement_url = ""
            procurement_error = ""
        if _is_pending_selection(item.execution.get("selected")) and execution_active:
            execution_status = ""
            execution_url = ""
            execution_error = ""
        procurement_amount = _number(item.procurement.get("amount"))
        execution_amount = _number(item.execution.get("amount"))
        procurement_remaining = ""
        execution_remaining = ""
        if procurement_active:
            procurement_balance -= procurement_amount
            procurement_remaining = _money(procurement_balance)
        if execution_active:
            execution_balance -= execution_amount
            execution_remaining = _money(execution_balance)

        output.append(
            [
                _mark(item.procurement.get("selected"), procurement_active, procurement_status),
                _mark(item.execution.get("selected"), execution_active, execution_status),
                item.sheet_name,
                item.procurement.get("date", ""),
                item.procurement.get("vendor", ""),
                item.procurement.get("amount", ""),
                item.procurement.get("payment_method", ""),
                item.procurement.get("evidence", ""),
                item.procurement.get("code", ""),
                item.procurement.get("number", ""),
                item.procurement.get("school", ""),
                item.procurement.get("hiworks_title", ""),
                item.procurement.get("note", ""),
                procurement_remaining,
                procurement_status,
                procurement_url,
                procurement_error,
                item.execution.get("date", ""),
                item.execution.get("vendor", ""),
                item.execution.get("supply_amount", ""),
                item.execution.get("vat", ""),
                item.execution.get("amount", ""),
                item.execution.get("payment_method", ""),
                item.execution.get("evidence", ""),
                item.execution.get("code", ""),
                item.execution.get("number", ""),
                item.execution.get("school", ""),
                item.execution.get("hiworks_title", ""),
                item.execution.get("actual_payment_date", ""),
                item.execution.get("actual_withdrawal_amount", ""),
                item.execution.get("unwithdrawn_amount", ""),
                item.execution.get("reconcile_status", ""),
                item.execution.get("memo", ""),
                execution_remaining,
                execution_status,
                execution_url,
                execution_error,
                item.key,
                item.sheet_code,
                item.source_row,
                item.procurement.get("row_no", ""),
                item.procurement.get("source_url", ""),
                item.execution.get("row_no", ""),
                item.execution.get("usage", ""),
                item.execution.get("source_url", ""),
            ]
        )
    return output


def existing_by_key(summary_values: list[list[Any]]) -> dict[str, dict[str, str]]:
    if not summary_values:
        return {}
    headers = [str(value) for value in summary_values[0]]
    result: dict[str, dict[str, str]] = {}
    for row in summary_values[1:]:
        record = {header: _cell(row, index) for index, header in enumerate(headers)}
        key = record.get("내부 키") or record.get("키")
        if key:
            result[key] = record
    return result


def _mark(source_value: str | None, active: bool, status: str = "") -> str:
    text = str(source_value or "").strip()
    if not active:
        return ""
    if text == "○" or _is_new_selection(text):
        return "○"
    if status == "기안완료" or text == "◎":
        return "◎"
    return ""


def _is_pending_selection(value: str | None) -> bool:
    text = str(value or "").strip()
    return text == "○" or _is_new_selection(text)


def _selected(value: str | None) -> bool:
    text = str(value or "").strip()
    return text == "◎" or _is_pending_selection(text)


def _is_new_selection(text: str) -> bool:
    return text in {"✓", "예"} or text.upper() in {"TRUE", "1", "Y", "YES", "V"}


def _money(value: int) -> str:
    return f"{value:,}"


SUMMARY_HEADERS = [
    "품의 업로드",
    "집행 업로드",
    "비목시트",
    "품의 기안일자",
    "품의 거래처",
    "품의 합계금액",
    "품의 결제",
    "품의 증빙번호",
    "품의 코드",
    "품의 번호",
    "품의 수혜기관명",
    "품의 하이웍스 제목",
    "품의 비고",
    "품의 잔여예산",
    "품의 상태",
    "품의 URL",
    "품의 오류",
    "집행 기안일자",
    "집행 거래처",
    "집행 공급가액",
    "집행 부가세",
    "집행 합계금액",
    "집행 결제",
    "집행 증빙번호",
    "집행 코드",
    "집행 번호",
    "집행 수혜기관명",
    "집행 하이웍스 제목",
    "실집행일자",
    "실출금금액",
    "미출금액",
    "대조상태",
    "메모",
    "집행 잔여예산",
    "집행 상태",
    "집행 URL",
    "집행 오류",
    "내부 키",
    "내부 비목코드",
    "내부 원본행",
    "내부 품의 순번",
    "내부 품의 하이웍스 URL",
    "내부 집행 순번",
    "내부 집행 사용내역",
    "내부 집행 하이웍스 URL",
]
