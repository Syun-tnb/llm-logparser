from __future__ import annotations

import json
from typing import Any

from .llm_client_protocol import LLMClient


def generate_structured_json(
    client: LLMClient,
    *,
    model: str,
    prompt: str,
    options: dict[str, object] | None = None,
) -> dict[str, Any]:
    """Generate one structured JSON object through the higher-layer LLM client."""
    for attempt in range(2):
        response_text = client.generate_text(
            model=model,
            prompt=prompt,
            response_format="json",
            options=options,
        )
        try:
            payload = json.loads(response_text)
        except json.JSONDecodeError as exc:
            if attempt == 0:
                continue
            raise RuntimeError(
                "structured LLM response was not valid JSON after 2 attempts"
            ) from exc

        if not isinstance(payload, dict):
            raise RuntimeError("structured LLM response must be a JSON object")
        return payload

    raise RuntimeError("structured LLM retry loop exited unexpectedly")
