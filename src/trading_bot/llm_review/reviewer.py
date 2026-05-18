from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from trading_bot.llm_review.prompts import build_trade_review_prompt
from trading_bot.llm_review.schemas import LLMTradeReview


class LLMReviewClient(Protocol):
    def complete_json(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class LLMReviewArtifact:
    trade_id: str
    generated_at: datetime
    review: LLMTradeReview
    trade_payload: dict[str, Any]
    research_only: bool = True


class LLMTradeReviewer:
    def __init__(self, client: LLMReviewClient) -> None:
        self.client = client

    def review_trade(self, trade_payload: dict[str, Any]) -> LLMTradeReview:
        prompt = build_trade_review_prompt(trade_payload)
        response = self.client.complete_json(prompt)
        return LLMTradeReview.from_json(response)

    def review_trade_to_artifact(
        self,
        trade_payload: dict[str, Any],
        *,
        trade_id: str,
    ) -> LLMReviewArtifact:
        return LLMReviewArtifact(
            trade_id=trade_id,
            generated_at=datetime.now(UTC),
            review=self.review_trade(trade_payload),
            trade_payload=trade_payload,
        )


class LLMReviewArtifactWriter:
    def __init__(self, path: str | Path = "docs/reports/llm_reviews.jsonl") -> None:
        self.path = Path(path)

    def write(self, artifact: LLMReviewArtifact) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_jsonable(artifact), sort_keys=True) + "\n")


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
