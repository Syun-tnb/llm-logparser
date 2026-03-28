from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .embedding_backend import (
    EmbeddingBackend,
    create_embedding_backend,
    resolve_embedding_model_settings,
)
from .i18n import _
from .schema_validation import load_message_windows_validator

EMBEDDING_SCHEMA_VERSION = "0.1"
NEIGHBORS_SCHEMA_VERSION = "0.1"
CLUSTERS_SCHEMA_VERSION = "0.1"
EMBEDDING_DECIMAL_PLACES = 6
SIMILARITY_DECIMAL_PLACES = 4
NEIGHBOR_PROGRESS_INTERVAL = 500
DEFAULT_MIN_SCORE = 0.0
DEFAULT_CANDIDATE_WINDOW_DAYS = 30
DEFAULT_CANDIDATE_MIN_CHARS = 0
DEFAULT_CANDIDATE_MIN_ASSISTANT_RATIO = 0.0
DEFAULT_CANDIDATE_SAME_THREAD = "allow"
SUPPORTED_SAME_THREAD_POLICIES = frozenset(
    {"allow", "prefer", "only", "exclude"}
)
CLUSTER_EDGE_POLICY = "mutual-only"


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


@dataclass(frozen=True)
class SqliteCandidateConfig:
    db_path: Path
    candidate_window_days: int = DEFAULT_CANDIDATE_WINDOW_DAYS
    candidate_min_chars: int = DEFAULT_CANDIDATE_MIN_CHARS
    candidate_min_assistant_ratio: float = DEFAULT_CANDIDATE_MIN_ASSISTANT_RATIO
    candidate_same_thread: str = DEFAULT_CANDIDATE_SAME_THREAD


WindowKey = tuple[str, str, str]


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


def _window_key(record: MessageWindowRecord | WindowEmbeddingRecord) -> WindowKey:
    return (record.provider_id, record.conversation_id, record.window_id)


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


def _embedding_lookup(
    embeddings: list[WindowEmbeddingRecord],
) -> tuple[dict[WindowKey, int], np.ndarray]:
    return (
        {_window_key(record): index for index, record in enumerate(embeddings)},
        _normalized_embedding_matrix(embeddings),
    )


def _ordered_neighbor_indices(
    candidate_indices: np.ndarray,
    scores: np.ndarray,
    embeddings: list[WindowEmbeddingRecord],
    *,
    top_k: int,
    min_score: float,
    target_conversation_id: str,
    prefer_same_thread: bool,
) -> list[int]:
    if candidate_indices.size == 0:
        return []

    keep_mask = scores >= min_score
    if not np.any(keep_mask):
        return []

    filtered_indices = candidate_indices[keep_mask]
    filtered_scores = scores[keep_mask]
    provider_ids = np.asarray(
        [embeddings[index].provider_id for index in filtered_indices],
        dtype=object,
    )
    conversation_ids = np.asarray(
        [embeddings[index].conversation_id for index in filtered_indices],
        dtype=object,
    )
    window_ids = np.asarray(
        [embeddings[index].window_id for index in filtered_indices],
        dtype=object,
    )

    sort_keys: list[np.ndarray] = [window_ids, conversation_ids, provider_ids]
    if prefer_same_thread:
        same_thread_rank = np.asarray(
            [
                0 if conversation_id == target_conversation_id else 1
                for conversation_id in conversation_ids
            ],
            dtype=np.int64,
        )
        sort_keys.append(same_thread_rank)
    sort_keys.append(-filtered_scores)

    ordered = filtered_indices[np.lexsort(tuple(sort_keys))]
    return ordered[:top_k].tolist()


def _neighbor_row_from_indices(
    record: WindowEmbeddingRecord,
    embeddings: list[WindowEmbeddingRecord],
    ordered_indices: list[int],
    score_lookup: dict[int, float],
) -> dict[str, Any]:
    neighbors = [
        {
            "provider_id": embeddings[index].provider_id,
            "conversation_id": embeddings[index].conversation_id,
            "window_id": embeddings[index].window_id,
            "score": round(score_lookup[index], SIMILARITY_DECIMAL_PLACES),
        }
        for index in ordered_indices
    ]
    return {
        "record_type": "window_neighbors",
        "schema_version": NEIGHBORS_SCHEMA_VERSION,
        "provider_id": record.provider_id,
        "conversation_id": record.conversation_id,
        "window_id": record.window_id,
        "embedding_model": record.embedding_model,
        "neighbor_count": len(neighbors),
        "neighbors": neighbors,
    }


def _validate_min_score(min_score: float) -> None:
    if not -1.0 <= min_score <= 1.0:
        raise ValueError("min_score must be between -1.0 and 1.0")


def build_window_neighbor_rows(
    embeddings: list[WindowEmbeddingRecord],
    *,
    top_k: int,
    min_score: float = DEFAULT_MIN_SCORE,
    progress: Callable[[str], None] | None = None,
    progress_every: int | None = None,
) -> list[dict[str, Any]]:
    if top_k <= 0:
        raise ValueError("top_k must be > 0")
    _validate_min_score(min_score)
    if progress_every is not None and progress_every <= 0:
        raise ValueError("progress_every must be > 0")

    if not embeddings:
        return []

    rows: list[dict[str, Any]] = []
    total = len(embeddings)
    effective_top_k = min(top_k, max(0, total - 1))

    normalized = _normalized_embedding_matrix(embeddings)
    similarity = normalized @ normalized.T
    np.fill_diagonal(similarity, -np.inf)

    for index, record in enumerate(embeddings):
        ordered_indices: list[int] = []
        score_lookup: dict[int, float] = {}
        if effective_top_k > 0:
            row_scores = similarity[index]
            candidate_indices = np.flatnonzero(np.isfinite(row_scores))
            score_lookup = {
                int(candidate_index): float(row_scores[candidate_index])
                for candidate_index in candidate_indices
            }
            ordered_indices = _ordered_neighbor_indices(
                candidate_indices,
                row_scores[candidate_indices],
                embeddings,
                top_k=effective_top_k,
                min_score=min_score,
                target_conversation_id=record.conversation_id,
                prefer_same_thread=False,
            )

        rows.append(
            _neighbor_row_from_indices(
                record,
                embeddings,
                ordered_indices,
                score_lookup,
            )
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


def _validate_sqlite_candidate_config(config: SqliteCandidateConfig) -> Path:
    db_path = config.db_path.expanduser()
    if not db_path.exists():
        raise SemanticPrototypeError(f"sqlite db not found: {db_path}")
    if not db_path.is_file():
        raise SemanticPrototypeError(f"sqlite db must be a file: {db_path}")
    if config.candidate_window_days < 0:
        raise SemanticPrototypeError("candidate_window_days must be >= 0")
    if config.candidate_min_chars < 0:
        raise SemanticPrototypeError("candidate_min_chars must be >= 0")
    if not 0.0 <= config.candidate_min_assistant_ratio <= 1.0:
        raise SemanticPrototypeError(
            "candidate_min_assistant_ratio must be between 0.0 and 1.0"
        )
    if config.candidate_same_thread not in SUPPORTED_SAME_THREAD_POLICIES:
        raise SemanticPrototypeError(
            "candidate_same_thread must be one of: "
            + ", ".join(sorted(SUPPORTED_SAME_THREAD_POLICIES))
        )
    return db_path


def _candidate_query(
    record: WindowEmbeddingRecord,
    *,
    config: SqliteCandidateConfig,
) -> tuple[str, list[Any]]:
    clauses = ["mw.provider_id = ?", "NOT (mw.provider_id = ? AND mw.conversation_id = ? AND mw.window_id = ?)"]
    params: list[Any] = [
        record.provider_id,
        record.provider_id,
        record.conversation_id,
        record.window_id,
    ]

    if record.ts_start is not None:
        days_ms = config.candidate_window_days * 24 * 60 * 60 * 1000
        clauses.append("mw.ts_start IS NOT NULL")
        clauses.append("mw.ts_start BETWEEN ? AND ?")
        params.extend([record.ts_start - days_ms, record.ts_start + days_ms])

    if config.candidate_min_chars > 0:
        clauses.append("COALESCE(mw.char_count, 0) >= ?")
        params.append(config.candidate_min_chars)

    if config.candidate_min_assistant_ratio > 0.0:
        clauses.append("COALESCE(t.message_count, 0) > 0")
        clauses.append(
            "(CAST(COALESCE(t.assistant_messages, 0) AS REAL) / "
            "CAST(t.message_count AS REAL)) >= ?"
        )
        params.append(config.candidate_min_assistant_ratio)

    if config.candidate_same_thread == "only":
        clauses.append("mw.conversation_id = ?")
        params.append(record.conversation_id)
    elif config.candidate_same_thread == "exclude":
        clauses.append("mw.conversation_id != ?")
        params.append(record.conversation_id)

    order_by = [
        "mw.provider_id ASC",
        "mw.conversation_id ASC",
        "mw.window_id ASC",
    ]
    if record.ts_start is not None:
        order_by.insert(0, "ABS(mw.ts_start - ?) ASC")
        params.append(record.ts_start)
    elif config.candidate_same_thread == "prefer":
        order_by.insert(0, "(mw.conversation_id != ?) ASC")
        params.append(record.conversation_id)

    query = f"""
        SELECT mw.provider_id, mw.conversation_id, mw.window_id
        FROM message_windows AS mw
        JOIN threads AS t
          ON t.conversation_id = mw.conversation_id
        WHERE {" AND ".join(clauses)}
        ORDER BY {", ".join(order_by)}
    """
    return query, params


def build_window_neighbor_rows_with_sqlite_candidates(
    embeddings: list[WindowEmbeddingRecord],
    *,
    top_k: int,
    min_score: float,
    candidate_config: SqliteCandidateConfig,
    progress: Callable[[str], None] | None = None,
    progress_every: int | None = None,
) -> list[dict[str, Any]]:
    if top_k <= 0:
        raise ValueError("top_k must be > 0")
    _validate_min_score(min_score)
    if progress_every is not None and progress_every <= 0:
        raise ValueError("progress_every must be > 0")
    if not embeddings:
        return []

    db_path = _validate_sqlite_candidate_config(candidate_config)
    index_by_key, normalized = _embedding_lookup(embeddings)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows: list[dict[str, Any]] = []
        total = len(embeddings)
        effective_top_k = min(top_k, max(0, total - 1))
        prefer_same_thread = candidate_config.candidate_same_thread == "prefer"

        for index, record in enumerate(embeddings):
            ordered_indices: list[int] = []
            score_lookup: dict[int, float] = {}
            if effective_top_k > 0:
                query, params = _candidate_query(record, config=candidate_config)
                candidate_indices: list[int] = []
                for row in conn.execute(query, params):
                    key = (row["provider_id"], row["conversation_id"], row["window_id"])
                    candidate_index = index_by_key.get(key)
                    if candidate_index is None or candidate_index == index:
                        continue
                    candidate_indices.append(candidate_index)

                if candidate_indices:
                    candidate_index_array = np.asarray(candidate_indices, dtype=np.int64)
                    candidate_scores = normalized[candidate_index_array] @ normalized[index]
                    score_lookup = {
                        int(candidate_index): float(score)
                        for candidate_index, score in zip(
                            candidate_index_array,
                            candidate_scores,
                            strict=True,
                        )
                    }
                    ordered_indices = _ordered_neighbor_indices(
                        candidate_index_array,
                        candidate_scores,
                        embeddings,
                        top_k=effective_top_k,
                        min_score=min_score,
                        target_conversation_id=record.conversation_id,
                        prefer_same_thread=prefer_same_thread,
                    )

            rows.append(
                _neighbor_row_from_indices(
                    record,
                    embeddings,
                    ordered_indices,
                    score_lookup,
                )
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
    finally:
        conn.close()


def window_embeddings_artifact_path(windows_path: Path) -> Path:
    return windows_path.with_name("window_embeddings.jsonl")


def window_neighbors_artifact_path(windows_path: Path) -> Path:
    return windows_path.with_name("window_neighbors.jsonl")


def window_clusters_artifact_path(windows_path: Path) -> Path:
    return windows_path.with_name("window_clusters.jsonl")


def build_window_cluster_rows(
    embeddings: list[WindowEmbeddingRecord],
    neighbor_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if len(embeddings) != len(neighbor_rows):
        raise ValueError("embeddings and neighbor_rows must have the same length")
    if not embeddings:
        return []

    nodes = [_window_key(record) for record in embeddings]
    node_set = set(nodes)
    neighbor_map: dict[WindowKey, set[WindowKey]] = {node: set() for node in nodes}
    for row in neighbor_rows:
        source = (row["provider_id"], row["conversation_id"], row["window_id"])
        for neighbor in row["neighbors"]:
            target = (
                neighbor["provider_id"],
                neighbor["conversation_id"],
                neighbor["window_id"],
            )
            if target in node_set:
                neighbor_map[source].add(target)

    adjacency: dict[WindowKey, set[WindowKey]] = {node: set() for node in nodes}
    for source in nodes:
        for target in sorted(neighbor_map[source]):
            if source in neighbor_map.get(target, set()):
                adjacency[source].add(target)
                adjacency[target].add(source)

    visited: set[WindowKey] = set()
    components: list[list[WindowKey]] = []
    for node in sorted(nodes):
        if node in visited:
            continue
        stack = [node]
        visited.add(node)
        component: list[WindowKey] = []
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbor in sorted(adjacency[current], reverse=True):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                stack.append(neighbor)
        components.append(sorted(component))

    cluster_rows_by_key: dict[WindowKey, dict[str, Any]] = {}
    for cluster_index, component in enumerate(components, start=1):
        cluster_id = f"cluster_{cluster_index:06d}"
        cluster_size = len(component)
        for provider_id, conversation_id, window_id in component:
            cluster_rows_by_key[(provider_id, conversation_id, window_id)] = {
                "record_type": "window_cluster_member",
                "schema_version": CLUSTERS_SCHEMA_VERSION,
                "provider_id": provider_id,
                "conversation_id": conversation_id,
                "window_id": window_id,
                "cluster_id": cluster_id,
                "cluster_size": cluster_size,
                "edge_policy": CLUSTER_EDGE_POLICY,
            }

    return [cluster_rows_by_key[_window_key(record)] for record in embeddings]


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
    min_score: float = DEFAULT_MIN_SCORE,
    overwrite: bool = False,
    backend_name: str = "deterministic-hash",
    model: str | None = None,
    max_input_bytes: int | None = None,
    chunk_overlap_bytes: int | None = None,
    aggregate: str | None = None,
    sqlite_db: Path | None = None,
    candidate_window_days: int = DEFAULT_CANDIDATE_WINDOW_DAYS,
    candidate_min_chars: int = DEFAULT_CANDIDATE_MIN_CHARS,
    candidate_min_assistant_ratio: float = DEFAULT_CANDIDATE_MIN_ASSISTANT_RATIO,
    candidate_same_thread: str = DEFAULT_CANDIDATE_SAME_THREAD,
    backend_options: dict[str, Any] | None = None,
    backend: EmbeddingBackend | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if top_k <= 0:
        raise SemanticPrototypeError("top_k must be > 0")
    try:
        _validate_min_score(min_score)
    except ValueError as exc:
        raise SemanticPrototypeError(str(exc)) from exc

    windows_files = discover_message_windows_jsonl(input_path)
    embedding_paths = [window_embeddings_artifact_path(path) for path in windows_files]
    neighbor_paths = [window_neighbors_artifact_path(path) for path in windows_files]
    cluster_paths = [window_clusters_artifact_path(path) for path in windows_files]

    if not overwrite:
        for artifact_path in [*embedding_paths, *neighbor_paths, *cluster_paths]:
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
            backend_options=backend_options,
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
    if sqlite_db is None:
        neighbor_rows = build_window_neighbor_rows(
            embeddings,
            top_k=top_k,
            min_score=min_score,
            progress=progress,
            progress_every=NEIGHBOR_PROGRESS_INTERVAL
            if len(embeddings) >= NEIGHBOR_PROGRESS_INTERVAL
            else None,
        )
    else:
        neighbor_rows = build_window_neighbor_rows_with_sqlite_candidates(
            embeddings,
            top_k=top_k,
            min_score=min_score,
            candidate_config=SqliteCandidateConfig(
                db_path=sqlite_db,
                candidate_window_days=candidate_window_days,
                candidate_min_chars=candidate_min_chars,
                candidate_min_assistant_ratio=candidate_min_assistant_ratio,
                candidate_same_thread=candidate_same_thread,
            ),
            progress=progress,
            progress_every=NEIGHBOR_PROGRESS_INTERVAL
            if len(embeddings) >= NEIGHBOR_PROGRESS_INTERVAL
            else None,
        )
    _emit_progress(progress, "runtime.analyze.semantic_prototype.building_clusters")
    cluster_rows = build_window_cluster_rows(embeddings, neighbor_rows)

    rows_by_source: dict[Path, dict[str, list[dict[str, Any]]]] = {
        path: {"embeddings": [], "neighbors": [], "clusters": []}
        for path in windows_files
    }

    for record, row in zip(embeddings, embedding_rows, strict=True):
        rows_by_source[record.source_path]["embeddings"].append(row)

    for record, row in zip(embeddings, neighbor_rows, strict=True):
        rows_by_source[record.source_path]["neighbors"].append(row)

    for record, row in zip(embeddings, cluster_rows, strict=True):
        rows_by_source[record.source_path]["clusters"].append(row)

    written_embeddings: list[Path] = []
    written_neighbors: list[Path] = []
    written_clusters: list[Path] = []

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
        written_clusters.append(
            _write_jsonl_rows(
                window_clusters_artifact_path(windows_path),
                rows_by_source[windows_path]["clusters"],
            )
        )

    return {
        "threads": len(windows_files),
        "windows": len(all_windows),
        "embedding_model": backend.model_id,
        "embedding_artifacts": written_embeddings,
        "neighbor_artifacts": written_neighbors,
        "cluster_artifacts": written_clusters,
    }


def resolve_embedding_backend(
    *,
    backend_name: str,
    model: str | None,
    max_input_bytes: int | None = None,
    chunk_overlap_bytes: int | None = None,
    aggregate: str | None = None,
    backend_options: dict[str, Any] | None = None,
) -> EmbeddingBackend:
    try:
        settings = None
        if backend_name == "ollama":
            settings = resolve_embedding_model_settings(
                model=model,
                max_input_bytes=max_input_bytes,
                chunk_overlap_bytes=chunk_overlap_bytes,
                aggregate=aggregate,
            )
        return create_embedding_backend(
            backend_name=backend_name,
            model=model,
            settings=settings,
            backend_options=backend_options,
        )
    except ValueError as exc:
        raise SemanticPrototypeError(str(exc)) from exc
