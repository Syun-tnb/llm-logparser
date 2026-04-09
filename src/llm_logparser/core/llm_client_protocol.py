from __future__ import annotations

from typing import Protocol


class LLMClient(Protocol):
    """Small higher-layer client surface for local LLM runtime interactions."""

    def embeddings(self, model: str, prompt: str) -> list[float]:
        """Return a single embedding vector for the given prompt."""

    def generate_text(
        self,
        model: str,
        prompt: str,
        *,
        response_format: str | None = None,
        options: dict[str, object] | None = None,
    ) -> str:
        """Return generated response text for the given prompt."""

    def generate_json(self, model: str, prompt: str) -> dict:
        """Return a generated JSON object for the given prompt."""
