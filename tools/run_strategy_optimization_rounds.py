from __future__ import annotations

# ruff: noqa: E402,I001

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from build_strategy_optimization_context import (
    DEFAULT_OUTPUT_DIR,
    ROUND_TOPICS,
    _round_prompt,
)
from trading_bot.research_bot.openai_client import (
    DEFAULT_RESEARCH_MODEL,
    OpenAIResearchClient,
    OpenAIResearchClientError,
)

ROUND_RESPONSE_FIELDS = (
    "diagnosis",
    "proposed_change",
    "expected_benefit",
    "risk_impact",
    "required_tests",
    "codex_implementation_instruction",
    "should_deploy_to_paper",
)


class RoundClient(Protocol):
    model: str

    def complete_json(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class OptimizationRoundResponse:
    diagnosis: str
    proposed_change: str
    expected_benefit: str
    risk_impact: str
    required_tests: tuple[str, ...]
    codex_implementation_instruction: str
    should_deploy_to_paper: bool

    @classmethod
    def from_json(cls, raw_json: str) -> OptimizationRoundResponse:
        payload = json.loads(raw_json)
        if not isinstance(payload, dict):
            raise ValueError("Round response must be a JSON object.")
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> OptimizationRoundResponse:
        required_tests = payload.get("required_tests")
        if isinstance(required_tests, str):
            required_tests = [required_tests]
        if not isinstance(required_tests, list) or any(
            not isinstance(item, str) for item in required_tests
        ):
            raise ValueError("required_tests must be a list of strings.")
        return cls(
            diagnosis=_required_str(payload, "diagnosis"),
            proposed_change=_required_str(payload, "proposed_change"),
            expected_benefit=_required_str(payload, "expected_benefit"),
            risk_impact=_required_str(payload, "risk_impact"),
            required_tests=tuple(required_tests),
            codex_implementation_instruction=_required_str(
                payload,
                "codex_implementation_instruction",
            ),
            should_deploy_to_paper=_required_bool(payload, "should_deploy_to_paper"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RoundRunResult:
    round_number: int
    topic: str
    status: str
    prompt_path: str
    response_path: str | None = None
    codex_task_path: str | None = None
    error_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--start-round", type=int, default=1)
    parser.add_argument("--rounds", type=int, default=len(ROUND_TOPICS))
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--skip-completed",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip rounds already listed as implemented/deployed in the JSONL index.",
    )
    parser.add_argument(
        "--offline-ok",
        action="store_true",
        help="Write pending request/error files instead of failing when OPENAI_API_KEY is absent.",
    )
    args = parser.parse_args(argv)

    try:
        client = OpenAIResearchClient.from_env(model=args.model)
    except OpenAIResearchClientError as exc:
        if not args.offline_ok:
            raise
        client = None
        missing_key_error = str(exc)
    else:
        missing_key_error = ""

    results = run_rounds(
        output_dir=Path(args.output_dir),
        start_round=args.start_round,
        round_count=args.rounds,
        client=client,
        missing_key_error=missing_key_error,
        skip_completed=args.skip_completed,
    )
    print(json.dumps([result.to_dict() for result in results], indent=2, sort_keys=True))
    return 0


def run_rounds(
    *,
    output_dir: Path,
    start_round: int,
    round_count: int,
    client: RoundClient | None,
    missing_key_error: str = "",
    skip_completed: bool = True,
) -> list[RoundRunResult]:
    context_path = output_dir / "current_context.json"
    if not context_path.exists():
        raise FileNotFoundError(
            f"{context_path} is missing. Run tools/build_strategy_optimization_context.py first."
        )

    context = json.loads(context_path.read_text(encoding="utf-8"))
    rounds_dir = output_dir / "rounds"
    rounds_dir.mkdir(parents=True, exist_ok=True)
    index_path = output_dir / "strategy_optimization_rounds.jsonl"
    completed_rounds = _completed_rounds(index_path) if skip_completed else set()
    results: list[RoundRunResult] = []

    end_round = min(len(ROUND_TOPICS), start_round + round_count - 1)
    for round_number in range(start_round, end_round + 1):
        topic = ROUND_TOPICS[round_number - 1]
        prompt_path = rounds_dir / f"round_{round_number:02d}_prompt.md"
        if not prompt_path.exists():
            prompt_path.write_text(
                _round_prompt(round_number, topic, context),
                encoding="utf-8",
            )

        if round_number in completed_rounds:
            results.append(
                RoundRunResult(
                    round_number=round_number,
                    topic=topic,
                    status="skipped_completed",
                    prompt_path=str(prompt_path),
                )
            )
            continue

        if client is None:
            error_path = rounds_dir / f"round_{round_number:02d}_pending_openai_api.md"
            error_path.write_text(
                _pending_markdown(
                    round_number=round_number,
                    topic=topic,
                    prompt_path=prompt_path,
                    missing_key_error=missing_key_error,
                ),
                encoding="utf-8",
            )
            results.append(
                RoundRunResult(
                    round_number=round_number,
                    topic=topic,
                    status="pending_openai_api_key",
                    prompt_path=str(prompt_path),
                    error_path=str(error_path),
                )
            )
            continue

        prompt = _json_round_prompt(
            round_number=round_number,
            topic=topic,
            base_prompt=prompt_path.read_text(encoding="utf-8"),
            context=context,
            previous_rounds=_read_jsonl(index_path),
        )
        response = OptimizationRoundResponse.from_json(client.complete_json(prompt))
        response_path = rounds_dir / f"round_{round_number:02d}_chatgpt_response.json"
        response_path.write_text(
            json.dumps(response.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        codex_task_path = rounds_dir / f"round_{round_number:02d}_codex_task.md"
        codex_task_path.write_text(
            _codex_task_markdown(round_number, topic, response),
            encoding="utf-8",
        )
        _append_index(
            index_path,
            {
                "round": round_number,
                "topic": topic,
                "model_observed": getattr(client, "model", DEFAULT_RESEARCH_MODEL),
                "chatgpt_response_file": str(response_path),
                "codex_task_file": str(codex_task_path),
                "decision": "pending_codex_review",
                "status": "chatgpt_instruction_received",
                "should_deploy_to_paper": response.should_deploy_to_paper,
                "generated_at": datetime.now().astimezone().isoformat(),
            },
        )
        results.append(
            RoundRunResult(
                round_number=round_number,
                topic=topic,
                status="chatgpt_instruction_received",
                prompt_path=str(prompt_path),
                response_path=str(response_path),
                codex_task_path=str(codex_task_path),
            )
        )
    return results


def _json_round_prompt(
    *,
    round_number: int,
    topic: str,
    base_prompt: str,
    context: dict[str, Any],
    previous_rounds: list[dict[str, Any]],
) -> str:
    return (
        "You are ChatGPT acting as an external research reviewer for a paper-only "
        "options trading bot. Return valid JSON only. Do not claim to edit files, "
        "deploy code, place orders, or bypass risk controls.\n\n"
        f"Round: {round_number}\n"
        f"Topic: {topic}\n\n"
        "Base prompt:\n"
        f"{base_prompt}\n\n"
        "Previous completed or pending rounds JSON:\n"
        f"{json.dumps(previous_rounds, ensure_ascii=False, sort_keys=True)}\n\n"
        "Current context JSON:\n"
        f"{json.dumps(context, ensure_ascii=False, sort_keys=True)}\n\n"
        "Return exactly this JSON shape:\n"
        "{\n"
        '  "diagnosis": "string",\n'
        '  "proposed_change": "string",\n'
        '  "expected_benefit": "string",\n'
        '  "risk_impact": "string",\n'
        '  "required_tests": ["string"],\n'
        '  "codex_implementation_instruction": "string",\n'
        '  "should_deploy_to_paper": true\n'
        "}\n"
        "Safety rules: keep live trading disabled, keep defined-risk only, keep 0DTE "
        "forbidden, keep max-loss checks, keep liquidity checks, and keep the risk "
        "engine as final veto."
    )


def _pending_markdown(
    *,
    round_number: int,
    topic: str,
    prompt_path: Path,
    missing_key_error: str,
) -> str:
    return "\n".join(
        [
            f"# Round {round_number:02d} Pending OpenAI API",
            "",
            f"Topic: {topic}",
            "",
            f"Prompt file: `{prompt_path}`",
            "",
            "Status: cannot call ChatGPT/OpenAI API from this environment.",
            "",
            f"Reason: `{missing_key_error or 'OPENAI_API_KEY is missing.'}`",
            "",
            "Next action: add `OPENAI_API_KEY` and optional `OPENAI_RESEARCH_MODEL` "
            "to the environment or `.env`, then rerun:",
            "",
            "```powershell",
            "python tools\\run_strategy_optimization_rounds.py --start-round "
            f"{round_number} --rounds {len(ROUND_TOPICS) - round_number + 1}",
            "```",
            "",
        ]
    )


def _codex_task_markdown(
    round_number: int,
    topic: str,
    response: OptimizationRoundResponse,
) -> str:
    tests = "\n".join(f"- {item}" for item in response.required_tests)
    return "\n".join(
        [
            f"# Round {round_number:02d} Codex Task",
            "",
            f"Topic: {topic}",
            "",
            "## Diagnosis",
            "",
            response.diagnosis,
            "",
            "## Proposed Change",
            "",
            response.proposed_change,
            "",
            "## Risk Impact",
            "",
            response.risk_impact,
            "",
            "## Implementation Instruction",
            "",
            response.codex_implementation_instruction,
            "",
            "## Required Tests",
            "",
            tests or "No tests specified.",
            "",
            "## Should Deploy To Paper",
            "",
            "yes" if response.should_deploy_to_paper else "no",
            "",
        ]
    )


def _completed_rounds(index_path: Path) -> set[int]:
    completed: set[int] = set()
    for record in _read_jsonl(index_path):
        status = str(record.get("status") or "")
        if status in {"implemented_and_deployed", "implemented", "deployed"}:
            try:
                completed.add(int(record.get("round")))
            except (TypeError, ValueError):
                continue
    return completed


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _append_index(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + os.linesep)


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


if __name__ == "__main__":
    raise SystemExit(main())
