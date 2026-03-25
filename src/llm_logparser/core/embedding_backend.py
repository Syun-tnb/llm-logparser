from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from urllib import error as urllib_error
from urllib import request as urllib_request
from typing import Protocol


class EmbeddingBackend(Protocol):
    model_id: str

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text."""


@dataclass(frozen=True)
class EmbeddingModelSettings:
    max_input_tokens: int
    chunk_overlap_tokens: int
    aggregate: str = "mean"


DEFAULT_EMBEDDING_SETTINGS = EmbeddingModelSettings(
    max_input_tokens=256,
    chunk_overlap_tokens=32,
    aggregate="mean",
)

OLLAMA_MODEL_PRESETS: dict[str, EmbeddingModelSettings] = {
    "nomic-embed-text-v2-moe": EmbeddingModelSettings(
        max_input_tokens=512,
        chunk_overlap_tokens=64,
        aggregate="mean",
    ),
    "embeddinggemma": EmbeddingModelSettings(
        max_input_tokens=2048,
        chunk_overlap_tokens=128,
        aggregate="mean",
    ),
}


class DeterministicHashEmbeddingBackend:
    """A tiny deterministic local backend for prototype semantic plumbing.

    This backend is intentionally simple and model-agnostic. It exists to make
    the Step 2 prototype rebuildable and testable without pulling in a real
    embedding model. It should not be treated as a quality semantic model.
    """

    def __init__(self, *, dim: int = 32) -> None:
        if dim <= 0:
            raise ValueError("embedding dimension must be > 0")
        self.dim = dim
        self.model_id = f"deterministic/hash-bow-v1-d{dim}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        for token in _tokenize(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            for offset in (0, 4, 8, 12):
                idx = int.from_bytes(digest[offset : offset + 2], "big") % self.dim
                sign = 1.0 if digest[offset + 2] < 128 else -1.0
                weight = 1.0 + (digest[offset + 3] / 255.0)
                vector[idx] += sign * weight
        return _l2_normalize(vector)


class OllamaEmbeddingBackend:
    """Minimal Ollama local embedding backend using the default HTTP API."""

    def __init__(
        self,
        model: str,
        *,
        settings: EmbeddingModelSettings | None = None,
        base_url: str = "http://localhost:11434",
        timeout_seconds: float = 30.0,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("ollama embedding model must be a non-empty string")
        self.model = model.strip()
        self.settings = settings or resolve_embedding_model_settings(self.model)
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.model_id = f"ollama/{self.model}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        vectors: list[list[float]] = []
        for text in texts:
            chunks = chunk_text_for_embedding(
                text,
                max_input_tokens=self.settings.max_input_tokens,
                chunk_overlap_tokens=self.settings.chunk_overlap_tokens,
            )
            chunk_vectors = self._embed_request(chunks)
            vectors.append(
                aggregate_embeddings(
                    chunk_vectors,
                    aggregate=self.settings.aggregate,
                )
            )
        return vectors

    def _embed_request(self, texts: list[str]) -> list[list[float]]:
        payload = {
            "model": self.model,
            "input": texts,
        }
        body = json.dumps(payload).encode("utf-8")
        request = urllib_request.Request(
            f"{self.base_url}/api/embed",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib_request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw_response = response.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            detail = _decode_error_body(exc)
            raise RuntimeError(
                f"ollama embedding request failed for model '{self.model}': "
                f"HTTP {exc.code}{detail}"
            ) from exc
        except urllib_error.URLError as exc:
            raise RuntimeError(
                "ollama embedding backend is unavailable at "
                f"{self.base_url}/api/embed: {exc.reason}"
            ) from exc

        try:
            payload = json.loads(raw_response)
        except json.JSONDecodeError as exc:
            raise RuntimeError("ollama embedding response was not valid JSON") from exc

        embeddings = payload.get("embeddings")
        if not isinstance(embeddings, list):
            raise RuntimeError("ollama embedding response is missing 'embeddings'")
        if len(embeddings) != len(texts):
            raise RuntimeError(
                "ollama embedding response count did not match input text count"
            )

        vectors: list[list[float]] = []
        for index, vector in enumerate(embeddings):
            if not isinstance(vector, list) or not vector:
                raise RuntimeError(
                    f"ollama embedding response contained an invalid vector at index {index}"
                )
            normalized_vector: list[float] = []
            for value in vector:
                if not isinstance(value, (int, float)):
                    raise RuntimeError(
                        "ollama embedding response contained a non-numeric vector value"
                    )
                normalized_vector.append(float(value))
            vectors.append(normalized_vector)
        return vectors


def _tokenize(text: str) -> list[str]:
    normalized = text.casefold().strip()
    tokens = re.findall(r"[a-z0-9_]+", normalized)
    if tokens:
        return tokens
    return [normalized or "<empty>"]


def _l2_normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return [0.0] * len(vector)
    return [value / norm for value in vector]


def _decode_error_body(exc: urllib_error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8").strip()
    except Exception:
        body = ""
    if not body:
        return ""
    return f" ({body})"


def resolve_embedding_model_settings(
    model: str,
    *,
    max_input_tokens: int | None = None,
    chunk_overlap_tokens: int | None = None,
    aggregate: str | None = None,
) -> EmbeddingModelSettings:
    preset = OLLAMA_MODEL_PRESETS.get(model, DEFAULT_EMBEDDING_SETTINGS)
    resolved = EmbeddingModelSettings(
        max_input_tokens=max_input_tokens or preset.max_input_tokens,
        chunk_overlap_tokens=(
            chunk_overlap_tokens
            if chunk_overlap_tokens is not None
            else preset.chunk_overlap_tokens
        ),
        aggregate=aggregate or preset.aggregate,
    )
    if resolved.max_input_tokens <= 0:
        raise ValueError("max_input_tokens must be > 0")
    if resolved.chunk_overlap_tokens < 0:
        raise ValueError("chunk_overlap_tokens must be >= 0")
    if resolved.chunk_overlap_tokens >= resolved.max_input_tokens:
        raise ValueError("chunk_overlap_tokens must be smaller than max_input_tokens")
    if resolved.aggregate != "mean":
        raise ValueError("aggregate must be 'mean'")
    return resolved


def chunk_text_for_embedding(
    text: str,
    *,
    max_input_tokens: int,
    chunk_overlap_tokens: int,
) -> list[str]:
    if not text:
        return [""]

    chars = list(text)
    chunks: list[str] = []
    start = 0

    while start < len(chars):
        end = start
        budget = 0
        while end < len(chars):
            char_cost = _estimate_text_budget(chars[end])
            if budget and budget + char_cost > max_input_tokens:
                break
            budget += char_cost
            end += 1

        if end == start:
            end += 1

        chunks.append("".join(chars[start:end]))
        if end >= len(chars):
            break

        overlap_budget = 0
        overlap_start = end
        while overlap_start > start:
            char_cost = _estimate_text_budget(chars[overlap_start - 1])
            if overlap_budget + char_cost > chunk_overlap_tokens:
                break
            overlap_budget += char_cost
            overlap_start -= 1

        start = overlap_start if overlap_start < end else end

    return chunks


def aggregate_embeddings(
    vectors: list[list[float]],
    *,
    aggregate: str,
) -> list[float]:
    if not vectors:
        raise ValueError("cannot aggregate empty embedding list")
    if aggregate != "mean":
        raise ValueError("aggregate must be 'mean'")

    dimension = len(vectors[0])
    if dimension == 0:
        raise ValueError("embedding vectors must not be empty")
    totals = [0.0] * dimension

    for vector in vectors:
        if len(vector) != dimension:
            raise ValueError("embedding vectors must have the same dimension")
        for index, value in enumerate(vector):
            totals[index] += value

    count = float(len(vectors))
    return [value / count for value in totals]


def _estimate_text_budget(text: str) -> int:
    return max(1, len(text.encode("utf-8")))
