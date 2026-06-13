from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Any, Protocol

from trading_bot.core.time_utils import now_new_york
from trading_bot.research_bot.openai_client import DEFAULT_RESEARCH_MODEL, OpenAIResearchClient


class StrategyChatClient(Protocol):
    def complete_json(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class StrategyChatMessage:
    role: str
    content: str


@dataclass(frozen=True)
class StrategyChangeProposal:
    title: str
    rationale: str
    files: tuple[str, ...]
    validation: tuple[str, ...]
    risk_impact: str


@dataclass(frozen=True)
class StrategyChatResponse:
    research_only: bool
    assistant_reply: str
    summary: str
    needs_human_approval: bool
    codex_task: str
    proposed_changes: tuple[StrategyChangeProposal, ...]
    follow_up_questions: tuple[str, ...]
    confidence: float
    model: str = DEFAULT_RESEARCH_MODEL
    generated_at: datetime | None = None

    @classmethod
    def from_json(cls, raw_json: str) -> StrategyChatResponse:
        payload = json.loads(raw_json)
        if not isinstance(payload, dict):
            raise ValueError("Strategy chat response must be a JSON object.")
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> StrategyChatResponse:
        proposals = _required_dict_list(payload, "proposed_changes")
        response = cls(
            research_only=_required_bool(payload, "research_only"),
            assistant_reply=_required_str(payload, "assistant_reply"),
            summary=_required_str(payload, "summary"),
            needs_human_approval=_required_bool(payload, "needs_human_approval"),
            codex_task=_required_str(payload, "codex_task"),
            proposed_changes=tuple(
                StrategyChangeProposal(
                    title=_required_str(item, "title"),
                    rationale=_required_str(item, "rationale"),
                    files=tuple(_required_str_list(item, "files")),
                    validation=tuple(_required_str_list(item, "validation")),
                    risk_impact=_required_str(item, "risk_impact"),
                )
                for item in proposals
            ),
            follow_up_questions=tuple(_required_str_list(payload, "follow_up_questions")),
            confidence=float(payload["confidence"]),
        )
        if not response.research_only:
            raise ValueError("Strategy chat response must be marked research_only=true.")
        return response

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.generated_at is None:
            payload.pop("generated_at", None)
        return _jsonable(payload)


class StrategyChatAssistant:
    def __init__(self, client: StrategyChatClient, *, model: str = DEFAULT_RESEARCH_MODEL) -> None:
        self.client = client
        self.model = model

    @classmethod
    def from_env(
        cls,
        env: dict[str, str] | None = None,
        *,
        model: str | None = None,
    ) -> StrategyChatAssistant:
        client = OpenAIResearchClient.from_env(env, model=model)
        return cls(client, model=client.model)

    def respond(
        self,
        messages: Sequence[StrategyChatMessage],
        *,
        context: dict[str, Any],
        mode: str = "strategy",
    ) -> StrategyChatResponse:
        prompt = build_strategy_chat_prompt(messages, context=context, mode=mode, model=self.model)
        response = StrategyChatResponse.from_json(self.client.complete_json(prompt))
        return replace(response, model=self.model, generated_at=now_new_york())


def build_strategy_chat_prompt(
    messages: Sequence[StrategyChatMessage],
    *,
    context: dict[str, Any],
    mode: str,
    model: str,
) -> str:
    conversation_payload = [
        {"role": message.role, "content": message.content}
        for message in messages
    ]
    return (
        "You are a research-only ChatGPT assistant embedded in a paper-trading dashboard.\n"
        "Your job is to answer questions, explain the current strategy state, and propose\n"
        "research-only changes. You must never claim to have modified files, changed live\n"
        "strategy parameters, bypassed risk controls, or submitted orders.\n\n"
        "Important rules:\n"
        "- Reply in the user's language when possible.\n"
        "- Keep all recommendations research-only.\n"
        "- If the user asks for a strategy change, produce a Codex-ready task in codex_task.\n"
        "- Do not recommend live trading or removing risk limits.\n"
        "- If you propose changes, include the file paths, validation steps, and risk impact.\n"
        "- The output must be valid JSON only.\n\n"
        "Required JSON fields:\n"
        "- research_only: boolean (must be true)\n"
        "- assistant_reply: string\n"
        "- summary: string\n"
        "- needs_human_approval: boolean\n"
        "- codex_task: string\n"
        "- proposed_changes: objects with title, rationale, files, validation, risk_impact\n"
        "- follow_up_questions: string[]\n"
        "- confidence: number from 0 to 1\n\n"
        f"Assistant model: {model}\n"
        f"Conversation mode: {mode}\n\n"
        f"Context JSON:\n{json.dumps(_jsonable(context), ensure_ascii=False, sort_keys=True)}\n\n"
        "Conversation JSON:\n"
        f"{json.dumps(conversation_payload, ensure_ascii=False, sort_keys=True)}\n"
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


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
