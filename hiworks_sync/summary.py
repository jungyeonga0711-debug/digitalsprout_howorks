from __future__ import annotations

from .google_sheets import SheetsClient, spreadsheet_id_from_url
from .parser import existing_by_key, flatten_values, parse_sheet_rows


def sync_summary(settings: dict) -> int:
    spreadsheet_settings = settings["spreadsheet"]
    spreadsheet_id = spreadsheet_id_from_url(spreadsheet_settings["url"])
    summary_sheet_name = spreadsheet_settings.get("summary_sheet_name", "하이웍스_종합")
    source_sheets = spreadsheet_settings["source_sheets"]
    read_range = spreadsheet_settings.get("read_range", "A1:AA1000")
    data_start_row = int(spreadsheet_settings.get("data_start_row", 24))
    total_budget = int(settings.get("budget", {}).get("total", 240_000_000))

    client = SheetsClient()
    summary_sheet_id = client.ensure_sheet(spreadsheet_id, summary_sheet_name)

    existing_summary = client.read_values(spreadsheet_id, f"'{summary_sheet_name}'!A1:AZ10000")
    existing = existing_by_key(existing_summary)

    rows = []
    source_ranges = [f"'{sheet_name}'!{read_range}" for sheet_name in source_sheets]
    source_values = client.batch_read_values(spreadsheet_id, source_ranges)
    for sheet_name, values in zip(source_sheets, source_values):
        rows.extend(parse_sheet_rows(sheet_name, values, data_start_row))

    output = flatten_values(rows, existing, total_budget)
    client.clear_values(spreadsheet_id, f"'{summary_sheet_name}'!A1:AZ10000")
    client.write_values(spreadsheet_id, f"'{summary_sheet_name}'!A1", output)
    client.format_summary(spreadsheet_id, summary_sheet_id, len(output), len(output[0]), [str(value) for value in output[0]])
    return len(output) - 1
