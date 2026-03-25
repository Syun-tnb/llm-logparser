from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .embedding_backend import (
    DeterministicHashEmbeddingBackend,
    EmbeddingBackend,
    OllamaEmbeddingBackend,
    resolve_embedding_model_settings,
)
from .i18n import _
from .schema_validation import load_message_windows_validator

EMBEDDING_SCHEMA_VERSION = "0.1"
NEIGHBORS_SCHEMA_VERSION = "0.1"
EMBEDDING_DECIMAL_PLACES = 6
SIMILARITY_DECIMAL_PLACES = 4
NEIGHBOR_PROGRESS_INTERVAL = 500


class SemanticPrototypeError(RuntimeError):
    pass


@dataclass(frozen=True)
class MessageWindowRecord:
    source_path: Path
    provider_id: str
    conversation_id: str
    window_id: str
    ts_start: int | None
    ts_end: int | None
    text: str


@dataclass(frozen=True)
class WindowEmbeddingRecord:
    source_path: Path
    provider_id: str
    conversation_id: str
    window_id: str
    ts_start: int | None
    ts_end: int | None
    embedding_model: str
    text_char_count: int
    embedding: tuple[float, ...]


def discover_message_windows_jsonl(input_path: Path) -> list[Path]:
    """Return message_windows.jsonl files from a file or directory input."""
    path = input_path.expanduser()
    if not path.exists():
        raise FileNotFoundError(f"input not found: {path}")
    if path.is_file():
        if path.name != "message_windows.jsonl":
            raise FileNotFoundError(f"expected message_windows.jsonl file: {path}")
        return [path]
    if not path.is_dir():
        raise FileNotFoundError(f"input not found: {path}")

    windows_files = sorted(path.rglob("message_windows.jsonl"))
    if not windows_files:
        raise FileNotFoundError(f"no message_windows.jsonl found under: {path}")
    return windows_files


def load_message_window_records(windows_path: Path) -> list[MessageWindowRecord]:
    validator = load_message_windows_validator()
    records: list[MessageWindowRecord] = []

    with windows_path.open("r", encoding="utf-8") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid JSON in {windows_path}:{line_no}: {exc.msg}"
                ) from exc
            if not isinstance(row, dict):
                raise ValueError(
                    f"invalid record in {windows_path}:{line_no}: expected object"
                )

            errors = list(validator.iter_errors(row))
            if errors:
                raise ValueError(
                    f"message window schema validation failed for "
                    f"{windows_path}:{line_no}: {errors[0].message}"
                )

            records.append(
                MessageWindowRecord(
                    source_path=windows_path,
                    provider_id=row["provider_id"],
                    conversation_id=row["conversation_id"],
                    window_id=row["window_id"],
                    ts_start=row.get("ts_start"),
                    ts_end=row.get("ts_end"),
                    text=row["text"],
                )
            )

    return records


def build_window_embedding_records(
    windows: list[MessageWindowRecord],
    *,
    backend: EmbeddingBackend,
) -> list[WindowEmbeddingRecord]:
    try:
        vectors = backend.embed([window.text for window in windows])
    except Exception as exc:
        raise SemanticPrototypeError(
            f"embedding backend '{backend.model_id}' failed: {exc}"
        ) from exc
    if len(vectors) != len(windows):
        raise SemanticPrototypeError(
            "embedding backend returned a different vector count than input texts"
        )

    records: list[WindowEmbeddingRecord] = []
    expected_dim: int | None = None

    for window, vector in zip(windows, vectors, strict=True):
        if expected_dim is None:
            expected_dim = len(vector)
        elif len(vector) != expected_dim:
            raise SemanticPrototypeError("embedding backend returned mixed dimensions")

        records.append(
            WindowEmbeddingRecord(
                source_path=window.source_path,
                provider_id=window.provider_id,
                conversation_id=window.conversation_id,
                window_id=window.window_id,
                ts_start=window.ts_start,
                ts_end=window.ts_end,
                embedding_model=backend.model_id,
                text_char_count=len(window.text),
                embedding=tuple(round(value, EMBEDDING_DECIMAL_PLACES) for value in vector),
            )
        )

    return records


def render_window_embedding_row(record: WindowEmbeddingRecord) -> dict[str, Any]:
    return {
        "record_type": "window_embedding",
        "schema_version": EMBEDDING_SCHEMA_VERSION,
        "provider_id": record.provider_id,
        "conversation_id": record.conversation_id,
        "window_id": record.window_id,
        "ts_start": record.ts_start,
        "ts_end": record.ts_end,
        "embedding_model": record.embedding_model,
        "embedding_dim": len(record.embedding),
        "text_char_count": record.text_char_count,
        "embedding": list(record.embedding),
    }


def cosine_similarity(left: list[float] | tuple[float, ...], right: list[float] | tuple[float, ...]) -> float:
    if len(left) != len(right):
        raise ValueError("cosine similarity requires equal vector dimensions")

    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0

    dot_product = sum(left_value * right_value for left_value, right_value in zip(left, right, strict=True))
    return dot_product / (left_norm * right_norm)


def _emit_progress(
    progress: Callable[[str], None] | None,
    key: str,
    **kwargs: Any,
) -> None:
    if progress is None:
        return
    progress(_(key, **kwargs))


def _normalized_embedding_matrix(embeddings: list[WindowEmbeddingRecord]) -> np.ndarray:
    if not embeddings:
        return np.zeros((0, 0), dtype=np.float64)

    matrix = np.asarray([record.embedding for record in embeddings], dtype=np.float64)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return np.divide(
        matrix,
        norms,
        out=np.zeros_like(matrix),
        where=norms != 0.0,
    )


def build_window_neighbor_rows(
    embeddings: list[WindowEmbeddingRecord],
    *,
    top_k: int,
    progress: Callable[[str], None] | None = None,
    progress_every: int | None = None,
) -> list[dict[str, Any]]:
    if top_k <= 0:
        raise ValueError("top_k must be > 0")
    if progress_every is not None and progress_every <= 0:
        raise ValueError("progress_every must be > 0")

    if not embeddings:
        return []

    rows: list[dict[str, Any]] = []
    total = len(embeddings)
    effective_top_k = min(top_k, max(0, total - 1))
    provider_ids = np.asarray([record.provider_id for record in embeddings], dtype=object)
    conversation_ids = np.asarray(
        [record.conversation_id for record in embeddings],
        dtype=object,
    )
    window_ids = np.asarray([record.window_id for record in embeddings], dtype=object)

    normalized = _normalized_embedding_matrix(embeddings)
    similarity = normalized @ normalized.T
    np.fill_diagonal(similarity, -np.inf)

    for index, record in enumerate(embeddings):
        top_neighbors: list[dict[str, Any]] = []
        if effective_top_k > 0:
            row_scores = similarity[index]
            cutoff = np.partition(row_scores, total - effective_top_k)[
                total - effective_top_k
            ]
            candidate_indices = np.flatnonzero(row_scores >= cutoff)
            ordered_candidate_indices = candidate_indices[
                np.lexsort(
                    (
                        window_ids[candidate_indices],
                        conversation_ids[candidate_indices],
                        provider_ids[candidate_indices],
                        -row_scores[candidate_indices],
                    )
                )
            ]

            for candidate_index in ordered_candidate_indices[:effective_top_k]:
                top_neighbors.append(
                    {
                        "provider_id": embeddings[candidate_index].provider_id,
                        "conversation_id": embeddings[candidate_index].conversation_id,
                        "window_id": embeddings[candidate_index].window_id,
                        "score": round(
                            float(row_scores[candidate_index]),
                            SIMILARITY_DECIMAL_PLACES,
                        ),
                    }
                )

        rows.append(
            {
                "record_type": "window_neighbors",
                "schema_version": NEIGHBORS_SCHEMA_VERSION,
                "provider_id": record.provider_id,
                "conversation_id": record.conversation_id,
                "window_id": record.window_id,
                "embedding_model": record.embedding_model,
                "neighbor_count": len(top_neighbors),
                "neighbors": top_neighbors,
            }
        )
        if progress_every is not None and (
            (index + 1) % progress_every == 0 or index + 1 == total
        ):
            _emit_progress(
                progress,
                "runtime.analyze.semantic_prototype.neighbor_progress",
                count=index + 1,
                total=total,
            )

    return rows


def window_embeddings_artifact_path(windows_path: Path) -> Path:
    return windows_path.with_name("window_embeddings.jsonl")


def window_neighbors_artifact_path(windows_path: Path) -> Path:
    return windows_path.with_name("window_neighbors.jsonl")


def _write_jsonl_rows(path: Path, rows: list[dict[str, Any]]) -> Path:
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")
    tmp.replace(path)
    return path


def analyze_semantic_prototype(
    input_path: Path,
    *,
    top_k: int = 5,
    overwrite: bool = False,
    backend_name: str = "deterministic-hash",
    model: str | None = None,
    max_input_bytes: int | None = None,
    chunk_overlap_bytes: int | None = None,
    aggregate: str | None = None,
    backend: EmbeddingBackend | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if top_k <= 0:
        raise SemanticPrototypeError("top_k must be > 0")

    windows_files = discover_message_windows_jsonl(input_path)
    embedding_paths = [window_embeddings_artifact_path(path) for path in windows_files]
    neighbor_paths = [window_neighbors_artifact_path(path) for path in windows_files]

    if not overwrite:
        for artifact_path in [*embedding_paths, *neighbor_paths]:
            if artifact_path.exists():
                raise SemanticPrototypeError(
                    f"artifact already exists: {artifact_path} (rerun with --overwrite)"
                )

    if backend is None:
        backend = resolve_embedding_backend(
            backend_name=backend_name,
            model=model,
            max_input_bytes=max_input_bytes,
            chunk_overlap_bytes=chunk_overlap_bytes,
            aggregate=aggregate,
        )

    _emit_progress(progress, "runtime.analyze.semantic_prototype.loading_windows")
    all_windows: list[MessageWindowRecord] = []
    for windows_path in windows_files:
        all_windows.extend(load_message_window_records(windows_path))
    _emit_progress(
        progress,
        "runtime.analyze.semantic_prototype.loaded_windows",
        windows=len(all_windows),
    )

    _emit_progress(progress, "runtime.analyze.semantic_prototype.generating_embeddings")
    embeddings = build_window_embedding_records(all_windows, backend=backend)
    _emit_progress(
        progress,
        "runtime.analyze.semantic_prototype.embeddings_complete",
        windows=len(embeddings),
    )
    embedding_rows = [render_window_embedding_row(record) for record in embeddings]
    _emit_progress(progress, "runtime.analyze.semantic_prototype.building_neighbors")
    neighbor_rows = build_window_neighbor_rows(
        embeddings,
        top_k=top_k,
        progress=progress,
        progress_every=NEIGHBOR_PROGRESS_INTERVAL if len(embeddings) >= NEIGHBOR_PROGRESS_INTERVAL else None,
    )

    rows_by_source: dict[Path, dict[str, list[dict[str, Any]]]] = {
        path: {"embeddings": [], "neighbors": []} for path in windows_files
    }

    for record, row in zip(embeddings, embedding_rows, strict=True):
        rows_by_source[record.source_path]["embeddings"].append(row)

    for record, row in zip(embeddings, neighbor_rows, strict=True):
        rows_by_source[record.source_path]["neighbors"].append(row)

    written_embeddings: list[Path] = []
    written_neighbors: list[Path] = []

    _emit_progress(progress, "runtime.analyze.semantic_prototype.writing_artifacts")
    for windows_path in windows_files:
        written_embeddings.append(
            _write_jsonl_rows(
                window_embeddings_artifact_path(windows_path),
                rows_by_source[windows_path]["embeddings"],
            )
        )
        written_neighbors.append(
            _write_jsonl_rows(
                window_neighbors_artifact_path(windows_path),
                rows_by_source[windows_path]["neighbors"],
            )
        )

    return {
        "threads": len(windows_files),
        "windows": len(all_windows),
        "embedding_model": backend.model_id,
        "embedding_artifacts": written_embeddings,
        "neighbor_artifacts": written_neighbors,
    }


def resolve_embedding_backend(
    *,
    backend_name: str,
    model: str | None,
    max_input_bytes: int | None = None,
    chunk_overlap_bytes: int | None = None,
    aggregate: str | None = None,
) -> EmbeddingBackend:
    if backend_name == "deterministic-hash":
        return DeterministicHashEmbeddingBackend()
    if backend_name == "ollama":
        if not model:
            raise SemanticPrototypeError(
                "--backend ollama requires --model <ollama-embedding-model>"
            )
        try:
            settings = resolve_embedding_model_settings(
                model,
                max_input_bytes=max_input_bytes,
                chunk_overlap_bytes=chunk_overlap_bytes,
                aggregate=aggregate,
            )
            return OllamaEmbeddingBackend(model=model, settings=settings)
        except ValueError as exc:
            raise SemanticPrototypeError(str(exc)) from exc
    raise SemanticPrototypeError(f"unsupported embedding backend: {backend_name}")
