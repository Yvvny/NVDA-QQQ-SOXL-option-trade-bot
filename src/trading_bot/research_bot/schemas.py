from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ResearchHypothesis:
    hypothesis: str
    expected_effect: str
    required_backtest: str
    confidence: float


@dataclass(frozen=True)
class BacktestTask:
    name: str
    description: str
    success_metric: str
    risk_to_watch: str


@dataclass(frozen=True)
class ResearchReviewReport:
    report_date: str
    research_only: bool
    executive_summary: str
    data_quality_findings: tuple[str, ...]
    no_trade_reasons: tuple[str, ...]
    strategy_observations: tuple[str, ...]
    risk_observations: tuple[str, ...]
    improvement_hypotheses: tuple[ResearchHypothesis, ...]
    backtest_tasks: tuple[BacktestTask, ...]
    recommended_next_actions: tuple[str, ...]
    prohibited_actions_verified: tuple[str, ...]
    confidence: float

    @classmethod
    def from_json(cls, raw_json: str) -> ResearchReviewReport:
        payload = json.loads(raw_json)
        if not isinstance(payload, dict):
            raise ValueError("Research review must be a JSON object.")
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ResearchReviewReport:
        hypotheses = _required_dict_list(payload, "improvement_hypotheses")
        tasks = _required_dict_list(payload, "backtest_tasks")
        report = cls(
            report_date=_required_str(payload, "report_date"),
            research_only=_required_bool(payload, "research_only"),
            executive_summary=_required_str(payload, "executive_summary"),
            data_quality_findings=tuple(_required_str_list(payload, "data_quality_findings")),
            no_trade_reasons=tuple(_required_str_list(payload, "no_trade_reasons")),
            strategy_observations=tuple(_required_str_list(payload, "strategy_observations")),
            risk_observations=tuple(_required_str_list(payload, "risk_observations")),
            improvement_hypotheses=tuple(
                ResearchHypothesis(
                    hypothesis=_required_str(item, "hypothesis"),
                    expected_effect=_required_str(item, "expected_effect"),
                    required_backtest=_required_str(item, "required_backtest"),
                    confidence=float(item["confidence"]),
                )
                for item in hypotheses
            ),
            backtest_tasks=tuple(
                BacktestTask(
                    name=_required_str(item, "name"),
                    description=_required_str(item, "description"),
                    success_metric=_required_str(item, "success_metric"),
                    risk_to_watch=_required_str(item, "risk_to_watch"),
                )
                for item in tasks
            ),
            recommended_next_actions=tuple(_required_str_list(payload, "recommended_next_actions")),
            prohibited_actions_verified=tuple(
                _required_str_list(payload, "prohibited_actions_verified")
            ),
            confidence=float(payload["confidence"]),
        )
        if not report.research_only:
            raise ValueError("Research report must be marked research_only=true.")
        return report


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


def _required_dict_list(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{key} must be a list of objects.")
    return value
