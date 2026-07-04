from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

from google.oauth2 import service_account
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class SheetsClient:
    def __init__(self) -> None:
        credentials = _credentials()
        self.service = build("sheets", "v4", credentials=credentials)

    def get_metadata(self, spreadsheet_id: str) -> dict[str, Any]:
        return _execute(self.service.spreadsheets().get(spreadsheetId=spreadsheet_id))

    def read_values(self, spreadsheet_id: str, range_name: str) -> list[list[Any]]:
        result = (
            self.service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=range_name)
        )
        result = _execute(result)
        return result.get("values", [])

    def batch_read_values(self, spreadsheet_id: str, ranges: list[str]) -> list[list[list[Any]]]:
        if not ranges:
            return []
        result = _execute(
            self.service.spreadsheets()
            .values()
            .batchGet(spreadsheetId=spreadsheet_id, ranges=ranges)
        )
        value_ranges = result.get("valueRanges", [])
        return [item.get("values", []) for item in value_ranges]

    def ensure_sheet(self, spreadsheet_id: str, sheet_name: str) -> int:
        metadata = self.get_metadata(spreadsheet_id)
        for sheet in metadata.get("sheets", []):
            properties = sheet.get("properties", {})
            if properties.get("title") == sheet_name:
                return int(properties["sheetId"])

        response = (
            self.service.spreadsheets()
            .batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": sheet_name}}}]},
            )
        )
        response = _execute(response)
        return int(response["replies"][0]["addSheet"]["properties"]["sheetId"])

    def write_values(self, spreadsheet_id: str, range_name: str, values: list[list[Any]]) -> None:
        _execute(self.service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            body={},
        ))
        _execute(self.service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption="USER_ENTERED",
            body={"values": values},
        ))

    def clear_values(self, spreadsheet_id: str, range_name: str) -> None:
        _execute(self.service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            body={},
        ))

    def append_values(self, spreadsheet_id: str, range_name: str, values: list[list[Any]]) -> None:
        if not values:
            return
        _execute(self.service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": values},
        ))

    def batch_update_values(self, spreadsheet_id: str, updates: list[dict[str, Any]]) -> None:
        if not updates:
            return
        _execute(self.service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": updates},
        ))

    def batch_update(self, spreadsheet_id: str, requests: list[dict[str, Any]]) -> None:
        if not requests:
            return
        _execute(self.service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": requests},
        ))

    def format_summary(
        self,
        spreadsheet_id: str,
        sheet_id: int,
        row_count: int,
        column_count: int,
        header_row: list[str] | None = None,
    ) -> None:
        header_row = header_row or []
        requests = [
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet_id,
                        "gridProperties": {"frozenRowCount": 1},
                    },
                    "fields": "gridProperties.frozenRowCount",
                }
            },
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": 1,
                        "startColumnIndex": 0,
                        "endColumnIndex": column_count,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"bold": True},
                            "backgroundColor": {"red": 0.9, "green": 0.95, "blue": 1.0},
                        }
                    },
                    "fields": "userEnteredFormat(textFormat,backgroundColor)",
                }
            },
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "endRowIndex": max(row_count, 2),
                        "startColumnIndex": 0,
                        "endColumnIndex": column_count,
                    },
                    "cell": {"userEnteredFormat": {"textFormat": {"bold": False}}},
                    "fields": "userEnteredFormat.textFormat.bold",
                }
            },
            {
                "setDataValidation": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "endRowIndex": max(row_count, 2),
                        "startColumnIndex": 0,
                        "endColumnIndex": 2,
                    }
                }
            },
        ]
        for index in range(column_count):
            header_text = header_row[index] if index < len(header_row) else ""
            if header_text.startswith("내부 "):
                requests.append(
                    {
                        "updateDimensionProperties": {
                            "range": {
                                "sheetId": sheet_id,
                                "dimension": "COLUMNS",
                                "startIndex": index,
                                "endIndex": index + 1,
                            },
                            "properties": {"hiddenByUser": True},
                            "fields": "hiddenByUser",
                        }
                    }
                )
                continue

            pixel_size = min(max(len(header_text) * 11 + 24, 72), 220)
            requests.append(
                {
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": sheet_id,
                            "dimension": "COLUMNS",
                            "startIndex": index,
                            "endIndex": index + 1,
                        },
                        "properties": {"pixelSize": pixel_size, "hiddenByUser": False},
                        "fields": "pixelSize,hiddenByUser",
                    }
                }
            )
        _execute(self.service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": requests},
        ))

    def update_status(
        self,
        spreadsheet_id: str,
        summary_sheet_name: str,
        row_number: int,
        kind: str,
        status: str,
        url: str = "",
        error: str = "",
    ) -> None:
        headers = self.read_values(spreadsheet_id, f"'{summary_sheet_name}'!A1:AZ1")
        header = [str(value) for value in headers[0]] if headers else []
        prefix = "품의" if kind == "procurement" else "집행" if kind == "execution" else ""
        if not prefix:
            raise ValueError(f"알 수 없는 kind: {kind}")
        try:
            start_col = header.index(f"{prefix} 상태") + 1
        except ValueError as exc:
            raise ValueError(f"{summary_sheet_name}에서 {prefix} 상태 열을 찾을 수 없습니다.") from exc
        range_name = (
            f"'{summary_sheet_name}'!"
            f"{_column_letter(start_col)}{row_number}:{_column_letter(start_col + 2)}{row_number}"
        )

        _execute(self.service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption="USER_ENTERED",
            body={"values": [[status, url, error]]},
        ))
        if status == "기안완료" and url:
            try:
                marker_col = header.index(f"{prefix} 업로드") + 1
            except ValueError as exc:
                raise ValueError(f"{summary_sheet_name}에서 {prefix} 업로드 열을 찾을 수 없습니다.") from exc
            _execute(self.service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=f"'{summary_sheet_name}'!{_column_letter(marker_col)}{row_number}",
                valueInputOption="USER_ENTERED",
                body={"values": [["◎"]]},
            ))

    def update_source_url(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        source_row: int,
        kind: str,
        url: str,
    ) -> None:
        if not url:
            return
        if kind == "procurement":
            column = "L"
        elif kind == "execution":
            column = "AB"
        else:
            raise ValueError(f"알 수 없는 kind: {kind}")
        _execute(self.service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{sheet_name}'!{column}{source_row}",
            valueInputOption="USER_ENTERED",
            body={"values": [[url]]},
        ))

    def update_source_marker(
        self,
        spreadsheet_id: str,
        sheet_name: str,
        source_row: int,
        kind: str,
        marker: str,
    ) -> None:
        if not marker:
            return
        if kind == "procurement":
            column = "A"
        elif kind == "execution":
            column = "N"
        else:
            raise ValueError(f"알 수 없는 kind: {kind}")
        _execute(self.service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{sheet_name}'!{column}{source_row}",
            valueInputOption="USER_ENTERED",
            body={"values": [[marker]]},
        ))


def _column_letter(one_based: int) -> str:
    result = ""
    number = one_based
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _execute(request):
    for attempt in range(3):
        try:
            return request.execute()
        except HttpError as exc:
            if exc.resp.status != 429 or attempt == 2:
                raise
            time.sleep(65)
    raise RuntimeError("Google Sheets 요청 재시도에 실패했습니다.")


def spreadsheet_id_from_url(url_or_id: str) -> str:
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url_or_id)
    if match:
        return match.group(1)
    return url_or_id


def _credentials() -> Credentials:
    service_account_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    local_service_account_path = Path("service-account.json")
    if not service_account_path and local_service_account_path.exists():
        service_account_path = str(local_service_account_path)

    if service_account_path:
        return service_account.Credentials.from_service_account_file(
            service_account_path,
            scopes=SCOPES,
        )

    token_path = Path("token.json")
    client_secret_path = Path("client_secret.json")

    credentials = None
    if token_path.exists():
        credentials = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        token_path.write_text(credentials.to_json(), encoding="utf-8")

    if credentials and credentials.valid:
        return credentials

    if not client_secret_path.exists():
        raise FileNotFoundError(
            "service-account.json을 프로젝트 폴더에 두거나, "
            "GOOGLE_APPLICATION_CREDENTIALS를 설정하거나, "
            "client_secret.json을 프로젝트 루트에 두세요."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), SCOPES)
    credentials = flow.run_local_server(port=0)
    token_path.write_text(credentials.to_json(), encoding="utf-8")
    return credentials
