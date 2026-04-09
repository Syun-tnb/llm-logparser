from __future__ import annotations

import pytest

from llm_logparser.core.structured_llm import generate_structured_json


class _FakeClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def embeddings(self, model: str, prompt: str) -> list[float]:
        raise AssertionError("embeddings should not be called")

    def generate_text(
        self,
        model: str,
        prompt: str,
        *,
        response_format: str | None = None,
        options: dict[str, object] | None = None,
    ) -> str:
        self.calls.append(
            {
                "model": model,
                "prompt": prompt,
                "response_format": response_format,
                "options": options,
            }
        )
        return self.responses.pop(0)

    def generate_json(self, model: str, prompt: str) -> dict:
        raise AssertionError("generate_json should not be called")


def test_generate_structured_json_returns_parsed_payload():
    client = _FakeClient(['{"key": "value"}'])

    result = generate_structured_json(
        client,
        model="llama3.1",
        prompt="return JSON",
        options={"temperature": 0.0},
    )

    assert result == {"key": "value"}
    assert client.calls == [
        {
            "model": "llama3.1",
            "prompt": "return JSON",
            "response_format": "json",
            "options": {"temperature": 0.0},
        }
    ]


def test_generate_structured_json_retries_once_on_invalid_json():
    client = _FakeClient(["{invalid", '{"key": "value"}'])

    result = generate_structured_json(
        client,
        model="llama3.1",
        prompt="return JSON",
    )

    assert result == {"key": "value"}
    assert len(client.calls) == 2


def test_generate_structured_json_raises_after_retry_failure():
    client = _FakeClient(["{invalid", "{still invalid"])

    with pytest.raises(RuntimeError, match="not valid JSON after 2 attempts"):
        generate_structured_json(
            client,
            model="llama3.1",
            prompt="return JSON",
        )
