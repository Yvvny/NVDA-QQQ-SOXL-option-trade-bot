from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from trading_bot.core.time_utils import now_new_york


class JsonlAuditLogger:
    def __init__(self, path: str | Path = "docs/reports/trade_audit.jsonl") -> None:
        self.path = Path(path)

    def record(self, event: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"logged_at": now_new_york(), **event}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_jsonable(payload), sort_keys=True) + "\n")


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value
