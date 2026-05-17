from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class TradeQuality(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"


class MainError(StrEnum):
    ENTRY = "entry"
    EXIT = "exit"
    POSITION_SIZE = "position_size"
    REGIME_MISMATCH = "regime_mismatch"
    LIQUIDITY = "liquidity"
    EVENT_RISK = "event_risk"
    NO_ERROR = "no_error"


@dataclass(frozen=True)
class ImprovementHypothesis:
    hypothesis: str
    expected_effect: str
    required_backtest: str
    confidence: float


@dataclass(frozen=True)
class LLMTradeReview:
    trade_quality: TradeQuality
    main_error: MainError
    should_have_traded: bool
    violated_rules: tuple[str, ...]
    missed_warnings: tuple[str, ...]
    improvement_hypotheses: tuple[ImprovementHypothesis, ...]
    risk_notes: str

    @classmethod
    def from_json(cls, raw_json: str) -> LLMTradeReview:
        payload = json.loads(raw_json)
        if not isinstance(payload, dict):
            raise ValueError("LLM review must be a JSON object.")
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> LLMTradeReview:
        hypotheses = payload.get("improvement_hypotheses", [])
        if not isinstance(hypotheses, list):
            raise ValueError("improvement_hypotheses must be a list.")

        return cls(
            trade_quality=TradeQuality(_required_str(payload, "trade_quality")),
            main_error=MainError(_required_str(payload, "main_error")),
            should_have_traded=_required_bool(payload, "should_have_traded"),
            violated_rules=tuple(_required_str_list(payload, "violated_rules")),
            missed_warnings=tuple(_required_str_list(payload, "missed_warnings")),
            improvement_hypotheses=tuple(
                ImprovementHypothesis(
                    hypothesis=_required_str(item, "hypothesis"),
                    expected_effect=_required_str(item, "expected_effect"),
                    required_backtest=_required_str(item, "required_backtest"),
                    confidence=float(item["confidence"]),
                )
                for item in hypotheses
                if isinstance(item, dict)
            ),
            risk_notes=_required_str(payload, "risk_notes"),
        )


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string.")
    return value


def _required_bool(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean.")
    return value


def _required_str_list(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be a list of strings.")
    return value
