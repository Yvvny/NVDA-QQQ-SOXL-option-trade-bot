from __future__ import annotations

from typing import Any, Protocol

from trading_bot.llm_review.prompts import build_trade_review_prompt
from trading_bot.llm_review.schemas import LLMTradeReview


class LLMReviewClient(Protocol):
    def complete_json(self, prompt: str) -> str: ...


class LLMTradeReviewer:
    def __init__(self, client: LLMReviewClient) -> None:
        self.client = client

    def review_trade(self, trade_payload: dict[str, Any]) -> LLMTradeReview:
        prompt = build_trade_review_prompt(trade_payload)
        response = self.client.complete_json(prompt)
        return LLMTradeReview.from_json(response)
