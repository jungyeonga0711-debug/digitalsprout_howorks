from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_settings(path: str | Path) -> dict[str, Any]:
    settings_path = Path(path)
    with settings_path.open("r", encoding="utf-8") as fp:
        settings = yaml.safe_load(fp) or {}

    if "spreadsheet" not in settings:
        raise ValueError("settings.yml에 spreadsheet 설정이 필요합니다.")
    if "hiworks" not in settings:
        settings["hiworks"] = {}

    return settings

