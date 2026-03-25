from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from llm_logparser.cli.cli import main
from llm_logparser.core.analyzer_semantic_prototype import (
    MessageWindowRecord,
    analyze_semantic_prototype,
    build_window_embedding_records,
    build_window_neighbor_rows,
    cosine_similarity,
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
    resolve_embedding_model_settings,
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

    rows = build_window_neighbor_rows(embeddings, top_k=2)

    assert rows[1]["neighbors"][0]["window_id"] == "window-0003"
    assert rows[1]["neighbors"][1]["window_id"] == "window-0002"
    assert all(
        neighbor["window_id"] != rows[1]["window_id"]
        for neighbor in rows[1]["neighbors"]
    )


def test_build_window_neighbor_rows_progress_callback():
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
            "--locale",
            "en-US",
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
    assert "semantic prototype: writing artifacts" in caplog.text


def test_default_backend_resolves_to_deterministic_hash():
    backend = resolve_embedding_backend(
        backend_name="deterministic-hash",
        model=None,
    )

    assert backend.model_id.startswith("deterministic/hash-bow-v1")


def test_known_model_preset_and_unknown_model_fallback():
    nomic = resolve_embedding_model_settings("nomic-embed-text-v2-moe")
    unknown = resolve_embedding_model_settings("unknown-local-model")

    assert nomic.max_input_bytes == 512
    assert nomic.chunk_overlap_bytes == 64
    assert nomic.aggregate == "mean"
    assert unknown == DEFAULT_EMBEDDING_SETTINGS


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
    captured_requests: list[dict[str, object]] = []

    def _fake_urlopen(request, timeout):
        captured_requests.append(
            {
                "url": request.full_url,
                "timeout": timeout,
                "body": json.loads(request.data.decode("utf-8")),
            }
        )
        body = captured_requests[-1]["body"]
        if body["input"] == ["alpha"]:
            return _FakeHTTPResponse({"embeddings": [[0.1, 0.2]]})
        return _FakeHTTPResponse({"embeddings": [[0.3, 0.4]]})

    monkeypatch.setattr(
        "llm_logparser.core.embedding_backend.urllib_request.urlopen",
        _fake_urlopen,
    )

    backend = OllamaEmbeddingBackend("embeddinggemma")
    vectors = backend.embed(["alpha", "beta"])

    assert backend.model_id == "ollama/embeddinggemma"
    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert [request["url"] for request in captured_requests] == [
        "http://localhost:11434/api/embed",
        "http://localhost:11434/api/embed",
    ]
    assert [request["body"] for request in captured_requests] == [
        {
            "model": "embeddinggemma",
            "input": ["alpha"],
        },
        {
            "model": "embeddinggemma",
            "input": ["beta"],
        },
    ]


def test_ollama_embedding_backend_chunks_long_input_and_aggregates(monkeypatch):
    captured_requests: list[dict[str, object]] = []

    def _fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        captured_requests.append(body)
        assert body["input"] == ["abcd", "defg", "gh"]
        return _FakeHTTPResponse({"embeddings": [[1.0, 1.0], [3.0, 3.0], [5.0, 5.0]]})

    monkeypatch.setattr(
        "llm_logparser.core.embedding_backend.urllib_request.urlopen",
        _fake_urlopen,
    )

    backend = OllamaEmbeddingBackend(
        "nomic-embed-text-v2-moe",
        settings=resolve_embedding_model_settings(
            "nomic-embed-text-v2-moe",
            max_input_bytes=4,
            chunk_overlap_bytes=1,
        ),
    )

    vectors = backend.embed(["abcdefgh"])

    assert len(captured_requests) == 1
    assert vectors == [[3.0, 3.0]]


def test_ollama_embedding_backend_rejects_malformed_response(monkeypatch):
    monkeypatch.setattr(
        "llm_logparser.core.embedding_backend.urllib_request.urlopen",
        lambda request, timeout: _FakeHTTPResponse({"embedding": [0.1, 0.2]}),
    )

    backend = OllamaEmbeddingBackend("embeddinggemma")

    with pytest.raises(RuntimeError, match="missing 'embeddings'"):
        backend.embed(["alpha"])


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

    captured_requests: list[list[str]] = []

    def _fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        assert body["model"] == "embeddinggemma"
        captured_requests.append(body["input"])
        vector = vectors[len(captured_requests) - 1]
        return _FakeHTTPResponse({"embeddings": [vector]})

    monkeypatch.setattr(
        "llm_logparser.core.embedding_backend.urllib_request.urlopen",
        _fake_urlopen,
    )

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
    assert captured_requests == [
        ["alpha beta"],
        ["release note draft"],
        ["alpha gamma"],
        ["database migration"],
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
                "        embedding:",
                "          max_input_bytes: 4",
                "          chunk_overlap_bytes: 1",
                "          aggregate: mean",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    def _fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        assert body["model"] == "nomic-embed-text-v2-moe"
        assert body["input"] in (["abcd", "defg", "gh"], ["ijkl", "lmno", "op"])
        return _FakeHTTPResponse({"embeddings": [[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]]})

    monkeypatch.setattr(
        "llm_logparser.core.embedding_backend.urllib_request.urlopen",
        _fake_urlopen,
    )

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

    def _fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        chunk_count = len(body["input"])
        return _FakeHTTPResponse({"embeddings": [[1.0, 0.0]] * chunk_count})

    monkeypatch.setattr(
        "llm_logparser.core.embedding_backend.urllib_request.urlopen",
        _fake_urlopen,
    )

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
