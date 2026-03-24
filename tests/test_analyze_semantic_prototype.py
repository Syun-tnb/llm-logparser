from __future__ import annotations

import json
from pathlib import Path

from llm_logparser.cli.cli import main
from llm_logparser.core.analyzer_semantic_prototype import (
    MessageWindowRecord,
    build_window_embedding_records,
    build_window_neighbor_rows,
    cosine_similarity,
    discover_message_windows_jsonl,
    load_message_window_records,
    render_window_embedding_row,
)
from llm_logparser.core.schema_validation import (
    load_window_embedding_validator,
    load_window_neighbors_validator,
)


def _window_row(
    provider_id: str,
    conversation_id: str,
    window_id: str,
    text: str,
    *,
    ts_start: int | None = None,
    ts_end: int | None = None,
) -> dict:
    return {
        "record_type": "message_window",
        "schema_version": "1.0",
        "provider_id": provider_id,
        "conversation_id": conversation_id,
        "window_id": window_id,
        "message_ids": [f"{window_id}-m1"],
        "roles": ["user"],
        "message_count": 1,
        "char_count": len(text),
        "ts_start": ts_start,
        "ts_end": ts_end,
        "text": text,
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


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
        ),
        MessageWindowRecord(
            source_path=windows_path,
            provider_id="openai",
            conversation_id="conv-a",
            window_id="window-0002",
            ts_start=3,
            ts_end=4,
            text="beta gamma",
        ),
    ]


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

    rows = build_window_neighbor_rows(embeddings, top_k=2)

    assert rows[0]["window_id"] == "window-0001"
    assert rows[0]["neighbor_count"] == 2
    assert rows[0]["neighbors"][0]["conversation_id"] == "conv-b"
    assert rows[0]["neighbors"][1]["conversation_id"] == "conv-c"
    assert rows[0]["neighbors"][0]["score"] > rows[0]["neighbors"][1]["score"]


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

    embedding_validator = load_window_embedding_validator()
    neighbor_validator = load_window_neighbors_validator()

    assert list(embedding_validator.iter_errors(embedding_row)) == []
    assert list(neighbor_validator.iter_errors(neighbor_row)) == []


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
            "analyze",
            "semantic-prototype",
            "--input",
            str(tmp_path / "artifacts"),
            "--top-k",
            "1",
        ]
    )

    embeddings_path = thread_a.with_name("window_embeddings.jsonl")
    neighbors_path = thread_a.with_name("window_neighbors.jsonl")

    assert embeddings_path.exists()
    assert neighbors_path.exists()

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

    assert embedding_rows[0]["record_type"] == "window_embedding"
    assert embedding_rows[0]["schema_version"] == "0.1"
    assert embedding_rows[0]["embedding_model"].startswith("deterministic/hash-bow-v1")
    assert neighbor_rows[0]["record_type"] == "window_neighbors"
    assert neighbor_rows[0]["schema_version"] == "0.1"
    assert neighbor_rows[0]["neighbor_count"] == 1
