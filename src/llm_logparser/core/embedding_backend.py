from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from .ollama_client import OllamaClient


class EmbeddingBackend(Protocol):
    model_id: str

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text."""


@dataclass(frozen=True)
class EmbeddingModelSettings:
    max_input_bytes: int
    chunk_overlap_bytes: int
    aggregate: str = "mean"


DEFAULT_EMBEDDING_SETTINGS = EmbeddingModelSettings(
    max_input_bytes=256,
    chunk_overlap_bytes=32,
    aggregate="mean",
)

SUPPORTED_EMBEDDING_BACKENDS = ("deterministic-hash", "ollama")

# Compatibility fallback only. Normal user-facing model tuning belongs in
# config/docs rather than this registry.
OLLAMA_MODEL_COMPATIBILITY_FALLBACKS: dict[str, EmbeddingModelSettings] = {
    "nomic-embed-text-v2-moe": EmbeddingModelSettings(
        max_input_bytes=512,
        chunk_overlap_bytes=64,
        aggregate="mean",
    ),
    "embeddinggemma": EmbeddingModelSettings(
        max_input_bytes=2048,
        chunk_overlap_bytes=128,
        aggregate="mean",
    ),
}


@dataclass(frozen=True)
class OllamaBackendOptions:
    base_url: str = "http://localhost:11434"
    timeout_seconds: float = 30.0


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
        self.settings = settings or resolve_embedding_model_settings(model=self.model)
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.model_id = f"ollama/{self.model}"
        self._client = OllamaClient(
            base_url=self.base_url,
            timeout=self.timeout_seconds,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        vectors: list[list[float]] = []
        for text in texts:
            chunks = chunk_text_for_embedding(
                text,
                max_input_bytes=self.settings.max_input_bytes,
                chunk_overlap_bytes=self.settings.chunk_overlap_bytes,
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
        vectors: list[list[float]] = []
        for index, text in enumerate(texts):
            vector = self._client.embeddings(
                model=self.model,
                prompt=text,
            )
            if not vector:
                raise RuntimeError(
                    f"ollama embedding response contained an invalid vector at index {index}"
                )
            vectors.append(vector)
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


def resolve_embedding_model_settings(
    model: str | None = None,
    *,
    max_input_bytes: int | None = None,
    chunk_overlap_bytes: int | None = None,
    aggregate: str | None = None,
    default_settings: EmbeddingModelSettings | None = None,
) -> EmbeddingModelSettings:
    baseline = default_settings
    if baseline is None and model:
        baseline = OLLAMA_MODEL_COMPATIBILITY_FALLBACKS.get(model)
    if baseline is None:
        baseline = DEFAULT_EMBEDDING_SETTINGS
    resolved = EmbeddingModelSettings(
        max_input_bytes=(
            max_input_bytes
            if max_input_bytes is not None
            else baseline.max_input_bytes
        ),
        chunk_overlap_bytes=(
            chunk_overlap_bytes
            if chunk_overlap_bytes is not None
            else baseline.chunk_overlap_bytes
        ),
        aggregate=aggregate or baseline.aggregate,
    )
    if resolved.max_input_bytes <= 0:
        raise ValueError("max_input_bytes must be > 0")
    if resolved.chunk_overlap_bytes < 0:
        raise ValueError("chunk_overlap_bytes must be >= 0")
    if resolved.chunk_overlap_bytes >= resolved.max_input_bytes:
        raise ValueError("chunk_overlap_bytes must be smaller than max_input_bytes")
    if resolved.aggregate != "mean":
        raise ValueError("aggregate must be 'mean'")
    return resolved


def resolve_ollama_backend_options(
    *,
    base_url: str | None = None,
    timeout_seconds: float | None = None,
    default_options: OllamaBackendOptions | None = None,
) -> OllamaBackendOptions:
    baseline = default_options or OllamaBackendOptions()
    resolved = OllamaBackendOptions(
        base_url=(base_url or baseline.base_url).strip(),
        timeout_seconds=(
            timeout_seconds
            if timeout_seconds is not None
            else baseline.timeout_seconds
        ),
    )
    if not resolved.base_url:
        raise ValueError("ollama base_url must be a non-empty string")
    if resolved.timeout_seconds <= 0:
        raise ValueError("ollama timeout_seconds must be > 0")
    return resolved


def create_embedding_backend(
    *,
    backend_name: str,
    model: str | None = None,
    settings: EmbeddingModelSettings | None = None,
    backend_options: Mapping[str, object] | OllamaBackendOptions | None = None,
) -> EmbeddingBackend:
    if backend_name == "deterministic-hash":
        return DeterministicHashEmbeddingBackend()

    if backend_name != "ollama":
        raise ValueError(f"unsupported embedding backend: {backend_name}")

    if not model:
        raise ValueError("--backend ollama requires --model <ollama-embedding-model>")

    if isinstance(backend_options, OllamaBackendOptions):
        resolved_options = resolve_ollama_backend_options(
            base_url=backend_options.base_url,
            timeout_seconds=backend_options.timeout_seconds,
        )
    elif backend_options is None:
        resolved_options = resolve_ollama_backend_options()
    elif isinstance(backend_options, Mapping):
        resolved_options = resolve_ollama_backend_options(
            base_url=_mapping_string(backend_options, "base_url"),
            timeout_seconds=_mapping_number(backend_options, "timeout_seconds"),
        )
    else:
        raise ValueError("backend_options must be a mapping when provided")

    resolved_settings = settings or resolve_embedding_model_settings(model=model)
    return OllamaEmbeddingBackend(
        model=model,
        settings=resolved_settings,
        base_url=resolved_options.base_url,
        timeout_seconds=resolved_options.timeout_seconds,
    )


def chunk_text_for_embedding(
    text: str,
    *,
    max_input_bytes: int,
    chunk_overlap_bytes: int,
) -> list[str]:
    # This chunker is intentionally deterministic and byte-budget based.
    # It keeps the current experimental semantic layer rebuildable without
    # claiming tokenizer-accurate context budgeting.
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
            if budget and budget + char_cost > max_input_bytes:
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
            if overlap_budget + char_cost > chunk_overlap_bytes:
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
    # The embedding prototype uses UTF-8 byte length as its deterministic
    # chunk-size estimate. This is deliberate and not tokenizer-accurate.
    return max(1, len(text.encode("utf-8")))


def _mapping_string(mapping: Mapping[str, object], key: str) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"ollama backend option '{key}' must be a string")
    stripped = value.strip()
    return stripped or None


def _mapping_number(mapping: Mapping[str, object], key: str) -> float | None:
    value = mapping.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"ollama backend option '{key}' must be a number")
    return float(value)
