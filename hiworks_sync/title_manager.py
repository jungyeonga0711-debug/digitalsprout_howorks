from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .google_sheets import SheetsClient, spreadsheet_id_from_url

LOG_HEADERS = [
    "기록시각",
    "이벤트",
    "구분",
    "시트",
    "원본행",
    "비고코드번호",
    "하이웍스 제목",
    "변경내용",
    "이전 전체행",
    "현재 전체행",
]

SNAPSHOT_HEADERS = [
    "스냅샷키",
    "구분",
    "시트",
    "원본행",
    "비고코드번호",
    "하이웍스 제목",
    "전체행JSON",
]

PROJECT_CODE = "260515-ED-03"
PROJECT_NAME = "26년 디싹"
TRAVEL_DATE_PLACEHOLDER = "260000"
TRAVEL_PURPOSE_PLACEHOLDER = "출장목적"


@dataclass(frozen=True)
class ManagedRecord:
    snapshot_key: str
    kind: str
    sheet_name: str
    row_number: int
    line_key: str
    title: str
    title_col: int
    record: dict[str, str]
    selected: bool = False
    needs_title_update: bool = False
    payment_col: int | None = None
    payment_value: str = ""
    needs_payment_update: bool = False


def generate_hiworks_titles(settings: dict) -> dict[str, int]:
    spreadsheet_settings = settings["spreadsheet"]
    spreadsheet_id = spreadsheet_id_from_url(spreadsheet_settings["url"])
    source_sheets = spreadsheet_settings["source_sheets"]
    read_range = spreadsheet_settings.get("title_read_range", "A1:AG1000")
    data_start_row = int(spreadsheet_settings.get("data_start_row", 24))
    snapshot_sheet = spreadsheet_settings.get("snapshot_sheet_name", "하이웍스_스냅샷")

    client = SheetsClient()
    client.ensure_sheet(spreadsheet_id, snapshot_sheet)
    ensure_title_columns(settings)
    _ensure_headers(client, spreadsheet_id, snapshot_sheet, SNAPSHOT_HEADERS)

    previous = _read_snapshots(client, spreadsheet_id, snapshot_sheet)
    current: dict[str, ManagedRecord] = {}
    title_updates: list[dict[str, Any]] = []
    payment_updates: list[dict[str, Any]] = []
    selection_updates: list[dict[str, Any]] = []
    selected_count = 0
    selected_counts = {"품의": 0, "집행": 0}
    title_counts = {"품의": 0, "집행": 0}
    source_ranges = [f"'{sheet_name}'!{read_range}" for sheet_name in source_sheets]
    source_values = client.batch_read_values(spreadsheet_id, source_ranges)

    for sheet_name, values in zip(source_sheets, source_values):
        for record in _records_from_sheet(sheet_name, values, data_start_row):
            if record.selected:
                selected_count += 1
                if record.kind in selected_counts:
                    selected_counts[record.kind] += 1
            if record.selected or record.snapshot_key in previous:
                current[record.snapshot_key] = record
            if record.selected and record.needs_title_update:
                if record.kind in title_counts:
                    title_counts[record.kind] += 1
                title_updates.append(
                    {
                        "range": f"'{sheet_name}'!{_column_letter(record.title_col)}{record.row_number}",
                        "values": [[record.title]],
                    }
                )
            if record.needs_payment_update and record.payment_col:
                payment_updates.append(
                    {
                        "range": f"'{sheet_name}'!{_column_letter(record.payment_col)}{record.row_number}",
                        "values": [[record.payment_value]],
                    }
                )
            selection_update = _selection_update(record)
            if selection_update is not None:
                selection_updates.append(
                    {
                        "range": f"'{sheet_name}'!{_column_letter(selection_update[0])}{record.row_number}",
                        "values": [[selection_update[1]]],
                    }
                )

    client.batch_update_values(spreadsheet_id, selection_updates + payment_updates + title_updates)

    log_rows = _diff_rows(previous, current)

    snapshot_values = [SNAPSHOT_HEADERS]
    for record in sorted(current.values(), key=lambda item: (item.sheet_name, item.kind, item.row_number)):
        snapshot_values.append(_snapshot_row(record))
    client.write_values(spreadsheet_id, f"'{snapshot_sheet}'!A1", snapshot_values)

    return {
        "titles": len(title_updates),
        "payments": len(payment_updates),
        "selections": len(selection_updates),
        "logs": 0,
        "changes": len(log_rows),
        "records": len(current),
        "selected": selected_count,
        "selected_procurement": selected_counts["품의"],
        "selected_execution": selected_counts["집행"],
        "titles_procurement": title_counts["품의"],
        "titles_execution": title_counts["집행"],
    }


def ensure_title_columns(settings: dict) -> dict[str, int]:
    spreadsheet_settings = settings["spreadsheet"]
    spreadsheet_id = spreadsheet_id_from_url(spreadsheet_settings["url"])
    source_sheets = spreadsheet_settings["source_sheets"]
    data_start_row = int(spreadsheet_settings.get("data_start_row", 24))

    client = SheetsClient()
    metadata = client.get_metadata(spreadsheet_id)
    sheet_by_name = {
        sheet["properties"]["title"]: sheet["properties"]
        for sheet in metadata.get("sheets", [])
    }

    requests: list[dict[str, Any]] = []
    value_updates: list[dict[str, Any]] = []
    changed = 0
    top_ranges = [f"'{sheet_name}'!A1:AG20" for sheet_name in source_sheets]
    top_values_by_sheet = dict(zip(source_sheets, client.batch_read_values(spreadsheet_id, top_ranges)))

    for sheet_name in source_sheets:
        properties = sheet_by_name.get(sheet_name)
        if not properties:
            continue
        sheet_id = int(properties["sheetId"])
        top_values = top_values_by_sheet.get(sheet_name, [])
        marker_row = top_values[0] if top_values else []
        header = top_values[18] if len(top_values) >= 19 else []

        repair_requests = _repair_requests(sheet_id, header)
        if repair_requests:
            requests.extend(repair_requests)
            changed += 1
        elif not _is_desired_header(header) and not _has_v4_marker(marker_row):
            requests.extend(_fresh_insert_requests(sheet_id))
            changed += 1

        value_updates.extend(
            [
                {"range": f"'{sheet_name}'!A19:A20", "values": [["구분"], ["품의"]]},
                {"range": f"'{sheet_name}'!F19:F20", "values": [["결제"], [""]]},
                {"range": f"'{sheet_name}'!J19:J20", "values": [["수혜기관명"], [""]]},
                {"range": f"'{sheet_name}'!L19:L20", "values": [["하이웍스 URL"], [""]]},
                {"range": f"'{sheet_name}'!N19:N20", "values": [["구분"], ["집행"]]},
                {"range": f"'{sheet_name}'!AB19:AB20", "values": [["하이웍스 URL"], [""]]},
                {"range": f"'{sheet_name}'!AA1:AG1", "values": [["", "", "", "", "", "", ""]]},
            ]
        )
        requests.extend(_validation_requests(sheet_id, data_start_row))

    client.batch_update(spreadsheet_id, requests)
    client.batch_update_values(spreadsheet_id, value_updates)
    return {"sheets": changed}


def _fresh_insert_requests(sheet_id: int) -> list[dict[str, Any]]:
    return [
        _insert_col(sheet_id, 0),
        _insert_col(sheet_id, 5),
        _insert_col(sheet_id, 9),
        _insert_col(sheet_id, 12),
    ]


def _repair_requests(sheet_id: int, header: list[Any]) -> list[dict[str, Any]]:
    if _is_v4_without_url_header(header):
        return [
            _insert_col(sheet_id, 11),
            _insert_col(sheet_id, 27),
        ]
    if _is_v4_double_insert_header(header):
        return [
            _delete_col(sheet_id, 10),
            _delete_col(sheet_id, 0),
        ]
    if _is_messy_v4_header(header):
        return [
            _delete_col(sheet_id, 12),
            _delete_col(sheet_id, 9),
            _delete_col(sheet_id, 5),
            _delete_col(sheet_id, 0),
        ]
    if _is_v3_header(header):
        return [
            _insert_col(sheet_id, 5),
            _copy_paste(sheet_id, 9, 5),
            _delete_col(sheet_id, 9),
            _insert_col(sheet_id, 9),
        ]
    if _is_v2_duplicate_header(header):
        return [
            _delete_col(sheet_id, 10),
            _delete_col(sheet_id, 0),
            _insert_col(sheet_id, 5),
            _copy_paste(sheet_id, 9, 5),
            _delete_col(sheet_id, 9),
            _insert_col(sheet_id, 9),
        ]
    return []


def _is_desired_header(header: list[Any]) -> bool:
    return (
        _is_selection_header(header, 0)
        and _at(header, 1) == "순번"
        and _at(header, 4) == "합계금액"
        and _at(header, 5) == "결제"
        and _at(header, 6).startswith("비고")
        and _at(header, 9) == "수혜기관명"
        and _at(header, 10) == "하이웍스 제목"
        and _at(header, 11) == "하이웍스 URL"
        and _is_selection_header(header, 13)
        and _at(header, 14) == "순번"
        and _at(header, 26) == "하이웍스 제목"
        and _at(header, 27) == "하이웍스 URL"
    )


def _is_v4_without_url_header(header: list[Any]) -> bool:
    return (
        _is_selection_header(header, 0)
        and _at(header, 1) == "순번"
        and _at(header, 10) == "하이웍스 제목"
        and _at(header, 11) != "하이웍스 URL"
        and _is_selection_header(header, 12)
        and _at(header, 25) == "하이웍스 제목"
    )


def _is_messy_v4_header(header: list[Any]) -> bool:
    return (
        _is_selection_header(header, 0)
        and _is_selection_header(header, 1)
        and _at(header, 2) == "순번"
        and _at(header, 5) == "결제"
        and _at(header, 7) == "결제"
        and _at(header, 9) == "수혜기관명"
        and _is_selection_header(header, 12)
        and _at(header, 13) == "수혜기관명"
        and _is_selection_header(header, 16)
    )


def _is_v4_double_insert_header(header: list[Any]) -> bool:
    return (
        _is_selection_header(header, 0)
        and _is_selection_header(header, 1)
        and _at(header, 2) == "순번"
        and _is_selection_header(header, 10)
        and _is_selection_header(header, 14)
    )


def _is_v3_header(header: list[Any]) -> bool:
    return (
        _is_selection_header(header, 0)
        and _at(header, 1) == "순번"
        and _at(header, 5).startswith("비고")
        and _at(header, 8) == "결제"
        and _at(header, 9) == "하이웍스 제목"
        and _is_selection_header(header, 11)
    )


def _is_v2_duplicate_header(header: list[Any]) -> bool:
    return (
        _is_selection_header(header, 0)
        and _is_selection_header(header, 1)
        and _at(header, 2) == "순번"
        and _is_selection_header(header, 10)
        and _at(header, 11) == "하이웍스 제목"
        and _is_selection_header(header, 13)
    )


def _has_v4_marker(marker_row: list[Any]) -> bool:
    return False


def _is_selection_header(values: list[Any], index: int) -> bool:
    return _at(values, index) in {"구분", "제목생성"}


def _validation_requests(sheet_id: int, data_start_row: int) -> list[dict[str, Any]]:
    return [
        _clear_validation_rows(sheet_id, 18, data_start_row - 1, 0),
        _clear_validation_rows(sheet_id, 18, data_start_row - 1, 5),
        _clear_validation_rows(sheet_id, 18, data_start_row - 1, 13),
        _clear_validation_rows(sheet_id, 18, data_start_row - 1, 21),
        _clear_validation(sheet_id, data_start_row, 0),
        _clear_validation(sheet_id, data_start_row, 13),
        _clear_validation(sheet_id, data_start_row, 18),
        _clear_validation(sheet_id, data_start_row, 19),
        _clear_validation(sheet_id, data_start_row, 20),
        _payment_validation(sheet_id, data_start_row, 5),
        _payment_validation(sheet_id, data_start_row, 21),
    ]


def _insert_col(sheet_id: int, index: int) -> dict[str, Any]:
    return {
        "insertDimension": {
            "range": {
                "sheetId": sheet_id,
                "dimension": "COLUMNS",
                "startIndex": index,
                "endIndex": index + 1,
            },
            "inheritFromBefore": False,
        }
    }


def _delete_col(sheet_id: int, index: int) -> dict[str, Any]:
    return {
        "deleteDimension": {
            "range": {
                "sheetId": sheet_id,
                "dimension": "COLUMNS",
                "startIndex": index,
                "endIndex": index + 1,
            }
        }
    }


def _copy_paste(sheet_id: int, source_col: int, destination_col: int) -> dict[str, Any]:
    return {
        "copyPaste": {
            "source": {
                "sheetId": sheet_id,
                "startRowIndex": 0,
                "endRowIndex": 1000,
                "startColumnIndex": source_col,
                "endColumnIndex": source_col + 1,
            },
            "destination": {
                "sheetId": sheet_id,
                "startRowIndex": 0,
                "endRowIndex": 1000,
                "startColumnIndex": destination_col,
                "endColumnIndex": destination_col + 1,
            },
            "pasteType": "PASTE_VALUES",
            "pasteOrientation": "NORMAL",
        }
    }


def _payment_validation(sheet_id: int, data_start_row: int, col: int) -> dict[str, Any]:
    return {
        "setDataValidation": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": data_start_row - 1,
                "endRowIndex": 1000,
                "startColumnIndex": col,
                "endColumnIndex": col + 1,
            },
            "rule": {
                "condition": {
                    "type": "ONE_OF_LIST",
                    "values": [
                        {"userEnteredValue": "카드"},
                        {"userEnteredValue": "계좌"},
                    ],
                },
                "strict": False,
                "showCustomUi": True,
            },
        }
    }


def _clear_validation(sheet_id: int, data_start_row: int, col: int) -> dict[str, Any]:
    return {
        "setDataValidation": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": data_start_row - 1,
                "endRowIndex": 1000,
                "startColumnIndex": col,
                "endColumnIndex": col + 1,
            }
        }
    }


def _clear_validation_rows(sheet_id: int, start_row: int, end_row: int, col: int) -> dict[str, Any]:
    return {
        "setDataValidation": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": start_row,
                "endRowIndex": end_row,
                "startColumnIndex": col,
                "endColumnIndex": col + 1,
            }
        }
    }


def _ensure_headers(client: SheetsClient, spreadsheet_id: str, sheet_name: str, headers: list[str]) -> None:
    values = client.read_values(spreadsheet_id, f"'{sheet_name}'!A1:{_column_letter(len(headers))}1")
    if values and values[0] == headers:
        return
    client.clear_values(spreadsheet_id, f"'{sheet_name}'!A:Z")
    client.write_values(spreadsheet_id, f"'{sheet_name}'!A1", [headers])


def _read_snapshots(client: SheetsClient, spreadsheet_id: str, sheet_name: str) -> dict[str, ManagedRecord]:
    values = client.read_values(spreadsheet_id, f"'{sheet_name}'!A1:G10000")
    if not values or values[0] != SNAPSHOT_HEADERS:
        return {}
    result: dict[str, ManagedRecord] = {}
    for row in values[1:]:
        padded = row + [""] * (len(SNAPSHOT_HEADERS) - len(row))
        try:
            payload = json.loads(padded[6]) if padded[6] else {}
        except json.JSONDecodeError:
            payload = {}
        key = str(padded[0])
        if not key:
            continue
        result[key] = ManagedRecord(
            snapshot_key=key,
            kind=str(padded[1]),
            sheet_name=str(padded[2]),
            row_number=_to_int(padded[3]),
            line_key=str(padded[4]),
            title=str(padded[5]),
            title_col=0,
            record={str(k): str(v) for k, v in payload.items()},
        )
    return result


def _records_from_sheet(sheet_name: str, values: list[list[Any]], data_start_row: int) -> list[ManagedRecord]:
    result: list[ManagedRecord] = []
    for row_number in range(data_start_row, len(values) + 1):
        row = _row(values, row_number)
        procurement = _procurement_record(sheet_name, row_number, row)
        execution = _execution_record(sheet_name, row_number, row)
        for item in (procurement, execution):
            if item and _is_real_input(item.record):
                result.append(item)
    return result


def _procurement_record(sheet_name: str, row_number: int, row: list[Any]) -> ManagedRecord:
    raw_payment = _cell(row, 6)
    payment = _normalize_payment_cell(raw_payment)
    raw_selection = _cell(row, 1)
    record = {
        "선택원본": raw_selection,
        "순번": _cell(row, 2),
        "기안일자": _cell(row, 3),
        "거래처": _cell(row, 4),
        "합계금액": _cell(row, 5),
        "결제": payment,
        "비고": _cell(row, 7),
        "코드": _cell(row, 8),
        "번호": _cell(row, 9),
        "수혜기관명": _cell(row, 10),
        "하이웍스 제목": _cell(row, 11),
        "하이웍스 URL": _cell(row, 12),
        "메모": _cell(row, 13),
    }
    title = _travel_title("품의", record) if sheet_name.startswith("L-") else _general_title("품의", record)
    needs_title_update = _clean(record["하이웍스 제목"]) != title
    final_title = title if needs_title_update else (record["하이웍스 제목"] or title)
    return ManagedRecord(
        snapshot_key=f"{sheet_name}|품의|{row_number}",
        kind="품의",
        sheet_name=sheet_name,
        row_number=row_number,
        line_key=_line_key(record),
        title=final_title,
        title_col=11,
        record={**record, "하이웍스 제목": final_title},
        selected=_checked(raw_selection),
        needs_title_update=needs_title_update,
        payment_col=6,
        payment_value=payment,
        needs_payment_update=bool(payment and raw_payment != payment),
    )


def _execution_record(sheet_name: str, row_number: int, row: list[Any]) -> ManagedRecord:
    raw_payment = _cell(row, 22)
    payment = _normalize_payment_cell(raw_payment)
    raw_selection = _cell(row, 14)
    record = {
        "선택원본": raw_selection,
        "순번": _cell(row, 15),
        "기안일자": _cell(row, 16),
        "거래처": _cell(row, 17),
        "공급가액": _cell(row, 18),
        "부가세": _cell(row, 19),
        "합계금액": _cell(row, 20),
        "사용내역": _cell(row, 21),
        "결제": payment,
        "비고": _cell(row, 23),
        "코드": _cell(row, 24),
        "번호": _cell(row, 25),
        "수혜기관명": _cell(row, 26),
        "하이웍스 제목": _cell(row, 27),
        "하이웍스 URL": _cell(row, 28),
        "실집행일자": _cell(row, 29),
        "실출금금액": _cell(row, 30),
        "미출금액": _cell(row, 31),
        "대조상태": _cell(row, 32),
        "메모": _cell(row, 33),
    }
    title = _travel_title("집행", record) if sheet_name.startswith("L-") else _general_title("집행", record)
    needs_title_update = _clean(record["하이웍스 제목"]) != title
    final_title = title if needs_title_update else (record["하이웍스 제목"] or title)
    return ManagedRecord(
        snapshot_key=f"{sheet_name}|집행|{row_number}",
        kind="집행",
        sheet_name=sheet_name,
        row_number=row_number,
        line_key=_line_key(record),
        title=final_title,
        title_col=27,
        record={**record, "하이웍스 제목": final_title},
        selected=_checked(raw_selection),
        needs_title_update=needs_title_update,
        payment_col=22,
        payment_value=payment,
        needs_payment_update=bool(payment and raw_payment != payment),
    )


def _is_real_input(record: dict[str, str]) -> bool:
    return bool(
        _clean(record.get("하이웍스 제목"))
        or _clean(record.get("거래처"))
        or _number(record.get("합계금액")) > 0
        or _number(record.get("공급가액")) > 0
        or _number(record.get("부가세")) > 0
        or _clean(record.get("사용내역"))
        or _clean(record.get("수혜기관명"))
    )


def _diff_rows(previous: dict[str, ManagedRecord], current: dict[str, ManagedRecord]) -> list[list[str]]:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows: list[list[str]] = []
    for key, current_record in current.items():
        previous_record = previous.get(key)
        if not previous_record:
            rows.append(_log_row(now, "생성", current_record, "신규 관리 행 생성", {}, current_record.record))
            continue
        changes = _changes(previous_record.record, current_record.record)
        if changes:
            rows.append(
                _log_row(
                    now,
                    "수정",
                    current_record,
                    "; ".join(changes),
                    previous_record.record,
                    current_record.record,
                )
            )

    for key, previous_record in previous.items():
        if key not in current:
            rows.append(
                _log_row(
                    now,
                    "삭제",
                    previous_record,
                    "관리 행이 삭제되었거나 주요 입력값이 비워짐",
                    previous_record.record,
                    {},
                )
            )
    return rows


def _changes(before: dict[str, str], after: dict[str, str]) -> list[str]:
    keys = sorted(set(before) | set(after))
    result: list[str] = []
    for key in keys:
        old = before.get(key, "")
        new = after.get(key, "")
        if old != new:
            result.append(f"{key}: {old or '(빈칸)'} -> {new or '(빈칸)'}")
    return result


def _log_row(
    timestamp: str,
    event: str,
    record: ManagedRecord,
    change_summary: str,
    before: dict[str, str],
    after: dict[str, str],
) -> list[str]:
    return [
        timestamp,
        event,
        record.kind,
        record.sheet_name,
        str(record.row_number),
        record.line_key,
        record.title,
        change_summary,
        json.dumps(before, ensure_ascii=False, sort_keys=True),
        json.dumps(after, ensure_ascii=False, sort_keys=True),
    ]


def _snapshot_row(record: ManagedRecord) -> list[str]:
    return [
        record.snapshot_key,
        record.kind,
        record.sheet_name,
        str(record.row_number),
        record.line_key,
        record.title,
        json.dumps(record.record, ensure_ascii=False, sort_keys=True),
    ]


def _general_title(kind: str, record: dict[str, str]) -> str:
    pieces = [
        f"[{kind}] {PROJECT_CODE}",
        _money(record.get("합계금액")),
        _clean(record.get("거래처")),
        PROJECT_NAME,
        _line_key(record),
    ]
    school = _clean(record.get("수혜기관명"))
    if school:
        pieces.append(school)
    payment = _payment_label(record.get("결제"))
    if payment:
        pieces.append(payment)
    return " / ".join(piece for piece in pieces if piece)


def _travel_title(kind: str, record: dict[str, str]) -> str:
    label = "출신" if kind == "품의" else "출보"
    pieces = [f"[{label}] {PROJECT_CODE}"]
    if kind == "집행":
        pieces.append(_money(record.get("합계금액")))
    pieces.extend(
        [
            PROJECT_NAME,
            _line_key({"비고": record.get("비고", ""), "번호": record.get("번호", "")}),
            _travel_date_code(record.get("기안일자")),
            TRAVEL_PURPOSE_PLACEHOLDER,
        ]
    )
    return " / ".join(piece for piece in pieces if piece)


def _travel_date_code(value: str | None) -> str:
    text = _clean(value)
    if not text:
        return TRAVEL_DATE_PLACEHOLDER

    numbers = re.findall(r"\d+", text)
    if len(numbers) >= 3:
        year = int(numbers[0])
        month = int(numbers[1])
        day = int(numbers[2])
    elif len(numbers) >= 2:
        year = int(PROJECT_CODE[:2])
        month = int(numbers[0])
        day = int(numbers[1])
    else:
        return TRAVEL_DATE_PLACEHOLDER

    if year >= 100:
        year %= 100
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return TRAVEL_DATE_PLACEHOLDER
    return f"{year:02d}{month:02d}{day:02d}"


def _line_key(record: dict[str, str]) -> str:
    return "-".join(
        piece
        for piece in [
            _key_piece(record.get("비고")),
            _key_piece(record.get("코드")),
            _key_piece(record.get("번호")),
        ]
        if piece
    )


def _payment_label(value: str | None) -> str:
    text = _normalize_payment_cell(value)
    if text == "계좌":
        return "계좌이체"
    if text == "카드":
        return "카드결제"
    return text


def _normalize_payment_cell(value: str | None) -> str:
    text = _clean(value)
    if "계좌" in text:
        return "계좌"
    if "카드" in text:
        return "카드"
    return text


def _selection_update(record: ManagedRecord) -> tuple[int, str] | None:
    source = _clean(record.record.get("선택원본"))
    if source in {"✓", "예", "TRUE", "True", "true", "1"}:
        return (1 if record.kind == "품의" else 14, "○")
    if source in {"FALSE", "False", "false", "0"}:
        return (1 if record.kind == "품의" else 14, "")
    return None


def _key_piece(value: str | None) -> str:
    return _clean(value).strip("-")


def _money(value: str | None) -> str:
    text = _clean(value)
    if not text:
        return ""
    number = text.replace(",", "").replace("원", "").strip()
    try:
        return f"{int(float(number)):,}"
    except ValueError:
        return text


def _row(values: list[list[Any]], row_number: int) -> list[Any]:
    index = row_number - 1
    if index < 0 or index >= len(values):
        return []
    return values[index]


def _cell(row: list[Any], one_based_col: int) -> str:
    index = one_based_col - 1
    if index < 0 or index >= len(row) or row[index] is None:
        return ""
    return str(row[index]).strip()


def _at(values: list[Any], index: int) -> str:
    if index >= len(values) or values[index] is None:
        return ""
    return str(values[index]).strip()


def _clean(value: str | None) -> str:
    text = (value or "").strip()
    if text in {"", "-", "거래처명", "2026-00-00", "2025-06-00"}:
        return ""
    return text


def _number(value: str | None) -> int:
    text = _clean(value).replace(",", "").replace("원", "").strip()
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def _to_int(value: Any) -> int:
    try:
        return int(str(value))
    except ValueError:
        return 0


def _checked(value: str | None) -> bool:
    text = str(value or "").strip()
    return text in {"✓", "○", "예"} or text.upper() in {"TRUE", "1", "Y", "YES", "V"}


def _column_letter(one_based_col: int) -> str:
    value = one_based_col
    letters = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters
