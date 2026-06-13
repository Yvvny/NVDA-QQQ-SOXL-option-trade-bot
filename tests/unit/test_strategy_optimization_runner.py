from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def test_strategy_optimization_runner_records_chatgpt_instruction(tmp_path):
    runner = _load_runner_module()
    output_dir = _write_context(tmp_path)
    client = _FakeRoundClient(
        {
            "diagnosis": "Exit rules are not yet compared by realized expectancy.",
            "proposed_change": "Add an exit matrix research gate before paper deployment.",
            "expected_benefit": "Improves exit selection discipline.",
            "risk_impact": "Risk decreases because paper deployment is deferred until tested.",
            "required_tests": ["pytest tests/unit/test_backtest.py"],
            "codex_implementation_instruction": "Create an exit comparison artifact.",
            "should_deploy_to_paper": False,
        }
    )

    results = runner.run_rounds(
        output_dir=output_dir,
        start_round=7,
        round_count=1,
        client=client,
        skip_completed=False,
    )

    assert results[0].status == "chatgpt_instruction_received"
    assert "Focus only on this round's topic" in client.last_prompt
    response_path = output_dir / "rounds" / "round_07_chatgpt_response.json"
    codex_task_path = output_dir / "rounds" / "round_07_codex_task.md"
    assert response_path.exists()
    assert codex_task_path.exists()
    assert json.loads(response_path.read_text(encoding="utf-8"))["should_deploy_to_paper"] is False

    index_records = [
        json.loads(line)
        for line in (output_dir / "strategy_optimization_rounds.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert index_records[-1]["round"] == 7
    assert index_records[-1]["status"] == "chatgpt_instruction_received"


def test_strategy_optimization_runner_writes_pending_file_without_client(tmp_path):
    runner = _load_runner_module()
    output_dir = _write_context(tmp_path)

    results = runner.run_rounds(
        output_dir=output_dir,
        start_round=8,
        round_count=1,
        client=None,
        missing_key_error="OPENAI_API_KEY is required for research-review.",
        skip_completed=False,
    )

    assert results[0].status == "pending_openai_api_key"
    pending_path = output_dir / "rounds" / "round_08_pending_openai_api.md"
    assert pending_path.exists()
    assert "OPENAI_API_KEY" in pending_path.read_text(encoding="utf-8")


def test_strategy_optimization_runner_skips_completed_round(tmp_path):
    runner = _load_runner_module()
    output_dir = _write_context(tmp_path)
    (output_dir / "strategy_optimization_rounds.jsonl").write_text(
        json.dumps({"round": 9, "status": "implemented_and_deployed"}) + "\n",
        encoding="utf-8",
    )

    results = runner.run_rounds(
        output_dir=output_dir,
        start_round=9,
        round_count=1,
        client=_FakeRoundClient({}),
        skip_completed=True,
    )

    assert results[0].status == "skipped_completed"


def _write_context(tmp_path: Path) -> Path:
    output_dir = tmp_path / "strategy_optimization"
    output_dir.mkdir(parents=True)
    (output_dir / "current_context.json").write_text(
        json.dumps(
            {
                "paper_summary": {
                    "state": {
                        "starting_equity": 2000.0,
                        "equity": 1705.5,
                        "total_pnl": -294.5,
                        "total_return_pct": -14.72,
                        "available_cash": 1198.0,
                        "total_open_max_loss": 507.5,
                        "open_positions": 2,
                        "closed_trades": 2,
                    }
                },
                "local_strategy": {"files": {}},
                "safety_constraints": {},
            }
        ),
        encoding="utf-8",
    )
    return output_dir


def _load_runner_module():
    module_path = (
        Path(__file__).resolve().parents[2]
        / "tools"
        / "run_strategy_optimization_rounds.py"
    )
    spec = importlib.util.spec_from_file_location("run_strategy_optimization_rounds", module_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _FakeRoundClient:
    model = "fake-research-model"

    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.last_prompt = ""

    def complete_json(self, prompt: str) -> str:
        self.last_prompt = prompt
        return json.dumps(self.response)
