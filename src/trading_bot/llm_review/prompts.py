from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any


def build_trade_review_prompt(trade_payload: dict[str, Any]) -> str:
    return (
        "Review this closed trade as research only. Return JSON matching the configured "
        "LLMTradeReview schema. Do not recommend live parameter changes without backtesting.\n\n"
        f"Trade payload:\n{json.dumps(_jsonable(trade_payload), sort_keys=True)}"
    )


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value
