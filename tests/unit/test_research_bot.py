import json
from datetime import date

import pytest

from trading_bot.research_bot import (
    ChatGPTResearchExportWriter,
    OpenAIResearchClient,
    ResearchReportWriter,
    ResearchReviewer,
    ResearchReviewReport,
    build_chatgpt_markdown_export,
    build_research_input_from_audit_log,
)


def test_research_input_aggregates_scan_diagnostics(tmp_path):
    audit_path = tmp_path / "paper_audit.jsonl"
    audit_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event_type": "paper_scan_diagnostics",
                        "logged_at": "2026-05-20T14:00:00+00:00",
                        "diagnostics": {
                            "symbol": "QQQ",
                            "underlying_last": 700.0,
                            "contracts": {"received": 30, "eligible": 0},
                            "market_data": {
                                "market_data_incomplete": False,
                                "received_option_quotes": 30,
                                "received_greeks": 31,
                            },
                            "liquidity_blocks": {
                                "low_or_missing_volume": 30,
                                "low_or_missing_open_interest": 30,
                            },
                            "reason_codes": [
                                "no_eligible_contracts_after_liquidity_filters",
                                "low_or_missing_volume",
                            ],
                            "strategies": [
                                {
                                    "strategy_name": "put_credit_spread",
                                    "score": 51.0,
                                    "candidate_generated": False,
                                    "reason_codes": ["score_below_min_entry_score"],
                                }
                            ],
                        },
                    }
                ),
                json.dumps(
                    {
                        "event_type": "paper_cycle",
                        "logged_at": "2026-05-20T14:00:01+00:00",
                        "result": {
                            "generated_candidates": 0,
                            "opened_positions": 0,
                            "rejected_candidates": 0,
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    research_input = build_research_input_from_audit_log(
        audit_path,
        report_date=date(2026, 5, 20),
    )

    assert research_input.scan_count == 1
    assert research_input.cycle_count == 1
    assert research_input.symbols == ("QQQ",)
    assert research_input.top_reason_codes[0] == (
        "no_eligible_contracts_after_liquidity_filters",
        1,
    )
    assert research_input.top_liquidity_blocks[0] == ("low_or_missing_volume", 30)
    assert research_input.symbol_summaries[0]["avg_contracts_received"] == 30


def test_research_review_report_validates_research_only_json():
    report = ResearchReviewReport.from_json(_report_json())

    assert report.research_only is True
    assert report.improvement_hypotheses[0].confidence == 0.5

    invalid = json.loads(_report_json())
    invalid["research_only"] = False
    with pytest.raises(ValueError, match="research_only"):
        ResearchReviewReport.from_dict(invalid)


def test_research_reviewer_writes_read_only_json_report(tmp_path):
    audit_path = tmp_path / "paper_audit.jsonl"
    audit_path.write_text("", encoding="utf-8")
    research_input = build_research_input_from_audit_log(
        audit_path,
        report_date=date(2026, 5, 20),
    )
    client = _FakeResearchClient(_report_json())
    artifact = ResearchReviewer(client, model="gpt-5.5").review_to_artifact(research_input)
    output_path = ResearchReportWriter(tmp_path / "research").write(artifact)
    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert output_path.name == "daily_review_2026-05-20.json"
    assert payload["research_only"] is True
    assert payload["model"] == "gpt-5.5"
    assert payload["report"]["research_only"] is True
    assert "research-only" in client.last_prompt.lower()


def test_openai_research_client_reads_dotenv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=placeholder-key\nOPENAI_RESEARCH_MODEL=gpt-5.5\n",
        encoding="utf-8",
    )

    client = OpenAIResearchClient.from_env()

    assert client.api_key == "placeholder-key"
    assert client.model == "gpt-5.5"


def test_research_export_writes_chatgpt_markdown_without_api(tmp_path):
    audit_path = tmp_path / "paper_audit.jsonl"
    audit_path.write_text(
        json.dumps(
            {
                "event_type": "paper_scan_diagnostics",
                "logged_at": "2026-05-20T14:00:00+00:00",
                "diagnostics": {
                    "symbol": "QQQ",
                    "contracts": {"received": 30, "eligible": 0},
                    "reason_codes": ["no_eligible_contracts_after_liquidity_filters"],
                    "liquidity_blocks": {"low_or_missing_volume": 30},
                    "strategies": [],
                },
            }
        ),
        encoding="utf-8",
    )
    research_input = build_research_input_from_audit_log(
        audit_path,
        report_date=date(2026, 5, 20),
    )

    markdown = build_chatgpt_markdown_export(research_input)
    output_path = ChatGPTResearchExportWriter(tmp_path / "research").write(research_input)

    assert "Instructions For ChatGPT" in markdown
    assert "Do not recommend live trading" in markdown
    assert "Full Compact JSON Payload" in markdown
    assert output_path.name == "chatgpt_export_2026-05-20.md"
    assert "Trading Bot Research Export" in output_path.read_text(encoding="utf-8")


def _report_json() -> str:
    return json.dumps(
        {
            "report_date": "2026-05-20",
            "research_only": True,
            "executive_summary": "No trades were opened due to liquidity filters.",
            "data_quality_findings": ["Market data was complete for the sample."],
            "no_trade_reasons": ["No eligible contracts after liquidity filters."],
            "strategy_observations": ["Scores were below entry threshold for credit spreads."],
            "risk_observations": ["No live trading changes were made."],
            "improvement_hypotheses": [
                {
                    "hypothesis": "Test a lower OI threshold during market hours.",
                    "expected_effect": "May increase candidate coverage.",
                    "required_backtest": "Replay scans with OI 100 vs 50.",
                    "confidence": 0.5,
                }
            ],
            "backtest_tasks": [
                {
                    "name": "oi_threshold_sensitivity",
                    "description": "Compare candidate generation across OI thresholds.",
                    "success_metric": "Improved expectancy without worse max drawdown.",
                    "risk_to_watch": "More slippage from lower-liquidity contracts.",
                }
            ],
            "recommended_next_actions": ["Run threshold sensitivity backtest."],
            "prohibited_actions_verified": ["No automatic strategy config changes."],
            "confidence": 0.6,
        }
    )


class _FakeResearchClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.last_prompt = ""

    def complete_json(self, prompt: str) -> str:
        self.last_prompt = prompt
        return self.response
