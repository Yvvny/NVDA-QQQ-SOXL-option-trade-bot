from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class OpenAIResearchClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenAIResearchClient:
    api_key: str
    model: str = "gpt-5.5"
    timeout_seconds: float = 60.0

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        model: str | None = None,
    ) -> OpenAIResearchClient:
        values = _merged_env(env)
        api_key = values.get("OPENAI_API_KEY", "")
        if not api_key:
            raise OpenAIResearchClientError("OPENAI_API_KEY is required for research-review.")
        return cls(
            api_key=api_key,
            model=model or values.get("OPENAI_RESEARCH_MODEL", "gpt-5.5"),
        )

    def complete_json(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return valid JSON only. You are research-only and cannot alter "
                        "trading behavior."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise OpenAIResearchClientError(f"OpenAI request failed: {exc.code} {body}") from exc
        except urllib.error.URLError as exc:
            raise OpenAIResearchClientError(f"OpenAI request failed: {exc}") from exc

        return _extract_chat_content(response_payload)


def _extract_chat_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise OpenAIResearchClientError("OpenAI response did not include choices.")
    first = choices[0]
    if not isinstance(first, dict):
        raise OpenAIResearchClientError("OpenAI response choice was malformed.")
    message = first.get("message")
    if not isinstance(message, dict):
        raise OpenAIResearchClientError("OpenAI response did not include a message.")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise OpenAIResearchClientError("OpenAI response content was empty.")
    return content


def _merged_env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    if env is not None:
        return env
    values = _read_dotenv(Path(".env"))
    values.update(os.environ)
    return values


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values
