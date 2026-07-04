from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SourceRow:
    sheet_name: str
    sheet_code: str
    source_row: int
    key: str
    procurement: dict[str, Any]
    execution: dict[str, Any]


@dataclass(frozen=True)
class ApprovalTask:
    key: str
    kind: str
    row_number: int
    payload: dict[str, Any]

