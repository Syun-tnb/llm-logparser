from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from unittest.mock import call, patch

import pytest

from llm_logparser.cli.cli import main
from llm_logparser.core.analyzer_semantic_prototype import (
    CandidateProviderSelection,
    MessageWindowRecord,
    SemanticStructureResult,
    SqliteCandidateConfig,
    WindowEmbeddingRecord,
    analyze_semantic_prototype,
    build_full_scan_candidate_pools,
    build_sqlite_candidate_pools,
    build_window_embedding_records,
    build_window_cluster_rows,
    build_window_neighbor_rows,
    build_window_neighbor_rows_with_sqlite_candidates,
    compute_semantic_structure,
    compute_semantic_structure_from_provider,
    cosine_similarity,
    discover_semantic_prototype_inputs,
    discover_message_windows_jsonl,
    load_message_window_records,
    render_window_embedding_row,
    resolve_embedding_backend,
)
from llm_logparser.core.embedding_backend import (
    DEFAULT_EMBEDDING_SETTINGS,
    DeterministicHashEmbeddingBackend,
    OllamaEmbeddingBackend,
    aggregate_embeddings,
    chunk_text_for_embedding,
    create_embedding_backend,
    resolve_embedding_model_settings,
)
from llm_logparser.core.message_windows import iter_message_windows_from_rows
from llm_logparser.core.schema_validation import (
    load_window_embedding_validator,
    load_window_clusters_validator,
    load_window_neighbors_validator,
)
from llm_logparser.core.i18n import set_locale
from llm_logparser.l2_sqlite.schema import create_schema, insert_metadata


def _window_row(
    provider_id: str,
    conversation_id: str,
    window_id: str,
    text: str,
    *,
    ts_start: int | None = None,
    ts_end: int | None = None,
) -> dict:
    message_id = f"{window_id}-m1"
    return {
        "record_type": "message_window",
        "schema_version": "3.0",
        "provider_id": provider_id,
        "conversation_id": conversation_id,
        "window_id": window_id,
        "message_ids": [message_id],
        "char_count": len(text),
        "ts_start": ts_start,
        "ts_end": ts_end,
        "window_size": 1,
        "window_stride": 1,
        "__parsed_messages": [
            {
                "record_type": "message",
                "provider_id": provider_id,
                "conversation_id": conversation_id,
                "message_id": message_id,
                "role": "user",
                "ts": ts_start,
                "text": text,
                "content": {"content_type": "text", "parts": [text]},
            }
        ],
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.name == "message_windows.jsonl":
        parsed_rows: list[dict] = []
        seen_message_ids: set[str] = set()
        clean_rows: list[dict] = []
        for row in rows:
            clean_rows.append({key: value for key, value in row.items() if not key.startswith("__")})
            for message in row.get("__parsed_messages", []):
                message_id = message["message_id"]
                if message_id in seen_message_ids:
                    continue
                seen_message_ids.add(message_id)
                parsed_rows.append(message)
        with path.open("w", encoding="utf-8") as handle:
            for row in clean_rows:
                handle.write(json.dumps(row, ensure_ascii=True) + "\n")
        if clean_rows:
            parsed_path = path.with_name("parsed.jsonl")
            thread_row = {
                "record_type": "thread",
                "provider_id": clean_rows[0]["provider_id"],
                "conversation_id": clean_rows[0]["conversation_id"],
                "message_count": len(parsed_rows),
            }
            with parsed_path.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps(thread_row, ensure_ascii=True) + "\n")
                for row in parsed_rows:
                    handle.write(json.dumps(row, ensure_ascii=True) + "\n")
        return
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def _write_parsed_jsonl(
    path: Path,
    *,
    provider_id: str,
    conversation_id: str,
    messages: list[dict],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "record_type": "thread",
                    "provider_id": provider_id,
                    "conversation_id": conversation_id,
                    "message_count": len(messages),
                },
                ensure_ascii=True,
            )
            + "\n"
        )
        for row in messages:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def _write_candidate_db(
    db_path: Path,
    *,
    provider_id: str,
    thread_rows: list[dict],
    window_rows: list[dict],
) -> Path:
    conn = sqlite3.connect(db_path)
    try:
        create_schema(conn)
        insert_metadata(conn, provider_id=provider_id)
        conn.executemany(
            """
            INSERT INTO threads (
                provider_id,
                conversation_id,
                message_count,
                user_messages,
                assistant_messages,
                other_roles,
                character_count,
                characters_total,
                characters_user,
                characters_assistant,
                other_role_breakdown,
                first_timestamp,
                last_timestamp,
                conversation_span_seconds
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["provider_id"],
                    row["conversation_id"],
                    row["message_count"],
                    row.get("user_messages"),
                    row.get("assistant_messages"),
                    row.get("other_roles"),
                    row.get("character_count"),
                    row.get("characters_total"),
                    row.get("characters_user"),
                    row.get("characters_assistant"),
                    row.get("other_role_breakdown"),
                    row.get("first_timestamp"),
                    row.get("last_timestamp"),
                    row.get("conversation_span_seconds"),
                )
                for row in thread_rows
            ],
        )
        conn.executemany(
            """
            INSERT INTO message_windows (
                provider_id,
                conversation_id,
                window_id,
                message_ids,
                char_count,
                ts_start,
                ts_end,
                window_size,
                window_stride
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["provider_id"],
                    row["conversation_id"],
                    row["window_id"],
                    json.dumps(row.get("message_ids", []), ensure_ascii=True),
                    row.get("char_count"),
                    row.get("ts_start"),
                    row.get("ts_end"),
                    row.get("window_size"),
                    row.get("window_stride"),
                )
                for row in window_rows
            ],
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


def _normalize_message_window_records(
    records: list[MessageWindowRecord],
) -> list[dict[str, object]]:
    return [
        {
            "provider_id": record.provider_id,
            "conversation_id": record.conversation_id,
            "window_id": record.window_id,
            "message_ids": record.message_ids,
            "span_id": record.span_id,
            "ts_start": record.ts_start,
            "ts_end": record.ts_end,
            "text": record.text,
        }
        for record in records
    ]


def _normalize_embedding_records(
    records: list[WindowEmbeddingRecord],
) -> list[dict[str, object]]:
    return [
        {
            "provider_id": record.provider_id,
            "conversation_id": record.conversation_id,
            "window_id": record.window_id,
            "message_ids": record.message_ids,
            "span_id": record.span_id,
            "ts_start": record.ts_start,
            "ts_end": record.ts_end,
            "embedding_model": record.embedding_model,
            "text_char_count": record.text_char_count,
            "embedding": record.embedding,
        }
        for record in records
    ]


def _normalize_semantic_structure(
    result: SemanticStructureResult,
    embeddings: list[WindowEmbeddingRecord],
) -> dict[str, object]:
    span_by_window_key = {
        (record.provider_id, record.conversation_id, record.window_id): record.span_id
        for record in embeddings
    }
    normalized_neighbors = {
        span_by_window_key[(row["provider_id"], row["conversation_id"], row["window_id"])]: [
            (
                span_by_window_key[
                    (
                        neighbor["provider_id"],
                        neighbor["conversation_id"],
                        neighbor["window_id"],
                    )
                ],
                neighbor["score"],
            )
            for neighbor in row["neighbors"]
        ]
        for row in result.neighbor_rows
    }
    cluster_members_by_id: dict[str, list[str]] = {}
    for row in result.cluster_rows:
        span_id = span_by_window_key[
            (row["provider_id"], row["conversation_id"], row["window_id"])
        ]
        cluster_members_by_id.setdefault(row["cluster_id"], []).append(span_id)
    normalized_clusters = sorted(
        tuple(sorted(span_ids)) for span_ids in cluster_members_by_id.values()
    )
    return {
        "neighbors": normalized_neighbors,
        "clusters": normalized_clusters,
    }


class MockEmbeddingBackend:
    model_id = "local/test-backend"

    def embed(self, texts: list[str]) -> list[list[float]]:
        assert texts == ["alpha beta", "beta gamma"]
        return [[1.0, 0.0], [0.0, 1.0]]


class StaticEmbeddingBackend:
    def __init__(self, vectors: list[list[float]], *, model_id: str = "local/test-static"):
        self.model_id = model_id
        self._vectors = vectors

    def embed(self, texts: list[str]) -> list[list[float]]:
        assert len(texts) == len(self._vectors)
        return self._vectors


class KeywordCountEmbeddingBackend:
    model_id = "local/test-keyword-count"

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            lowered = text.lower()
            vectors.append(
                [
                    float(lowered.count("beta")),
                    float(lowered.count("alpha")),
                ]
            )
        return vectors


class _FakeHTTPResponse:
    def __init__(self, payload: object):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_discover_and_load_message_window_records(tmp_path):
    windows_path = tmp_path / "openai" / "thread-conv-a" / "message_windows.jsonl"
    _write_jsonl(
        windows_path,
        [
            _window_row("openai", "conv-a", "window-0001", "alpha beta", ts_start=1, ts_end=2),
            _window_row("openai", "conv-a", "window-0002", "beta gamma", ts_start=3, ts_end=4),
        ],
    )

    assert discover_message_windows_jsonl(tmp_path) == [windows_path]
    assert discover_semantic_prototype_inputs(tmp_path) == [windows_path]

    records = load_message_window_records(windows_path)
    assert records == [
        MessageWindowRecord(
            source_path=windows_path,
            provider_id="openai",
            conversation_id="conv-a",
            window_id="window-0001",
            ts_start=1,
            ts_end=2,
            text="alpha beta",
            message_ids=("window-0001-m1",),
        ),
        MessageWindowRecord(
            source_path=windows_path,
            provider_id="openai",
            conversation_id="conv-a",
            window_id="window-0002",
            ts_start=3,
            ts_end=4,
            text="beta gamma",
            message_ids=("window-0002-m1",),
        ),
    ]


def test_discover_semantic_prototype_inputs_accepts_parsed_jsonl_file(tmp_path):
    parsed_path = tmp_path / "openai" / "thread-conv-a" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed_path,
        provider_id="openai",
        conversation_id="conv-a",
        messages=[
            {
                "record_type": "message",
                "provider_id": "openai",
                "conversation_id": "conv-a",
                "message_id": "m1",
                "role": "user",
                "ts": 1,
                "text": "alpha beta",
                "content": {"content_type": "text", "parts": ["alpha beta"]},
            }
        ],
    )

    assert discover_semantic_prototype_inputs(parsed_path) == [parsed_path]


def test_discover_semantic_prototype_inputs_prefers_windows_per_thread(tmp_path):
    root = tmp_path / "artifacts"
    windows_path = root / "openai" / "thread-conv-a" / "message_windows.jsonl"
    parsed_only_path = root / "openai" / "thread-conv-b" / "parsed.jsonl"
    parsed_with_windows_path = windows_path.with_name("parsed.jsonl")
    _write_jsonl(
        windows_path,
        [_window_row("openai", "conv-a", "window-0001", "alpha beta", ts_start=1, ts_end=2)],
    )
    _write_parsed_jsonl(
        parsed_with_windows_path,
        provider_id="openai",
        conversation_id="conv-a",
        messages=[
            {
                "record_type": "message",
                "provider_id": "openai",
                "conversation_id": "conv-a",
                "message_id": "m1",
                "role": "user",
                "ts": 1,
                "text": "alpha beta",
                "content": {"content_type": "text", "parts": ["alpha beta"]},
            }
        ],
    )
    _write_parsed_jsonl(
        parsed_only_path,
        provider_id="openai",
        conversation_id="conv-b",
        messages=[
            {
                "record_type": "message",
                "provider_id": "openai",
                "conversation_id": "conv-b",
                "message_id": "n1",
                "role": "user",
                "ts": 3,
                "text": "beta gamma",
                "content": {"content_type": "text", "parts": ["beta gamma"]},
            }
        ],
    )

    assert discover_semantic_prototype_inputs(root) == [
        windows_path,
        parsed_only_path,
    ]


def test_load_message_window_records_from_parsed_matches_message_windows_default_segmentation(
    tmp_path,
):
    thread_dir = tmp_path / "openai" / "thread-conv-a"
    parsed_path = thread_dir / "parsed.jsonl"
    messages = [
        {
            "record_type": "message",
            "provider_id": "openai",
            "conversation_id": "conv-a",
            "message_id": f"m{index}",
            "role": "user" if index % 2 else "assistant",
            "ts": index,
            "text": f"text {index}",
            "content": {"content_type": "text", "parts": [f"text {index}"]},
        }
        for index in range(1, 6)
    ]
    _write_parsed_jsonl(
        parsed_path,
        provider_id="openai",
        conversation_id="conv-a",
        messages=messages,
    )
    windows_path = thread_dir / "message_windows.jsonl"
    window_rows = list(iter_message_windows_from_rows(messages))
    for row in window_rows:
        row["__parsed_messages"] = messages
    _write_jsonl(
        windows_path,
        window_rows,
    )

    from_parsed = load_message_window_records(parsed_path)
    from_windows = load_message_window_records(windows_path)

    assert from_parsed == [
        MessageWindowRecord(
            source_path=parsed_path,
            provider_id=record.provider_id,
            conversation_id=record.conversation_id,
            window_id=record.window_id,
            message_ids=record.message_ids,
            ts_start=record.ts_start,
            ts_end=record.ts_end,
            text=record.text,
        )
        for record in from_windows
    ]


def test_semantic_prototype_parsed_and_stored_window_inputs_are_equivalent(tmp_path):
    parsed_only_path = tmp_path / "parsed-only" / "openai" / "thread-conv-a" / "parsed.jsonl"
    windows_path = tmp_path / "with-windows" / "openai" / "thread-conv-a" / "message_windows.jsonl"
    messages = [
        {
            "record_type": "message",
            "provider_id": "openai",
            "conversation_id": "conv-a",
            "message_id": f"m{index}",
            "role": "user" if index % 2 else "assistant",
            "ts": index,
            "text": f"text {index}",
            "content": {"content_type": "text", "parts": [f"text {index}"]},
        }
        for index in range(1, 7)
    ]
    _write_parsed_jsonl(
        parsed_only_path,
        provider_id="openai",
        conversation_id="conv-a",
        messages=messages,
    )
    _write_parsed_jsonl(
        windows_path.with_name("parsed.jsonl"),
        provider_id="openai",
        conversation_id="conv-a",
        messages=messages,
    )
    window_rows = list(iter_message_windows_from_rows(messages))
    for row in window_rows:
        row["__parsed_messages"] = messages
    _write_jsonl(windows_path, window_rows)

    parsed_records = load_message_window_records(parsed_only_path)
    window_records = load_message_window_records(windows_path)

    assert _normalize_message_window_records(parsed_records) == _normalize_message_window_records(
        window_records
    )

    backend_vectors = [[1.0, 0.0], [0.25, 0.968246]]
    parsed_embeddings = build_window_embedding_records(
        parsed_records,
        backend=StaticEmbeddingBackend(backend_vectors),
    )
    window_embeddings = build_window_embedding_records(
        window_records,
        backend=StaticEmbeddingBackend(backend_vectors),
    )

    assert _normalize_embedding_records(parsed_embeddings) == _normalize_embedding_records(
        window_embeddings
    )

    parsed_structure = compute_semantic_structure(
        parsed_embeddings,
        top_k=1,
        min_score=0.0,
    )
    window_structure = compute_semantic_structure(
        window_embeddings,
        top_k=1,
        min_score=0.0,
    )

    assert _normalize_semantic_structure(
        parsed_structure,
        parsed_embeddings,
    ) == _normalize_semantic_structure(window_structure, window_embeddings)


def test_span_identity_uses_ordered_message_ids_not_window_id():
    first = MessageWindowRecord(
        source_path=Path("/tmp/thread-a/message_windows.jsonl"),
        provider_id="openai",
        conversation_id="conv-a",
        window_id="window-0001",
        message_ids=("m1", "m2"),
        ts_start=1,
        ts_end=2,
        text="alpha beta",
    )
    second = MessageWindowRecord(
        source_path=Path("/tmp/thread-a/message_windows.jsonl"),
        provider_id="openai",
        conversation_id="conv-a",
        window_id="window-9999",
        message_ids=("m1", "m2"),
        ts_start=1,
        ts_end=2,
        text="alpha beta",
    )
    reordered = MessageWindowRecord(
        source_path=Path("/tmp/thread-a/message_windows.jsonl"),
        provider_id="openai",
        conversation_id="conv-a",
        window_id="window-0002",
        message_ids=("m2", "m1"),
        ts_start=1,
        ts_end=2,
        text="alpha beta",
    )

    assert first.span_id == second.span_id
    assert first.span_id != reordered.span_id


def test_semantic_structure_is_invariant_to_window_id_overlay():
    windows = [
        MessageWindowRecord(
            source_path=Path("/tmp/thread-a/message_windows.jsonl"),
            provider_id="openai",
            conversation_id="conv-a",
            window_id="window-0001",
            message_ids=("m1",),
            ts_start=1,
            ts_end=2,
            text="alpha beta",
        ),
        MessageWindowRecord(
            source_path=Path("/tmp/thread-b/message_windows.jsonl"),
            provider_id="openai",
            conversation_id="conv-b",
            window_id="window-0002",
            message_ids=("m2",),
            ts_start=3,
            ts_end=4,
            text="alpha gamma",
        ),
        MessageWindowRecord(
            source_path=Path("/tmp/thread-c/message_windows.jsonl"),
            provider_id="openai",
            conversation_id="conv-c",
            window_id="window-0003",
            message_ids=("m3",),
            ts_start=5,
            ts_end=6,
            text="database migration",
        ),
    ]
    overlay_windows = [
        MessageWindowRecord(
            source_path=record.source_path,
            provider_id=record.provider_id,
            conversation_id=record.conversation_id,
            window_id=f"overlay-{index}",
            message_ids=record.message_ids,
            ts_start=record.ts_start,
            ts_end=record.ts_end,
            text=record.text,
        )
        for index, record in enumerate(windows, start=1)
    ]
    backend_vectors = [[1.0, 0.0], [0.95, 0.05], [0.0, 1.0]]
    embeddings = build_window_embedding_records(
        windows,
        backend=StaticEmbeddingBackend(backend_vectors),
    )
    overlay_embeddings = build_window_embedding_records(
        overlay_windows,
        backend=StaticEmbeddingBackend(backend_vectors),
    )

    assert [record.span_id for record in embeddings] == [
        record.span_id for record in overlay_embeddings
    ]

    semantic_structure = compute_semantic_structure(
        embeddings,
        top_k=1,
        min_score=0.0,
    )
    overlay_structure = compute_semantic_structure(
        overlay_embeddings,
        top_k=1,
        min_score=0.0,
    )

    assert _normalize_semantic_structure(
        semantic_structure,
        embeddings,
    ) == _normalize_semantic_structure(overlay_structure, overlay_embeddings)


def test_compute_semantic_structure_from_provider_uses_supplied_candidate_provider():
    embeddings = build_window_embedding_records(
        [
            MessageWindowRecord(
                source_path=Path("/tmp/thread-a/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-a",
                window_id="window-0001",
                message_ids=("m1",),
                ts_start=1,
                ts_end=2,
                text="alpha beta",
            ),
            MessageWindowRecord(
                source_path=Path("/tmp/thread-b/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-b",
                window_id="window-0001",
                message_ids=("m2",),
                ts_start=3,
                ts_end=4,
                text="alpha gamma",
            ),
        ],
        backend=StaticEmbeddingBackend([[1.0, 0.0], [0.9, 0.1]]),
    )
    candidate_pools = [tuple(range(len(embeddings))) for _ in embeddings]
    observed_calls: list[int] = []

    def _provider(records: list[WindowEmbeddingRecord]):
        observed_calls.append(len(records))
        return candidate_pools

    from_provider = compute_semantic_structure_from_provider(
        embeddings,
        top_k=1,
        min_score=0.0,
        candidate_provider=_provider,
    )
    direct = compute_semantic_structure(
        embeddings,
        top_k=1,
        min_score=0.0,
        candidate_pools=candidate_pools,
    )

    assert observed_calls == [2]
    assert from_provider == direct


def test_build_window_embedding_records_uses_backend_vectors():
    windows = [
        MessageWindowRecord(
            source_path=Path("/tmp/thread-a/message_windows.jsonl"),
            provider_id="openai",
            conversation_id="conv-a",
            window_id="window-0001",
            ts_start=1,
            ts_end=2,
            text="alpha beta",
        ),
        MessageWindowRecord(
            source_path=Path("/tmp/thread-a/message_windows.jsonl"),
            provider_id="openai",
            conversation_id="conv-a",
            window_id="window-0002",
            ts_start=3,
            ts_end=4,
            text="beta gamma",
        ),
    ]

    records = build_window_embedding_records(windows, backend=MockEmbeddingBackend())

    assert [record.embedding_model for record in records] == ["local/test-backend", "local/test-backend"]
    assert [tuple(record.embedding) for record in records] == [(1.0, 0.0), (0.0, 1.0)]
    assert [record.text_char_count for record in records] == [10, 10]


def test_cosine_similarity_and_neighbor_ranking():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0

    embeddings = build_window_embedding_records(
        [
            MessageWindowRecord(
                source_path=Path("/tmp/thread-a/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-a",
                window_id="window-0001",
                ts_start=1,
                ts_end=2,
                text="alpha beta",
            ),
            MessageWindowRecord(
                source_path=Path("/tmp/thread-b/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-b",
                window_id="window-0001",
                ts_start=3,
                ts_end=4,
                text="alpha beta gamma",
            ),
            MessageWindowRecord(
                source_path=Path("/tmp/thread-c/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-c",
                window_id="window-0001",
                ts_start=5,
                ts_end=6,
                text="completely different",
            ),
        ],
        backend=StaticEmbeddingBackend(
            [
                [1.0, 0.0],
                [0.8, 0.2],
                [0.0, 1.0],
            ]
        ),
    )

    rows = build_window_neighbor_rows(embeddings, top_k=2, min_score=0.0)

    assert rows[0]["window_id"] == "window-0001"
    assert rows[0]["neighbor_count"] == 2
    assert rows[0]["neighbors"][0]["conversation_id"] == "conv-b"
    assert rows[0]["neighbors"][1]["conversation_id"] == "conv-c"
    assert rows[0]["neighbors"][0]["score"] > rows[0]["neighbors"][1]["score"]


def test_neighbor_rows_exclude_self_and_use_deterministic_tie_breaks():
    embeddings = build_window_embedding_records(
        [
            MessageWindowRecord(
                source_path=Path("/tmp/thread-a/message_windows.jsonl"),
                provider_id="provider-b",
                conversation_id="conv-b",
                window_id="window-0002",
                ts_start=1,
                ts_end=2,
                text="alpha",
            ),
            MessageWindowRecord(
                source_path=Path("/tmp/thread-a/message_windows.jsonl"),
                provider_id="provider-a",
                conversation_id="conv-a",
                window_id="window-0001",
                ts_start=3,
                ts_end=4,
                text="beta",
            ),
            MessageWindowRecord(
                source_path=Path("/tmp/thread-a/message_windows.jsonl"),
                provider_id="provider-c",
                conversation_id="conv-c",
                window_id="window-0003",
                ts_start=5,
                ts_end=6,
                text="gamma",
            ),
        ],
        backend=StaticEmbeddingBackend(
            [
                [1.0, 0.0],
                [0.0, 1.0],
                [0.0, 1.0],
            ]
        ),
    )

    rows = build_window_neighbor_rows(embeddings, top_k=2, min_score=0.0)

    assert rows[1]["neighbors"][0]["window_id"] == "window-0003"
    assert rows[1]["neighbors"][1]["window_id"] == "window-0002"
    assert all(
        neighbor["window_id"] != rows[1]["window_id"]
        for neighbor in rows[1]["neighbors"]
    )


def test_min_score_filters_weak_neighbors_and_allows_fewer_than_top_k():
    embeddings = build_window_embedding_records(
        [
            MessageWindowRecord(
                source_path=Path("/tmp/thread-a/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-a",
                window_id="window-0001",
                ts_start=1,
                ts_end=2,
                text="target",
            ),
            MessageWindowRecord(
                source_path=Path("/tmp/thread-b/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-b",
                window_id="window-0001",
                ts_start=3,
                ts_end=4,
                text="strong",
            ),
            MessageWindowRecord(
                source_path=Path("/tmp/thread-c/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-c",
                window_id="window-0001",
                ts_start=5,
                ts_end=6,
                text="weak",
            ),
        ],
        backend=StaticEmbeddingBackend(
            [
                [1.0, 0.0],
                [0.9, 0.1],
                [0.2, 0.98],
            ]
        ),
    )

    rows = build_window_neighbor_rows(embeddings, top_k=2, min_score=0.8)

    assert rows[0]["neighbor_count"] == 1
    assert rows[0]["neighbors"] == [
        {
            "provider_id": "openai",
            "conversation_id": "conv-b",
            "window_id": "window-0001",
            "score": pytest.approx(0.9939, abs=1e-4),
        }
    ]


def test_min_score_preserves_deterministic_tie_breaks():
    embeddings = build_window_embedding_records(
        [
            MessageWindowRecord(
                source_path=Path("/tmp/thread-a/message_windows.jsonl"),
                provider_id="provider-z",
                conversation_id="conv-z",
                window_id="window-0001",
                ts_start=1,
                ts_end=1,
                text="target",
            ),
            MessageWindowRecord(
                source_path=Path("/tmp/thread-a/message_windows.jsonl"),
                provider_id="provider-a",
                conversation_id="conv-a",
                window_id="window-0001",
                ts_start=2,
                ts_end=2,
                text="left",
            ),
            MessageWindowRecord(
                source_path=Path("/tmp/thread-a/message_windows.jsonl"),
                provider_id="provider-b",
                conversation_id="conv-b",
                window_id="window-0001",
                ts_start=3,
                ts_end=3,
                text="right",
            ),
        ],
        backend=StaticEmbeddingBackend(
            [
                [1.0, 0.0],
                [0.5, 0.5],
                [0.5, 0.5],
            ]
        ),
    )

    rows = build_window_neighbor_rows(embeddings, top_k=2, min_score=0.7)

    assert [neighbor["conversation_id"] for neighbor in rows[0]["neighbors"]] == [
        "conv-a",
        "conv-b",
    ]


def test_near_same_thread_candidates_backfill_after_cross_thread_recurrence():
    embeddings = build_window_embedding_records(
        [
            MessageWindowRecord(
                source_path=Path("/tmp/thread-a/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-a",
                window_id="window-0001",
                message_ids=("m1",),
                ts_start=1,
                ts_end=1,
                text="target",
            ),
            MessageWindowRecord(
                source_path=Path("/tmp/thread-a/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-a",
                window_id="window-0002",
                message_ids=("m2",),
                ts_start=2,
                ts_end=2,
                text="adjacent same-thread",
            ),
            MessageWindowRecord(
                source_path=Path("/tmp/thread-b/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-b",
                window_id="window-0001",
                message_ids=("n1",),
                ts_start=3,
                ts_end=3,
                text="cross-thread recurrence",
            ),
        ],
        backend=StaticEmbeddingBackend(
            [
                [1.0, 0.0],
                [1.0, 0.0],
                [0.9, 0.1],
            ]
        ),
    )

    rows = build_window_neighbor_rows(embeddings, top_k=1, min_score=0.0)

    assert rows[0]["neighbors"] == [
        {
            "provider_id": "openai",
            "conversation_id": "conv-b",
            "window_id": "window-0001",
            "score": pytest.approx(0.9939, abs=1e-4),
        }
    ]


def test_distant_same_thread_candidates_remain_primary_neighbors():
    embeddings = build_window_embedding_records(
        [
            MessageWindowRecord(
                source_path=Path("/tmp/thread-a/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-a",
                window_id="window-0001",
                message_ids=("m1",),
                ts_start=1,
                ts_end=1,
                text="target",
            ),
            MessageWindowRecord(
                source_path=Path("/tmp/thread-a/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-a",
                window_id="window-0002",
                message_ids=("m2",),
                ts_start=2,
                ts_end=2,
                text="adjacent same-thread",
            ),
            MessageWindowRecord(
                source_path=Path("/tmp/thread-a/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-a",
                window_id="window-0003",
                message_ids=("m3",),
                ts_start=3,
                ts_end=3,
                text="distant same-thread recurrence",
            ),
        ],
        backend=StaticEmbeddingBackend(
            [
                [1.0, 0.0],
                [1.0, 0.0],
                [0.9, 0.1],
            ]
        ),
    )

    rows = build_window_neighbor_rows(embeddings, top_k=1, min_score=0.0)

    assert rows[0]["neighbors"] == [
        {
            "provider_id": "openai",
            "conversation_id": "conv-a",
            "window_id": "window-0003",
            "score": pytest.approx(0.9939, abs=1e-4),
        }
    ]


def test_overlapping_same_thread_candidates_are_treated_as_near_backfill():
    embeddings = build_window_embedding_records(
        [
            MessageWindowRecord(
                source_path=Path("/tmp/thread-a/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-a",
                window_id="window-0002",
                message_ids=("m2", "m3", "m4"),
                ts_start=2,
                ts_end=4,
                text="target overlap window",
            ),
            MessageWindowRecord(
                source_path=Path("/tmp/thread-a/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-a",
                window_id="window-0004",
                message_ids=("m4", "m5", "m6"),
                ts_start=4,
                ts_end=6,
                text="overlapping same-thread",
            ),
            MessageWindowRecord(
                source_path=Path("/tmp/thread-b/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-b",
                window_id="window-0001",
                message_ids=("n1",),
                ts_start=7,
                ts_end=7,
                text="cross-thread recurrence",
            ),
        ],
        backend=StaticEmbeddingBackend(
            [
                [1.0, 0.0],
                [1.0, 0.0],
                [0.9, 0.1],
            ]
        ),
    )

    rows = build_window_neighbor_rows(embeddings, top_k=1, min_score=0.0)

    assert rows[0]["neighbors"] == [
        {
            "provider_id": "openai",
            "conversation_id": "conv-b",
            "window_id": "window-0001",
            "score": pytest.approx(0.9939, abs=1e-4),
        }
    ]


def test_near_same_thread_backfill_preserves_deterministic_ordering():
    embeddings = build_window_embedding_records(
        [
            MessageWindowRecord(
                source_path=Path("/tmp/thread-z/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-z",
                window_id="window-0001",
                message_ids=("m1",),
                ts_start=1,
                ts_end=1,
                text="target",
            ),
            MessageWindowRecord(
                source_path=Path("/tmp/thread-z/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-z",
                window_id="window-0002",
                message_ids=("m2",),
                ts_start=2,
                ts_end=2,
                text="adjacent same-thread",
            ),
            MessageWindowRecord(
                source_path=Path("/tmp/thread-a/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-a",
                window_id="window-0001",
                message_ids=("n1",),
                ts_start=3,
                ts_end=3,
                text="cross-thread left",
            ),
            MessageWindowRecord(
                source_path=Path("/tmp/thread-b/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-b",
                window_id="window-0001",
                message_ids=("n2",),
                ts_start=4,
                ts_end=4,
                text="cross-thread right",
            ),
        ],
        backend=StaticEmbeddingBackend(
            [
                [1.0, 0.0],
                [1.0, 0.0],
                [0.5, 0.5],
                [0.5, 0.5],
            ]
        ),
    )

    rows_first = build_window_neighbor_rows(embeddings, top_k=3, min_score=0.7)
    rows_second = build_window_neighbor_rows(embeddings, top_k=3, min_score=0.7)

    assert rows_first == rows_second
    assert [neighbor["conversation_id"] for neighbor in rows_first[0]["neighbors"]] == [
        "conv-a",
        "conv-b",
        "conv-z",
    ]


def test_build_window_neighbor_rows_progress_callback():
    set_locale("en-US")
    embeddings = build_window_embedding_records(
        [
            MessageWindowRecord(
                source_path=Path("/tmp/thread-a/message_windows.jsonl"),
                provider_id="openai",
                conversation_id=f"conv-{index}",
                window_id=f"window-{index:04d}",
                ts_start=index,
                ts_end=index,
                text=f"text-{index}",
            )
            for index in range(3)
        ],
        backend=StaticEmbeddingBackend(
            [
                [1.0, 0.0],
                [0.5, 0.5],
                [0.0, 1.0],
            ]
        ),
    )
    progress_messages: list[str] = []

    build_window_neighbor_rows(
        embeddings,
        top_k=1,
        progress=progress_messages.append,
        progress_every=1,
    )

    assert progress_messages == [
        "semantic prototype: neighbors 1 / 3",
        "semantic prototype: neighbors 2 / 3",
        "semantic prototype: neighbors 3 / 3",
    ]


def test_build_window_neighbor_rows_uses_full_scan_provider_orchestration(monkeypatch):
    embeddings = [
        WindowEmbeddingRecord(
            source_path=Path("/tmp/thread-a/message_windows.jsonl"),
            provider_id="openai",
            conversation_id="conv-a",
            window_id="window-0001",
            message_ids=("m1",),
            ts_start=1,
            ts_end=2,
            embedding_model="local/test-static",
            text_char_count=5,
            embedding=(1.0, 0.0),
        )
    ]
    observed: dict[str, object] = {}

    def _fake_compute(
        embeddings_arg,
        *,
        top_k,
        min_score,
        candidate_provider,
        prefer_same_thread=False,
        cross_thread_score_percentile=0.75,
        progress=None,
        progress_every=None,
    ):
        observed["embeddings"] = embeddings_arg
        observed["top_k"] = top_k
        observed["min_score"] = min_score
        observed["candidate_provider"] = candidate_provider
        observed["prefer_same_thread"] = prefer_same_thread
        return SemanticStructureResult(neighbor_rows=[{"ok": True}], cluster_rows=[])

    monkeypatch.setattr(
        "llm_logparser.core.analyzer_semantic_prototype.compute_semantic_structure_from_provider",
        _fake_compute,
    )

    rows = build_window_neighbor_rows(embeddings, top_k=1, min_score=0.5)

    assert rows == [{"ok": True}]
    assert observed["embeddings"] == embeddings
    assert observed["top_k"] == 1
    assert observed["min_score"] == 0.5
    assert observed["candidate_provider"] is build_full_scan_candidate_pools
    assert observed["prefer_same_thread"] is False


def test_build_window_neighbor_rows_with_sqlite_candidates_uses_provider_orchestration(
    monkeypatch, tmp_path
):
    embeddings = [
        WindowEmbeddingRecord(
            source_path=Path("/tmp/thread-a/message_windows.jsonl"),
            provider_id="openai",
            conversation_id="conv-a",
            window_id="window-0001",
            message_ids=("m1",),
            ts_start=1,
            ts_end=2,
            embedding_model="local/test-static",
            text_char_count=5,
            embedding=(1.0, 0.0),
        )
    ]
    observed: dict[str, object] = {}

    def _fake_compute(
        embeddings_arg,
        *,
        top_k,
        min_score,
        candidate_provider,
        prefer_same_thread=False,
        cross_thread_score_percentile=0.75,
        progress=None,
        progress_every=None,
    ):
        observed["embeddings"] = embeddings_arg
        observed["top_k"] = top_k
        observed["min_score"] = min_score
        observed["candidate_provider"] = candidate_provider
        observed["prefer_same_thread"] = prefer_same_thread
        return SemanticStructureResult(neighbor_rows=[{"ok": True}], cluster_rows=[])

    monkeypatch.setattr(
        "llm_logparser.core.analyzer_semantic_prototype.compute_semantic_structure_from_provider",
        _fake_compute,
    )

    rows = build_window_neighbor_rows_with_sqlite_candidates(
        embeddings,
        top_k=1,
        min_score=0.5,
        candidate_config=SqliteCandidateConfig(
            db_path=tmp_path / "analysis.db",
            candidate_same_thread="prefer",
        ),
    )

    assert rows == [{"ok": True}]
    assert observed["embeddings"] == embeddings
    assert observed["top_k"] == 1
    assert observed["min_score"] == 0.5
    assert callable(observed["candidate_provider"])
    assert observed["candidate_provider"] is not build_full_scan_candidate_pools
    assert observed["prefer_same_thread"] is True


def test_embedding_and_neighbor_rows_match_schema():
    windows = [
        MessageWindowRecord(
            source_path=Path("/tmp/thread-a/message_windows.jsonl"),
            provider_id="openai",
            conversation_id="conv-a",
            window_id="window-0001",
            ts_start=1,
            ts_end=2,
            text="alpha beta",
        ),
        MessageWindowRecord(
            source_path=Path("/tmp/thread-b/message_windows.jsonl"),
            provider_id="openai",
            conversation_id="conv-b",
            window_id="window-0001",
            ts_start=3,
            ts_end=4,
            text="alpha gamma",
        ),
    ]

    embeddings = build_window_embedding_records(
        windows,
        backend=StaticEmbeddingBackend([[1.0, 0.0], [0.5, 0.5]]),
    )
    embedding_row = render_window_embedding_row(embeddings[0])
    neighbor_row = build_window_neighbor_rows(embeddings, top_k=1)[0]
    cluster_row = build_window_cluster_rows(embeddings, [neighbor_row, build_window_neighbor_rows(embeddings, top_k=1)[1]])[0]

    embedding_validator = load_window_embedding_validator()
    neighbor_validator = load_window_neighbors_validator()
    cluster_validator = load_window_clusters_validator()

    assert list(embedding_validator.iter_errors(embedding_row)) == []
    assert list(neighbor_validator.iter_errors(neighbor_row)) == []
    assert list(cluster_validator.iter_errors(cluster_row)) == []


def test_sqlite_candidate_generation_applies_filters_and_same_thread_policies(tmp_path):
    day_ms = 24 * 60 * 60 * 1000
    base_ts = 10 * day_ms
    embeddings = build_window_embedding_records(
        [
            MessageWindowRecord(
                source_path=Path("/tmp/thread-a/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-a",
                window_id="window-0001",
                ts_start=base_ts,
                ts_end=base_ts + 1,
                text="target",
            ),
            MessageWindowRecord(
                source_path=Path("/tmp/thread-a/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-a",
                window_id="window-0002",
                ts_start=base_ts + 1000,
                ts_end=base_ts + 1001,
                text="same-thread",
            ),
            MessageWindowRecord(
                source_path=Path("/tmp/thread-b/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-b",
                window_id="window-0001",
                ts_start=base_ts + 2000,
                ts_end=base_ts + 2001,
                text="cross-thread",
            ),
            MessageWindowRecord(
                source_path=Path("/tmp/thread-c/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-c",
                window_id="window-0001",
                ts_start=base_ts + (2 * day_ms),
                ts_end=base_ts + (2 * day_ms) + 1,
                text="too-old",
            ),
            MessageWindowRecord(
                source_path=Path("/tmp/thread-d/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-d",
                window_id="window-0001",
                ts_start=base_ts + 3000,
                ts_end=base_ts + 3001,
                text="too-short",
            ),
            MessageWindowRecord(
                source_path=Path("/tmp/thread-e/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-e",
                window_id="window-0001",
                ts_start=base_ts + 4000,
                ts_end=base_ts + 4001,
                text="low-ratio",
            ),
        ],
        backend=StaticEmbeddingBackend(
            [
                [1.0, 0.0],
                [1.0, 0.0],
                [1.0, 0.0],
                [1.0, 0.0],
                [1.0, 0.0],
                [1.0, 0.0],
            ]
        ),
    )
    db_path = _write_candidate_db(
        tmp_path / "analysis.db",
        provider_id="openai",
        thread_rows=[
            {
                "provider_id": "openai",
                "conversation_id": "conv-a",
                "message_count": 4,
                "assistant_messages": 2,
                "user_messages": 2,
                "other_roles": 0,
                "character_count": 40,
                "characters_total": 40,
                "characters_user": 20,
                "characters_assistant": 20,
                "other_role_breakdown": None,
                "first_timestamp": base_ts,
                "last_timestamp": base_ts + 1000,
                "conversation_span_seconds": 1,
            },
            {
                "provider_id": "openai",
                "conversation_id": "conv-b",
                "message_count": 2,
                "assistant_messages": 2,
                "user_messages": 0,
                "other_roles": 0,
                "character_count": 20,
                "characters_total": 20,
                "characters_user": 0,
                "characters_assistant": 20,
                "other_role_breakdown": None,
                "first_timestamp": base_ts + 2000,
                "last_timestamp": base_ts + 2000,
                "conversation_span_seconds": 0,
            },
            {
                "provider_id": "openai",
                "conversation_id": "conv-c",
                "message_count": 2,
                "assistant_messages": 2,
                "user_messages": 0,
                "other_roles": 0,
                "character_count": 20,
                "characters_total": 20,
                "characters_user": 0,
                "characters_assistant": 20,
                "other_role_breakdown": None,
                "first_timestamp": base_ts + (2 * day_ms),
                "last_timestamp": base_ts + (2 * day_ms),
                "conversation_span_seconds": 0,
            },
            {
                "provider_id": "openai",
                "conversation_id": "conv-d",
                "message_count": 2,
                "assistant_messages": 2,
                "user_messages": 0,
                "other_roles": 0,
                "character_count": 5,
                "characters_total": 5,
                "characters_user": 0,
                "characters_assistant": 5,
                "other_role_breakdown": None,
                "first_timestamp": base_ts + 3000,
                "last_timestamp": base_ts + 3000,
                "conversation_span_seconds": 0,
            },
            {
                "provider_id": "openai",
                "conversation_id": "conv-e",
                "message_count": 2,
                "assistant_messages": 0,
                "user_messages": 2,
                "other_roles": 0,
                "character_count": 20,
                "characters_total": 20,
                "characters_user": 20,
                "characters_assistant": 0,
                "other_role_breakdown": None,
                "first_timestamp": base_ts + 4000,
                "last_timestamp": base_ts + 4000,
                "conversation_span_seconds": 0,
            },
        ],
        window_rows=[
            _window_row("openai", "conv-a", "window-0001", "target", ts_start=base_ts, ts_end=base_ts + 1),
            _window_row("openai", "conv-a", "window-0002", "same-thread", ts_start=base_ts + 1000, ts_end=base_ts + 1001),
            _window_row("openai", "conv-b", "window-0001", "cross-thread", ts_start=base_ts + 2000, ts_end=base_ts + 2001),
            _window_row("openai", "conv-c", "window-0001", "too-old", ts_start=base_ts + (2 * day_ms), ts_end=base_ts + (2 * day_ms) + 1),
            _window_row("openai", "conv-d", "window-0001", "tiny", ts_start=base_ts + 3000, ts_end=base_ts + 3001),
            _window_row("openai", "conv-e", "window-0001", "low-ratio", ts_start=base_ts + 4000, ts_end=base_ts + 4001),
        ],
    )

    allow_rows = build_window_neighbor_rows_with_sqlite_candidates(
        embeddings,
        top_k=5,
        min_score=0.0,
        candidate_config=SqliteCandidateConfig(
            db_path=db_path,
            candidate_window_days=1,
            candidate_min_chars=10,
            candidate_min_assistant_ratio=0.5,
            candidate_same_thread="allow",
        ),
    )
    exclude_rows = build_window_neighbor_rows_with_sqlite_candidates(
        embeddings,
        top_k=5,
        min_score=0.0,
        candidate_config=SqliteCandidateConfig(
            db_path=db_path,
            candidate_window_days=1,
            candidate_min_chars=10,
            candidate_min_assistant_ratio=0.5,
            candidate_same_thread="exclude",
        ),
    )
    only_rows = build_window_neighbor_rows_with_sqlite_candidates(
        embeddings,
        top_k=5,
        min_score=0.0,
        candidate_config=SqliteCandidateConfig(
            db_path=db_path,
            candidate_window_days=1,
            candidate_min_chars=10,
            candidate_min_assistant_ratio=0.5,
            candidate_same_thread="only",
        ),
    )

    assert [neighbor["conversation_id"] for neighbor in allow_rows[0]["neighbors"]] == [
        "conv-b",
        "conv-a",
    ]
    assert [neighbor["conversation_id"] for neighbor in exclude_rows[0]["neighbors"]] == [
        "conv-b",
    ]
    assert [neighbor["conversation_id"] for neighbor in only_rows[0]["neighbors"]] == [
        "conv-a",
    ]


def test_sqlite_candidate_generation_prefer_same_thread_breaks_ties_for_non_near_candidates(tmp_path):
    base_ts = 1000
    embeddings = build_window_embedding_records(
        [
            MessageWindowRecord(
                source_path=Path("/tmp/thread-a/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-a",
                window_id="window-0001",
                ts_start=base_ts,
                ts_end=base_ts + 1,
                text="target",
            ),
            MessageWindowRecord(
                source_path=Path("/tmp/thread-a/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-a",
                window_id="window-0003",
                ts_start=base_ts + 10,
                ts_end=base_ts + 11,
                text="same-thread",
            ),
            MessageWindowRecord(
                source_path=Path("/tmp/thread-b/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-b",
                window_id="window-0001",
                ts_start=base_ts + 20,
                ts_end=base_ts + 21,
                text="cross-thread",
            ),
        ],
        backend=StaticEmbeddingBackend([[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]]),
    )
    db_path = _write_candidate_db(
        tmp_path / "analysis.db",
        provider_id="openai",
        thread_rows=[
            {
                "provider_id": "openai",
                "conversation_id": "conv-a",
                "message_count": 2,
                "assistant_messages": 1,
                "user_messages": 1,
                "other_roles": 0,
                "character_count": 20,
                "characters_total": 20,
                "characters_user": 10,
                "characters_assistant": 10,
                "other_role_breakdown": None,
                "first_timestamp": base_ts,
                "last_timestamp": base_ts + 10,
                "conversation_span_seconds": 0,
            },
            {
                "provider_id": "openai",
                "conversation_id": "conv-b",
                "message_count": 2,
                "assistant_messages": 1,
                "user_messages": 1,
                "other_roles": 0,
                "character_count": 20,
                "characters_total": 20,
                "characters_user": 10,
                "characters_assistant": 10,
                "other_role_breakdown": None,
                "first_timestamp": base_ts + 20,
                "last_timestamp": base_ts + 20,
                "conversation_span_seconds": 0,
            },
        ],
        window_rows=[
            _window_row("openai", "conv-a", "window-0001", "target", ts_start=base_ts, ts_end=base_ts + 1),
            _window_row("openai", "conv-a", "window-0003", "same-thread", ts_start=base_ts + 10, ts_end=base_ts + 11),
            _window_row("openai", "conv-b", "window-0001", "cross-thread", ts_start=base_ts + 20, ts_end=base_ts + 21),
        ],
    )

    rows = build_window_neighbor_rows_with_sqlite_candidates(
        embeddings,
        top_k=1,
        min_score=0.0,
        candidate_config=SqliteCandidateConfig(
            db_path=db_path,
            candidate_window_days=1,
            candidate_same_thread="prefer",
        ),
    )

    assert rows[0]["neighbors"][0]["conversation_id"] == "conv-a"


def test_sqlite_candidate_pool_comparison_is_symmetric_and_recovers_mutual_links(
    tmp_path,
):
    day_ms = 24 * 60 * 60 * 1000
    embeddings = build_window_embedding_records(
        [
            MessageWindowRecord(
                source_path=Path("/tmp/thread-a/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-a",
                window_id="window-0001",
                ts_start=0,
                ts_end=1,
                text="alpha",
            ),
            MessageWindowRecord(
                source_path=Path("/tmp/thread-b/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-b",
                window_id="window-0001",
                ts_start=day_ms,
                ts_end=day_ms + 1,
                text="bridge",
            ),
            MessageWindowRecord(
                source_path=Path("/tmp/thread-c/message_windows.jsonl"),
                provider_id="openai",
                conversation_id="conv-c",
                window_id="window-0001",
                ts_start=2 * day_ms,
                ts_end=(2 * day_ms) + 1,
                text="alpha",
            ),
        ],
        backend=StaticEmbeddingBackend(
            [
                [1.0, 0.0],
                [0.0, 1.0],
                [1.0, 0.0],
            ]
        ),
    )
    db_path = _write_candidate_db(
        tmp_path / "analysis.db",
        provider_id="openai",
        thread_rows=[
            {
                "provider_id": "openai",
                "conversation_id": "conv-a",
                "message_count": 2,
                "assistant_messages": 1,
                "user_messages": 1,
                "other_roles": 0,
                "character_count": 20,
                "characters_total": 20,
                "characters_user": 10,
                "characters_assistant": 10,
                "other_role_breakdown": None,
                "first_timestamp": 0,
                "last_timestamp": 1,
                "conversation_span_seconds": 0,
            },
            {
                "provider_id": "openai",
                "conversation_id": "conv-b",
                "message_count": 2,
                "assistant_messages": 1,
                "user_messages": 1,
                "other_roles": 0,
                "character_count": 20,
                "characters_total": 20,
                "characters_user": 10,
                "characters_assistant": 10,
                "other_role_breakdown": None,
                "first_timestamp": day_ms,
                "last_timestamp": day_ms + 1,
                "conversation_span_seconds": 0,
            },
            {
                "provider_id": "openai",
                "conversation_id": "conv-c",
                "message_count": 2,
                "assistant_messages": 1,
                "user_messages": 1,
                "other_roles": 0,
                "character_count": 20,
                "characters_total": 20,
                "characters_user": 10,
                "characters_assistant": 10,
                "other_role_breakdown": None,
                "first_timestamp": 2 * day_ms,
                "last_timestamp": (2 * day_ms) + 1,
                "conversation_span_seconds": 0,
            },
        ],
        window_rows=[
            _window_row("openai", "conv-a", "window-0001", "alpha", ts_start=0, ts_end=1),
            _window_row("openai", "conv-b", "window-0001", "bridge", ts_start=day_ms, ts_end=day_ms + 1),
            _window_row("openai", "conv-c", "window-0001", "alpha", ts_start=2 * day_ms, ts_end=(2 * day_ms) + 1),
        ],
    )
    candidate_config = SqliteCandidateConfig(
        db_path=db_path,
        candidate_window_days=1,
        candidate_same_thread="exclude",
    )

    pools = build_sqlite_candidate_pools(
        embeddings,
        candidate_config=candidate_config,
    )
    rows = build_window_neighbor_rows_with_sqlite_candidates(
        embeddings,
        top_k=1,
        min_score=0.8,
        candidate_config=candidate_config,
    )
    cluster_rows = build_window_cluster_rows(embeddings, rows)

    assert pools == [
        (0, 1),
        (0, 1, 2),
        (1, 2),
    ]
    assert rows[0]["neighbors"][0]["conversation_id"] == "conv-c"
    assert rows[2]["neighbors"][0]["conversation_id"] == "conv-a"
    assert rows[1]["neighbor_count"] == 0
    assert [(row["conversation_id"], row["cluster_size"]) for row in cluster_rows] == [
        ("conv-a", 2),
        ("conv-b", 1),
        ("conv-c", 2),
    ]


def test_sliding_windows_improve_boundary_recoverability_for_mutual_clustering():
    message_rows = [
        {
            "record_type": "message",
            "provider_id": "openai",
            "conversation_id": "conv-a",
            "message_id": f"m{index}",
            "role": "user",
            "ts": index,
            "text": text,
            "content": {"content_type": "text", "parts": [text]},
        }
        for index, text in enumerate(
            ["alpha", "alpha", "alpha", "beta", "beta", "beta"],
            start=1,
        )
    ]

    non_overlapping_windows = list(
        iter_message_windows_from_rows(
            message_rows,
            window_size=3,
            window_stride=3,
        )
    )
    sliding_windows = list(
        iter_message_windows_from_rows(
            message_rows,
            window_size=3,
            window_stride=2,
        )
    )

    non_overlapping_records = [
        MessageWindowRecord(
            source_path=Path("/tmp/thread-a/message_windows.jsonl"),
            provider_id=row["provider_id"],
            conversation_id=row["conversation_id"],
            window_id=row["window_id"],
            ts_start=row["ts_start"],
            ts_end=row["ts_end"],
            message_ids=tuple(row["message_ids"]),
            text="\n\n".join(
                message_row["text"]
                for message_id in row["message_ids"]
                for message_row in message_rows
                if message_row["message_id"] == message_id
            ),
        )
        for row in non_overlapping_windows
    ]
    sliding_records = [
        MessageWindowRecord(
            source_path=Path("/tmp/thread-a/message_windows.jsonl"),
            provider_id=row["provider_id"],
            conversation_id=row["conversation_id"],
            window_id=row["window_id"],
            ts_start=row["ts_start"],
            ts_end=row["ts_end"],
            message_ids=tuple(row["message_ids"]),
            text="\n\n".join(
                message_row["text"]
                for message_id in row["message_ids"]
                for message_row in message_rows
                if message_row["message_id"] == message_id
            ),
        )
        for row in sliding_windows
    ]

    non_overlapping_embeddings = build_window_embedding_records(
        non_overlapping_records,
        backend=KeywordCountEmbeddingBackend(),
    )
    sliding_embeddings = build_window_embedding_records(
        sliding_records,
        backend=KeywordCountEmbeddingBackend(),
    )

    non_overlapping_neighbors = build_window_neighbor_rows(
        non_overlapping_embeddings,
        top_k=2,
        min_score=0.8,
    )
    sliding_neighbors = build_window_neighbor_rows(
        sliding_embeddings,
        top_k=2,
        min_score=0.8,
    )
    non_overlapping_clusters = build_window_cluster_rows(
        non_overlapping_embeddings,
        non_overlapping_neighbors,
    )
    sliding_clusters = build_window_cluster_rows(
        sliding_embeddings,
        sliding_neighbors,
    )

    assert [row["cluster_size"] for row in non_overlapping_clusters] == [1, 1]
    assert [row["cluster_size"] for row in sliding_clusters] == [1, 2, 2]
    assert [row["cluster_id"] for row in sliding_clusters] == [
        "cluster_000001",
        "cluster_000002",
        "cluster_000002",
    ]


def test_build_window_cluster_rows_are_deterministic_and_mutual_only():
    embeddings = [
        WindowEmbeddingRecord(
            source_path=Path("/tmp/thread-a/message_windows.jsonl"),
            provider_id="openai",
            conversation_id="conv-a",
            window_id="window-0001",
            ts_start=1,
            ts_end=1,
            embedding_model="local/test",
            text_char_count=10,
            embedding=(1.0, 0.0),
        ),
        WindowEmbeddingRecord(
            source_path=Path("/tmp/thread-a/message_windows.jsonl"),
            provider_id="openai",
            conversation_id="conv-a",
            window_id="window-0002",
            ts_start=2,
            ts_end=2,
            embedding_model="local/test",
            text_char_count=10,
            embedding=(1.0, 0.0),
        ),
        WindowEmbeddingRecord(
            source_path=Path("/tmp/thread-b/message_windows.jsonl"),
            provider_id="openai",
            conversation_id="conv-b",
            window_id="window-0001",
            ts_start=3,
            ts_end=3,
            embedding_model="local/test",
            text_char_count=10,
            embedding=(1.0, 0.0),
        ),
        WindowEmbeddingRecord(
            source_path=Path("/tmp/thread-c/message_windows.jsonl"),
            provider_id="openai",
            conversation_id="conv-c",
            window_id="window-0001",
            ts_start=4,
            ts_end=4,
            embedding_model="local/test",
            text_char_count=10,
            embedding=(1.0, 0.0),
        ),
    ]
    neighbor_rows = [
        {
            "record_type": "window_neighbors",
            "schema_version": "0.1",
            "provider_id": "openai",
            "conversation_id": "conv-a",
            "window_id": "window-0001",
            "embedding_model": "local/test",
            "neighbor_count": 1,
            "neighbors": [
                {
                    "provider_id": "openai",
                    "conversation_id": "conv-a",
                    "window_id": "window-0002",
                    "score": 0.95,
                }
            ],
        },
        {
            "record_type": "window_neighbors",
            "schema_version": "0.1",
            "provider_id": "openai",
            "conversation_id": "conv-a",
            "window_id": "window-0002",
            "embedding_model": "local/test",
            "neighbor_count": 1,
            "neighbors": [
                {
                    "provider_id": "openai",
                    "conversation_id": "conv-a",
                    "window_id": "window-0001",
                    "score": 0.95,
                }
            ],
        },
        {
            "record_type": "window_neighbors",
            "schema_version": "0.1",
            "provider_id": "openai",
            "conversation_id": "conv-b",
            "window_id": "window-0001",
            "embedding_model": "local/test",
            "neighbor_count": 1,
            "neighbors": [
                {
                    "provider_id": "openai",
                    "conversation_id": "conv-c",
                    "window_id": "window-0001",
                    "score": 0.91,
                }
            ],
        },
        {
            "record_type": "window_neighbors",
            "schema_version": "0.1",
            "provider_id": "openai",
            "conversation_id": "conv-c",
            "window_id": "window-0001",
            "embedding_model": "local/test",
            "neighbor_count": 0,
            "neighbors": [],
        },
    ]

    rows = build_window_cluster_rows(embeddings, neighbor_rows)

    assert [(row["conversation_id"], row["cluster_id"], row["cluster_size"]) for row in rows] == [
        ("conv-a", "cluster_000001", 2),
        ("conv-a", "cluster_000001", 2),
        ("conv-b", "cluster_000002", 1),
        ("conv-c", "cluster_000003", 1),
    ]


def test_build_window_cluster_rows_suppresses_same_thread_edges_when_windows_share_multiple_messages():
    embeddings = [
        WindowEmbeddingRecord(
            source_path=Path("/tmp/thread-a/message_windows.jsonl"),
            provider_id="openai",
            conversation_id="conv-a",
            window_id="window-0001",
            ts_start=1,
            ts_end=2,
            embedding_model="local/test",
            text_char_count=10,
            embedding=(1.0, 0.0),
            message_ids=("m1", "m2", "m3", "m4"),
        ),
        WindowEmbeddingRecord(
            source_path=Path("/tmp/thread-a/message_windows.jsonl"),
            provider_id="openai",
            conversation_id="conv-a",
            window_id="window-0002",
            ts_start=3,
            ts_end=4,
            embedding_model="local/test",
            text_char_count=10,
            embedding=(1.0, 0.0),
            message_ids=("m3", "m4", "m5", "m6"),
        ),
        WindowEmbeddingRecord(
            source_path=Path("/tmp/thread-b/message_windows.jsonl"),
            provider_id="openai",
            conversation_id="conv-b",
            window_id="window-0001",
            ts_start=5,
            ts_end=6,
            embedding_model="local/test",
            text_char_count=10,
            embedding=(1.0, 0.0),
            message_ids=("n1", "n2", "n3", "n4"),
        ),
    ]
    neighbor_rows = [
        {
            "record_type": "window_neighbors",
            "schema_version": "0.1",
            "provider_id": "openai",
            "conversation_id": "conv-a",
            "window_id": "window-0001",
            "embedding_model": "local/test",
            "neighbor_count": 1,
            "neighbors": [
                {
                    "provider_id": "openai",
                    "conversation_id": "conv-a",
                    "window_id": "window-0002",
                    "score": 0.99,
                }
            ],
        },
        {
            "record_type": "window_neighbors",
            "schema_version": "0.1",
            "provider_id": "openai",
            "conversation_id": "conv-a",
            "window_id": "window-0002",
            "embedding_model": "local/test",
            "neighbor_count": 1,
            "neighbors": [
                {
                    "provider_id": "openai",
                    "conversation_id": "conv-a",
                    "window_id": "window-0001",
                    "score": 0.99,
                }
            ],
        },
        {
            "record_type": "window_neighbors",
            "schema_version": "0.1",
            "provider_id": "openai",
            "conversation_id": "conv-b",
            "window_id": "window-0001",
            "embedding_model": "local/test",
            "neighbor_count": 0,
            "neighbors": [],
        },
    ]

    rows = build_window_cluster_rows(embeddings, neighbor_rows)

    assert [(row["conversation_id"], row["cluster_size"]) for row in rows] == [
        ("conv-a", 1),
        ("conv-a", 1),
        ("conv-b", 1),
    ]


def test_build_window_cluster_rows_preserves_same_thread_edges_with_one_shared_message():
    embeddings = [
        WindowEmbeddingRecord(
            source_path=Path("/tmp/thread-a/message_windows.jsonl"),
            provider_id="openai",
            conversation_id="conv-a",
            window_id="window-0003",
            ts_start=1,
            ts_end=2,
            embedding_model="local/test",
            text_char_count=10,
            embedding=(1.0, 0.0),
            message_ids=("m1", "m2", "m3"),
        ),
        WindowEmbeddingRecord(
            source_path=Path("/tmp/thread-a/message_windows.jsonl"),
            provider_id="openai",
            conversation_id="conv-a",
            window_id="window-0004",
            ts_start=3,
            ts_end=4,
            embedding_model="local/test",
            text_char_count=10,
            embedding=(1.0, 0.0),
            message_ids=("m3", "m4"),
        ),
    ]
    neighbor_rows = [
        {
            "record_type": "window_neighbors",
            "schema_version": "0.1",
            "provider_id": "openai",
            "conversation_id": "conv-a",
            "window_id": "window-0003",
            "embedding_model": "local/test",
            "neighbor_count": 1,
            "neighbors": [
                {
                    "provider_id": "openai",
                    "conversation_id": "conv-a",
                    "window_id": "window-0004",
                    "score": 0.98,
                }
            ],
        },
        {
            "record_type": "window_neighbors",
            "schema_version": "0.1",
            "provider_id": "openai",
            "conversation_id": "conv-a",
            "window_id": "window-0004",
            "embedding_model": "local/test",
            "neighbor_count": 1,
            "neighbors": [
                {
                    "provider_id": "openai",
                    "conversation_id": "conv-a",
                    "window_id": "window-0003",
                    "score": 0.98,
                }
            ],
        },
    ]

    rows = build_window_cluster_rows(embeddings, neighbor_rows)

    assert [row["cluster_size"] for row in rows] == [2, 2]


def test_build_window_cluster_rows_falls_back_to_legacy_behavior_without_message_ids():
    embeddings = [
        WindowEmbeddingRecord(
            source_path=Path("/tmp/thread-a/message_windows.jsonl"),
            provider_id="openai",
            conversation_id="conv-a",
            window_id="window-0001",
            ts_start=1,
            ts_end=2,
            embedding_model="local/test",
            text_char_count=10,
            embedding=(1.0, 0.0),
        ),
        WindowEmbeddingRecord(
            source_path=Path("/tmp/thread-a/message_windows.jsonl"),
            provider_id="openai",
            conversation_id="conv-a",
            window_id="window-0002",
            ts_start=3,
            ts_end=4,
            embedding_model="local/test",
            text_char_count=10,
            embedding=(1.0, 0.0),
        ),
    ]
    neighbor_rows = [
        {
            "record_type": "window_neighbors",
            "schema_version": "0.1",
            "provider_id": "openai",
            "conversation_id": "conv-a",
            "window_id": "window-0001",
            "embedding_model": "local/test",
            "neighbor_count": 1,
            "neighbors": [
                {
                    "provider_id": "openai",
                    "conversation_id": "conv-a",
                    "window_id": "window-0002",
                    "score": 0.95,
                }
            ],
        },
        {
            "record_type": "window_neighbors",
            "schema_version": "0.1",
            "provider_id": "openai",
            "conversation_id": "conv-a",
            "window_id": "window-0002",
            "embedding_model": "local/test",
            "neighbor_count": 1,
            "neighbors": [
                {
                    "provider_id": "openai",
                    "conversation_id": "conv-a",
                    "window_id": "window-0001",
                    "score": 0.95,
                }
            ],
        },
    ]

    rows = build_window_cluster_rows(embeddings, neighbor_rows)

    assert [row["cluster_size"] for row in rows] == [2, 2]


def test_build_window_cluster_rows_suppresses_weak_cross_thread_bridges_but_keeps_stronger_links():
    embeddings = [
        WindowEmbeddingRecord(
            source_path=Path("/tmp/thread-a/message_windows.jsonl"),
            provider_id="openai",
            conversation_id=conversation_id,
            window_id=window_id,
            ts_start=index * 2 + 1,
            ts_end=index * 2 + 2,
            embedding_model="local/test",
            text_char_count=10,
            embedding=(1.0, 0.0),
            message_ids=(f"{conversation_id}-{window_id}-m1",),
        )
        for index, (conversation_id, window_id) in enumerate(
            [
                ("conv-a", "window-0001"),
                ("conv-a", "window-0002"),
                ("conv-b", "window-0001"),
                ("conv-b", "window-0002"),
                ("conv-c", "window-0001"),
                ("conv-c", "window-0002"),
                ("conv-d", "window-0001"),
                ("conv-d", "window-0002"),
            ]
        )
    ]
    neighbors_by_key = {
        ("conv-a", "window-0001"): [
            ("conv-a", "window-0002", 0.99),
            ("conv-b", "window-0001", 0.91),
        ],
        ("conv-a", "window-0002"): [
            ("conv-a", "window-0001", 0.99),
        ],
        ("conv-b", "window-0001"): [
            ("conv-b", "window-0002", 0.99),
            ("conv-a", "window-0001", 0.91),
            ("conv-c", "window-0001", 0.80),
        ],
        ("conv-b", "window-0002"): [
            ("conv-b", "window-0001", 0.99),
        ],
        ("conv-c", "window-0001"): [
            ("conv-c", "window-0002", 0.99),
            ("conv-b", "window-0001", 0.80),
            ("conv-d", "window-0001", 0.92),
        ],
        ("conv-c", "window-0002"): [
            ("conv-c", "window-0001", 0.99),
        ],
        ("conv-d", "window-0001"): [
            ("conv-d", "window-0002", 0.99),
            ("conv-c", "window-0001", 0.92),
        ],
        ("conv-d", "window-0002"): [
            ("conv-d", "window-0001", 0.99),
        ],
    }
    neighbor_rows = [
        {
            "record_type": "window_neighbors",
            "schema_version": "0.1",
            "provider_id": "openai",
            "conversation_id": record.conversation_id,
            "window_id": record.window_id,
            "embedding_model": "local/test",
            "neighbor_count": len(
                neighbors_by_key[(record.conversation_id, record.window_id)]
            ),
            "neighbors": [
                {
                    "provider_id": "openai",
                    "conversation_id": conversation_id,
                    "window_id": window_id,
                    "score": score,
                }
                for conversation_id, window_id, score in neighbors_by_key[
                    (record.conversation_id, record.window_id)
                ]
            ],
        }
        for record in embeddings
    ]

    rows = build_window_cluster_rows(embeddings, neighbor_rows)
    cluster_ids = {
        (row["conversation_id"], row["window_id"]): row["cluster_id"] for row in rows
    }

    assert rows[0]["cluster_size"] == 4
    assert rows[2]["cluster_size"] == 4
    assert rows[4]["cluster_size"] == 4
    assert rows[6]["cluster_size"] == 4
    assert cluster_ids[("conv-a", "window-0001")] == cluster_ids[("conv-b", "window-0001")]
    assert cluster_ids[("conv-c", "window-0001")] == cluster_ids[("conv-d", "window-0001")]
    assert cluster_ids[("conv-a", "window-0001")] != cluster_ids[("conv-c", "window-0001")]


def test_build_window_cluster_rows_falls_back_to_legacy_cross_thread_behavior_without_scores():
    embeddings = [
        WindowEmbeddingRecord(
            source_path=Path("/tmp/thread-a/message_windows.jsonl"),
            provider_id="openai",
            conversation_id="conv-a",
            window_id="window-0001",
            ts_start=1,
            ts_end=2,
            embedding_model="local/test",
            text_char_count=10,
            embedding=(1.0, 0.0),
            message_ids=("m1",),
        ),
        WindowEmbeddingRecord(
            source_path=Path("/tmp/thread-b/message_windows.jsonl"),
            provider_id="openai",
            conversation_id="conv-b",
            window_id="window-0001",
            ts_start=3,
            ts_end=4,
            embedding_model="local/test",
            text_char_count=10,
            embedding=(1.0, 0.0),
            message_ids=("n1",),
        ),
    ]
    neighbor_rows = [
        {
            "record_type": "window_neighbors",
            "schema_version": "0.1",
            "provider_id": "openai",
            "conversation_id": "conv-a",
            "window_id": "window-0001",
            "embedding_model": "local/test",
            "neighbor_count": 1,
            "neighbors": [
                {
                    "provider_id": "openai",
                    "conversation_id": "conv-b",
                    "window_id": "window-0001",
                }
            ],
        },
        {
            "record_type": "window_neighbors",
            "schema_version": "0.1",
            "provider_id": "openai",
            "conversation_id": "conv-b",
            "window_id": "window-0001",
            "embedding_model": "local/test",
            "neighbor_count": 1,
            "neighbors": [
                {
                    "provider_id": "openai",
                    "conversation_id": "conv-a",
                    "window_id": "window-0001",
                }
            ],
        },
    ]

    rows = build_window_cluster_rows(embeddings, neighbor_rows)

    assert [row["cluster_size"] for row in rows] == [2, 2]


def test_analyze_semantic_prototype_cli_happy_path(tmp_path):
    root = tmp_path / "artifacts" / "openai"
    thread_a = root / "thread-conv-a" / "message_windows.jsonl"
    thread_b = root / "thread-conv-b" / "message_windows.jsonl"

    _write_jsonl(
        thread_a,
        [
            _window_row("openai", "conv-a", "window-0001", "alpha beta", ts_start=1, ts_end=2),
            _window_row("openai", "conv-a", "window-0002", "release note draft", ts_start=3, ts_end=4),
        ],
    )
    _write_jsonl(
        thread_b,
        [
            _window_row("openai", "conv-b", "window-0001", "alpha gamma", ts_start=5, ts_end=6),
            _window_row("openai", "conv-b", "window-0002", "database migration", ts_start=7, ts_end=8),
        ],
    )

    main(
        [
            "--locale",
            "en-US",
            "analyze",
            "semantic-prototype",
            "--input",
            str(tmp_path / "artifacts"),
            "--top-k",
            "1",
            "--min-score",
            "0.0",
        ]
    )

    embeddings_path = thread_a.with_name("window_embeddings.jsonl")
    neighbors_path = thread_a.with_name("window_neighbors.jsonl")
    clusters_path = thread_a.with_name("window_clusters.jsonl")

    assert embeddings_path.exists()
    assert neighbors_path.exists()
    assert clusters_path.exists()

    embedding_rows = [
        json.loads(line)
        for line in embeddings_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    neighbor_rows = [
        json.loads(line)
        for line in neighbors_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    cluster_rows = [
        json.loads(line)
        for line in clusters_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert embedding_rows[0]["record_type"] == "window_embedding"
    assert embedding_rows[0]["schema_version"] == "0.1"
    assert embedding_rows[0]["embedding_model"].startswith("deterministic/hash-bow-v1")
    assert neighbor_rows[0]["record_type"] == "window_neighbors"
    assert neighbor_rows[0]["schema_version"] == "0.1"
    assert neighbor_rows[0]["neighbor_count"] == 1
    assert cluster_rows[0]["record_type"] == "window_cluster_member"
    assert cluster_rows[0]["schema_version"] == "0.1"


def test_analyze_semantic_prototype_cli_accepts_parsed_jsonl_directly(tmp_path):
    parsed_path = tmp_path / "artifacts" / "openai" / "thread-conv-a" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed_path,
        provider_id="openai",
        conversation_id="conv-a",
        messages=[
            {
                "record_type": "message",
                "provider_id": "openai",
                "conversation_id": "conv-a",
                "message_id": "m1",
                "role": "user",
                "ts": 1,
                "text": "alpha beta",
                "content": {"content_type": "text", "parts": ["alpha beta"]},
            },
            {
                "record_type": "message",
                "provider_id": "openai",
                "conversation_id": "conv-a",
                "message_id": "m2",
                "role": "assistant",
                "ts": 2,
                "text": "release note draft",
                "content": {"content_type": "text", "parts": ["release note draft"]},
            },
        ],
    )

    main(
        [
            "--locale",
            "en-US",
            "analyze",
            "semantic-prototype",
            "--input",
            str(parsed_path),
            "--top-k",
            "1",
            "--min-score",
            "0.0",
        ]
    )

    embeddings_path = parsed_path.with_name("window_embeddings.jsonl")
    neighbors_path = parsed_path.with_name("window_neighbors.jsonl")
    clusters_path = parsed_path.with_name("window_clusters.jsonl")

    assert embeddings_path.exists()
    assert neighbors_path.exists()
    assert clusters_path.exists()

    embedding_rows = [
        json.loads(line)
        for line in embeddings_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(embedding_rows) == 1
    assert embedding_rows[0]["window_id"] == "window-0001"
    assert embedding_rows[0]["text_char_count"] == len("alpha beta\n\nrelease note draft")


def test_semantic_prototype_machine_outputs_are_locale_independent(tmp_path):
    def _build_artifacts(root: Path, *, locale: str) -> tuple[list[dict], list[dict], list[dict]]:
        parsed_path = root / "openai" / "thread-conv-a" / "parsed.jsonl"
        _write_parsed_jsonl(
            parsed_path,
            provider_id="openai",
            conversation_id="conv-a",
            messages=[
                {
                    "record_type": "message",
                    "provider_id": "openai",
                    "conversation_id": "conv-a",
                    "message_id": "m1",
                    "role": "user",
                    "ts": 1,
                    "text": "alpha beta",
                    "content": {"content_type": "text", "parts": ["alpha beta"]},
                },
                {
                    "record_type": "message",
                    "provider_id": "openai",
                    "conversation_id": "conv-a",
                    "message_id": "m2",
                    "role": "assistant",
                    "ts": 2,
                    "text": "release note draft",
                    "content": {"content_type": "text", "parts": ["release note draft"]},
                },
                {
                    "record_type": "message",
                    "provider_id": "openai",
                    "conversation_id": "conv-a",
                    "message_id": "m3",
                    "role": "user",
                    "ts": 3,
                    "text": "database migration",
                    "content": {"content_type": "text", "parts": ["database migration"]},
                },
                {
                    "record_type": "message",
                    "provider_id": "openai",
                    "conversation_id": "conv-a",
                    "message_id": "m4",
                    "role": "assistant",
                    "ts": 4,
                    "text": "rollback checklist",
                    "content": {"content_type": "text", "parts": ["rollback checklist"]},
                },
                {
                    "record_type": "message",
                    "provider_id": "openai",
                    "conversation_id": "conv-a",
                    "message_id": "m5",
                    "role": "user",
                    "ts": 5,
                    "text": "follow-up notes",
                    "content": {"content_type": "text", "parts": ["follow-up notes"]},
                },
            ],
        )
        set_locale(locale)
        analyze_semantic_prototype(
            parsed_path,
            top_k=1,
            min_score=0.0,
            backend=DeterministicHashEmbeddingBackend(dim=4),
        )
        return (
            [
                json.loads(line)
                for line in parsed_path.with_name("window_embeddings.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip()
            ],
            [
                json.loads(line)
                for line in parsed_path.with_name("window_neighbors.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip()
            ],
            [
                json.loads(line)
                for line in parsed_path.with_name("window_clusters.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip()
            ],
        )

    en_rows = _build_artifacts(tmp_path / "en", locale="en-US")
    ja_rows = _build_artifacts(tmp_path / "ja", locale="ja-JP")

    assert en_rows == ja_rows


def test_analyze_semantic_prototype_logs_major_phases(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    root = tmp_path / "artifacts" / "openai" / "thread-conv-a" / "message_windows.jsonl"
    _write_jsonl(
        root,
        [
            _window_row("openai", "conv-a", "window-0001", "alpha beta", ts_start=1, ts_end=2),
            _window_row("openai", "conv-a", "window-0002", "beta gamma", ts_start=3, ts_end=4),
        ],
    )

    main(
        [
            "--locale",
            "en-US",
            "analyze",
            "semantic-prototype",
            "--input",
            str(tmp_path / "artifacts"),
            "--top-k",
            "1",
            "--overwrite",
        ]
    )

    assert "semantic prototype: loading windows" in caplog.text
    assert "semantic prototype: loaded 2 windows" in caplog.text
    assert "semantic prototype: generating embeddings" in caplog.text
    assert "semantic prototype: embeddings complete (2 windows)" in caplog.text
    assert "semantic prototype: building neighbors" in caplog.text
    assert "semantic prototype: building clusters" in caplog.text
    assert "semantic prototype: writing artifacts" in caplog.text


def test_default_backend_resolves_to_deterministic_hash():
    backend = resolve_embedding_backend(
        backend_name="deterministic-hash",
        model=None,
    )

    assert backend.model_id.startswith("deterministic/hash-bow-v1")


def test_known_model_compatibility_fallback_and_safe_default_fallback():
    nomic = resolve_embedding_model_settings(model="nomic-embed-text-v2-moe")
    unknown = resolve_embedding_model_settings(model="unknown-local-model")

    assert nomic.max_input_bytes == 512
    assert nomic.chunk_overlap_bytes == 64
    assert nomic.aggregate == "mean"
    assert unknown == DEFAULT_EMBEDDING_SETTINGS


def test_create_embedding_backend_returns_correct_backend_implementations():
    deterministic = create_embedding_backend(backend_name="deterministic-hash")
    ollama = create_embedding_backend(
        backend_name="ollama",
        model="embeddinggemma",
        settings=resolve_embedding_model_settings(
            max_input_bytes=2048,
            chunk_overlap_bytes=128,
            aggregate="mean",
        ),
        backend_options={
            "base_url": "http://localhost:22434",
            "timeout_seconds": 12.5,
        },
    )

    assert isinstance(deterministic, DeterministicHashEmbeddingBackend)
    assert isinstance(ollama, OllamaEmbeddingBackend)
    assert ollama.base_url == "http://localhost:22434"
    assert ollama.timeout_seconds == 12.5


def test_chunk_text_for_embedding_is_deterministic_and_aggregate_is_mean():
    chunks = chunk_text_for_embedding(
        "abcdefgh",
        max_input_bytes=4,
        chunk_overlap_bytes=1,
    )

    assert chunks == ["abcd", "defg", "gh"]
    assert chunk_text_for_embedding(
        "abcdefgh",
        max_input_bytes=4,
        chunk_overlap_bytes=1,
    ) == chunks
    assert aggregate_embeddings([[1.0, 3.0], [3.0, 5.0]], aggregate="mean") == [2.0, 4.0]


def test_cli_ollama_backend_requires_model(tmp_path, caplog):
    root = tmp_path / "artifacts" / "openai" / "thread-conv-a" / "message_windows.jsonl"
    _write_jsonl(
        root,
        [_window_row("openai", "conv-a", "window-0001", "alpha beta", ts_start=1, ts_end=2)],
    )

    with pytest.raises(SystemExit) as exc:
        main(
            [
                "analyze",
                "semantic-prototype",
                "--input",
                str(tmp_path / "artifacts"),
                "--backend",
                "ollama",
            ]
        )

    assert exc.value.code == 2
    assert "--backend ollama requires --model" in caplog.text


def test_ollama_embedding_backend_returns_vectors(monkeypatch):
    del monkeypatch

    with patch("llm_logparser.core.embedding_backend.OllamaClient") as client_cls:
        client = client_cls.return_value
        client.embeddings.side_effect = [[0.1, 0.2], [0.3, 0.4]]

        backend = OllamaEmbeddingBackend("embeddinggemma")
        vectors = backend.embed(["alpha", "beta"])

    assert backend.model_id == "ollama/embeddinggemma"
    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    client_cls.assert_called_once_with(
        base_url="http://localhost:11434",
        timeout=30.0,
    )
    assert client.embeddings.call_args_list == [
        call(model="embeddinggemma", prompt="alpha"),
        call(model="embeddinggemma", prompt="beta"),
    ]


def test_ollama_embedding_backend_chunks_long_input_and_aggregates(monkeypatch):
    del monkeypatch

    with patch("llm_logparser.core.embedding_backend.OllamaClient") as client_cls:
        client = client_cls.return_value
        client.embeddings.side_effect = [[1.0, 1.0], [3.0, 3.0], [5.0, 5.0]]

        backend = OllamaEmbeddingBackend(
            "nomic-embed-text-v2-moe",
            settings=resolve_embedding_model_settings(
                "nomic-embed-text-v2-moe",
                max_input_bytes=4,
                chunk_overlap_bytes=1,
            ),
        )

        vectors = backend.embed(["abcdefgh"])

    assert client.embeddings.call_args_list == [
        call(model="nomic-embed-text-v2-moe", prompt="abcd"),
        call(model="nomic-embed-text-v2-moe", prompt="defg"),
        call(model="nomic-embed-text-v2-moe", prompt="gh"),
    ]
    assert vectors == [[3.0, 3.0]]


def test_ollama_embedding_backend_rejects_malformed_response(monkeypatch):
    del monkeypatch

    with patch("llm_logparser.core.embedding_backend.OllamaClient") as client_cls:
        client = client_cls.return_value
        client.embeddings.return_value = []

        backend = OllamaEmbeddingBackend("embeddinggemma")

        with pytest.raises(RuntimeError, match="invalid vector at index 0"):
            backend.embed(["alpha"])


def test_ollama_embedding_backend_embeds_empty_text_with_compatibility_prompt():
    with patch(
        "llm_logparser.core.embedding_backend.OllamaClient._post",
        return_value={"embedding": [0.1, 0.2]},
    ) as post_mock:
        backend = OllamaEmbeddingBackend("nomic-embed-text-v2-moe")
        vectors = backend.embed([""])

    assert vectors == [[0.1, 0.2]]
    post_mock.assert_called_once_with(
        "/api/embeddings",
        {"model": "nomic-embed-text-v2-moe", "prompt": " "},
    )


def test_analyze_semantic_prototype_cli_with_ollama_backend(tmp_path, monkeypatch):
    root = tmp_path / "artifacts" / "openai"
    thread_a = root / "thread-conv-a" / "message_windows.jsonl"
    thread_b = root / "thread-conv-b" / "message_windows.jsonl"

    _write_jsonl(
        thread_a,
        [
            _window_row("openai", "conv-a", "window-0001", "alpha beta", ts_start=1, ts_end=2),
            _window_row("openai", "conv-a", "window-0002", "release note draft", ts_start=3, ts_end=4),
        ],
    )
    _write_jsonl(
        thread_b,
        [
            _window_row("openai", "conv-b", "window-0001", "alpha gamma", ts_start=5, ts_end=6),
            _window_row("openai", "conv-b", "window-0002", "database migration", ts_start=7, ts_end=8),
        ],
    )

    vectors = [
        [1.0, 0.0],
        [0.0, 1.0],
        [0.9, 0.1],
        [0.1, 0.9],
    ]

    del monkeypatch

    with patch("llm_logparser.core.embedding_backend.OllamaClient") as client_cls:
        client = client_cls.return_value
        client.embeddings.side_effect = vectors

        main(
            [
                "analyze",
                "semantic-prototype",
                "--input",
                str(tmp_path / "artifacts"),
                "--backend",
                "ollama",
                "--model",
                "embeddinggemma",
                "--top-k",
                "1",
            ]
        )

    embeddings_path = thread_a.with_name("window_embeddings.jsonl")
    neighbors_path = thread_a.with_name("window_neighbors.jsonl")
    embedding_rows = [
        json.loads(line)
        for line in embeddings_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    neighbor_rows = [
        json.loads(line)
        for line in neighbors_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert embedding_rows[0]["embedding_model"] == "ollama/embeddinggemma"
    assert embedding_rows[0]["embedding_dim"] == 2
    assert neighbor_rows[0]["embedding_model"] == "ollama/embeddinggemma"
    assert neighbor_rows[0]["neighbor_count"] == 1
    assert neighbor_rows[0]["neighbors"][0]["conversation_id"] == "conv-b"
    client_cls.assert_called_once_with(
        base_url="http://localhost:11434",
        timeout=30.0,
    )
    assert client.embeddings.call_args_list == [
        call(model="embeddinggemma", prompt="alpha beta"),
        call(model="embeddinggemma", prompt="release note draft"),
        call(model="embeddinggemma", prompt="alpha gamma"),
        call(model="embeddinggemma", prompt="database migration"),
    ]


def test_semantic_prototype_reads_config_profile_settings(tmp_path, monkeypatch):
    root = tmp_path / "artifacts" / "openai" / "thread-conv-a" / "message_windows.jsonl"
    _write_jsonl(
        root,
        [
            _window_row("openai", "conv-a", "window-0001", "abcdefgh", ts_start=1, ts_end=2),
            _window_row("openai", "conv-a", "window-0002", "ijklmnop", ts_start=3, ts_end=4),
        ],
    )

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "active_profile: local",
                "profiles:",
                "  local:",
                "    input:",
                f"      path: {tmp_path / 'artifacts'}",
                "    analyze:",
                "      semantic_prototype:",
                "        backend: ollama",
                "        model: nomic-embed-text-v2-moe",
                "        top_k: 1",
                "        backend_options:",
                "          base_url: http://localhost:22434",
                "          timeout_seconds: 12.5",
                "        embedding:",
                "          max_input_bytes: 4",
                "          chunk_overlap_bytes: 1",
                "          aggregate: mean",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    del monkeypatch

    with patch("llm_logparser.core.embedding_backend.OllamaClient") as client_cls:
        client = client_cls.return_value
        client.embeddings.return_value = [1.0, 0.0]

        main(
            [
                "--config",
                str(config_path),
                "analyze",
                "semantic-prototype",
                "--overwrite",
            ]
        )

    embedding_rows = [
        json.loads(line)
        for line in root.with_name("window_embeddings.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert embedding_rows[0]["embedding_model"] == "ollama/nomic-embed-text-v2-moe"
    client_cls.assert_called_once_with(
        base_url="http://localhost:22434",
        timeout=12.5,
    )
    assert client.embeddings.call_args_list == [
        call(model="nomic-embed-text-v2-moe", prompt="abcd"),
        call(model="nomic-embed-text-v2-moe", prompt="defg"),
        call(model="nomic-embed-text-v2-moe", prompt="gh"),
        call(model="nomic-embed-text-v2-moe", prompt="ijkl"),
        call(model="nomic-embed-text-v2-moe", prompt="lmno"),
        call(model="nomic-embed-text-v2-moe", prompt="op"),
    ]


def test_semantic_prototype_uses_safe_defaults_when_config_omits_embedding_overrides(
    tmp_path, monkeypatch
):
    root = tmp_path / "artifacts" / "openai" / "thread-conv-a" / "message_windows.jsonl"
    _write_jsonl(
        root,
        [
            _window_row("openai", "conv-a", "window-0001", "alpha beta", ts_start=1, ts_end=2),
        ],
    )

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "active_profile: local",
                "profiles:",
                "  local:",
                "    input:",
                f"      path: {tmp_path / 'artifacts'}",
                "    analyze:",
                "      semantic_prototype:",
                "        backend: ollama",
                "        model: custom-local-embedder",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    del monkeypatch

    with patch("llm_logparser.core.embedding_backend.OllamaClient") as client_cls:
        client = client_cls.return_value
        client.embeddings.return_value = [1.0, 0.0]

        main(
            [
                "--config",
                str(config_path),
                "analyze",
                "semantic-prototype",
                "--overwrite",
            ]
        )

    client_cls.assert_called_once_with(
        base_url="http://localhost:11434",
        timeout=30.0,
    )
    assert client.embeddings.call_args_list == [
        call(model="custom-local-embedder", prompt="alpha beta")
    ]


def test_semantic_prototype_cli_overrides_config(tmp_path, monkeypatch):
    root = tmp_path / "artifacts" / "openai"
    thread_a = root / "thread-conv-a" / "message_windows.jsonl"
    thread_b = root / "thread-conv-b" / "message_windows.jsonl"
    _write_jsonl(
        thread_a,
        [
            _window_row("openai", "conv-a", "window-0001", "abcdefgh", ts_start=1, ts_end=2),
            _window_row("openai", "conv-a", "window-0002", "ijklmnop", ts_start=3, ts_end=4),
        ],
    )
    _write_jsonl(
        thread_b,
        [
            _window_row("openai", "conv-b", "window-0001", "qrstuvwx", ts_start=5, ts_end=6),
            _window_row("openai", "conv-b", "window-0002", "yzabcdef", ts_start=7, ts_end=8),
        ],
    )

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "active_profile: local",
                "profiles:",
                "  local:",
                "    input:",
                f"      path: {tmp_path / 'artifacts'}",
                "    analyze:",
                "      semantic_prototype:",
                "        backend: ollama",
                "        model: nomic-embed-text-v2-moe",
                "        top_k: 2",
                "        embedding:",
                "          max_input_bytes: 4",
                "          chunk_overlap_bytes: 1",
                "          aggregate: mean",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    del monkeypatch

    with patch("llm_logparser.core.embedding_backend.OllamaClient") as client_cls:
        client = client_cls.return_value
        client.embeddings.return_value = [1.0, 0.0]

        main(
            [
                "--config",
                str(config_path),
                "analyze",
                "semantic-prototype",
                "--top-k",
                "1",
                "--model",
                "embeddinggemma",
                "--overwrite",
            ]
        )

    neighbor_rows = [
        json.loads(line)
        for line in thread_a.with_name("window_neighbors.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    embedding_rows = [
        json.loads(line)
        for line in thread_a.with_name("window_embeddings.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert neighbor_rows[0]["neighbor_count"] == 1
    assert embedding_rows[0]["embedding_model"] == "ollama/embeddinggemma"
    client_cls.assert_called_once_with(
        base_url="http://localhost:11434",
        timeout=30.0,
    )


def test_analyze_semantic_prototype_neighbor_progress_for_large_runs(tmp_path):
    root = tmp_path / "artifacts" / "openai" / "thread-conv-a" / "message_windows.jsonl"
    rows = [
        _window_row(
            "openai",
            "conv-a",
            f"window-{index:04d}",
            f"text {index}",
            ts_start=index,
            ts_end=index,
        )
        for index in range(500)
    ]
    _write_jsonl(root, rows)

    progress_messages: list[str] = []
    result = analyze_semantic_prototype(
        tmp_path / "artifacts",
        top_k=1,
        overwrite=True,
        backend=DeterministicHashEmbeddingBackend(dim=4),
        progress=progress_messages.append,
    )

    assert result["windows"] == 500
    assert "semantic prototype: neighbors 500 / 500" in progress_messages
