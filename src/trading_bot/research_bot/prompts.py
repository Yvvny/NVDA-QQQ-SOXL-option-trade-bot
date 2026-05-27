from __future__ import annotations

import json
from typing import Any


def build_research_review_prompt(research_payload: dict[str, Any]) -> str:
    return (
        "You are a research-only options trading system reviewer. Analyze the provided "
        "paper-trading scan diagnostics and produce one JSON object matching the requested "
        "schema. Do not suggest live trading, automatic parameter changes, larger position "
        "sizes, removing risk limits, or bypassing the risk engine. Every suggestion must be "
        "framed as a research hypothesis or backtest task requiring human review.\n\n"
        "Required JSON fields:\n"
        "- report_date: string\n"
        "- research_only: true\n"
        "- executive_summary: string\n"
        "- data_quality_findings: string[]\n"
        "- no_trade_reasons: string[]\n"
        "- strategy_observations: string[]\n"
        "- risk_observations: string[]\n"
        "- improvement_hypotheses: objects with hypothesis, expected_effect, "
        "required_backtest, confidence\n"
        "- backtest_tasks: objects with name, description, success_metric, risk_to_watch\n"
        "- recommended_next_actions: string[]\n"
        "- prohibited_actions_verified: string[]\n"
        "- confidence: number from 0 to 1\n\n"
        "Research payload:\n"
        f"{json.dumps(research_payload, sort_keys=True)}"
    )
