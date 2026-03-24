from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol


class EmbeddingBackend(Protocol):
    model_id: str

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text."""


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
