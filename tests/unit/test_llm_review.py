import json

import pytest

from trading_bot.llm_review import LLMTradeReview, LLMTradeReviewer, MainError, TradeQuality


def test_llm_trade_review_validates_json_response():
    review = LLMTradeReview.from_json(_review_json())

    assert review.trade_quality == TradeQuality.B
    assert review.main_error == MainError.NO_ERROR
    assert review.improvement_hypotheses[0].confidence == 0.4


def test_llm_trade_review_rejects_invalid_json_shape():
    with pytest.raises(ValueError, match="trade_quality"):
        LLMTradeReview.from_json(json.dumps({"trade_quality": 123}))


def test_llm_trade_reviewer_uses_client_but_only_returns_validated_artifact():
    client = _FakeClient(_review_json())
    review = LLMTradeReviewer(client).review_trade({"trade_id": "t1", "pnl": 50})

    assert review.trade_quality == TradeQuality.B
    assert "research only" in client.last_prompt.lower()


def _review_json() -> str:
    return json.dumps(
        {
            "trade_quality": "B",
            "main_error": "no_error",
            "should_have_traded": True,
            "violated_rules": [],
            "missed_warnings": [],
            "improvement_hypotheses": [
                {
                    "hypothesis": "Test a tighter liquidity threshold.",
                    "expected_effect": "May reduce slippage.",
                    "required_backtest": "Replay last 50 dry-run trades.",
                    "confidence": 0.4,
                }
            ],
            "risk_notes": "Defined-risk trade stayed within limits.",
        }
    )


class _FakeClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.last_prompt = ""

    def complete_json(self, prompt: str) -> str:
        self.last_prompt = prompt
        return self.response
