from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from trading_bot.research_bot.analyzer import ResearchInput
from trading_bot.research_bot.prompts import build_research_review_prompt
from trading_bot.research_bot.schemas import ResearchReviewReport


class ResearchReviewClient(Protocol):
    def complete_json(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class ResearchReviewArtifact:
    generated_at: datetime
    model: str
    report: ResearchReviewReport
    research_input: ResearchInput
    research_only: bool = True


class ResearchReviewer:
    def __init__(self, client: ResearchReviewClient, *, model: str = "gpt-5.5") -> None:
        self.client = client
        self.model = model

    def review(self, research_input: ResearchInput) -> ResearchReviewReport:
        prompt = build_research_review_prompt(research_input.to_prompt_payload())
        response = self.client.complete_json(prompt)
        return ResearchReviewReport.from_json(response)

    def review_to_artifact(self, research_input: ResearchInput) -> ResearchReviewArtifact:
        return ResearchReviewArtifact(
            generated_at=datetime.now(UTC),
            model=self.model,
            report=self.review(research_input),
            research_input=research_input,
        )


class ResearchReportWriter:
    def __init__(self, output_dir: str | Path = "docs/reports/research") -> None:
        self.output_dir = Path(output_dir)

    def write(self, artifact: ResearchReviewArtifact) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        report_date = artifact.report.report_date
        path = self.output_dir / f"daily_review_{report_date}.json"
        path.write_text(
            json.dumps(_jsonable(artifact), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return path


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
