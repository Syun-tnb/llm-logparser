from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from llm_logparser.cli.cli import main
from llm_logparser.core.analyzer_semantic_topics import (
    build_semantic_topics_artifact,
    write_semantic_topics_artifacts,
)
from llm_logparser.core.schema_validation import (
    load_topic_membership_validator,
    load_topics_validator,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def _message_window_row(
    conversation_id: str,
    window_id: str,
    *,
    message_ids: list[str],
    roles: list[str],
    text: str,
    ts_start: int,
    ts_end: int,
) -> dict:
    return {
        "record_type": "message_window",
        "schema_version": "1.0",
        "provider_id": "openai",
        "conversation_id": conversation_id,
        "window_id": window_id,
        "message_ids": message_ids,
        "roles": roles,
        "message_count": len(roles),
        "char_count": len(text),
        "ts_start": ts_start,
        "ts_end": ts_end,
        "text": text,
    }


def _window_cluster_row(
    conversation_id: str,
    window_id: str,
    *,
    cluster_id: str,
    cluster_size: int,
) -> dict:
    return {
        "record_type": "window_cluster_member",
        "schema_version": "0.1",
        "provider_id": "openai",
        "conversation_id": conversation_id,
        "window_id": window_id,
        "cluster_id": cluster_id,
        "cluster_size": cluster_size,
        "edge_policy": "mutual-only",
    }


class _FakeHTTPResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def _write_topics_fixture(root: Path) -> None:
    thread_a = root / "thread-conv-a"
    thread_b = root / "thread-conv-b"
    thread_c = root / "thread-conv-c"

    _write_jsonl(
        thread_a / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-a",
                "window-0001",
                message_ids=["a-1", "a-2"],
                roles=["user", "assistant"],
                text=(
                    "user: Draft the production migration checklist\n\n"
                    "assistant: Include schema audit and rollback steps"
                ),
                ts_start=100,
                ts_end=120,
            ),
            _message_window_row(
                "conv-a",
                "window-0002",
                message_ids=["a-3"],
                roles=["user"],
                text="user: Capture monitoring gates for rollout readiness",
                ts_start=130,
                ts_end=140,
            ),
        ],
    )
    _write_jsonl(
        thread_b / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-b",
                "window-0001",
                message_ids=["b-1", "b-2"],
                roles=["assistant", "user"],
                text=(
                    "assistant: Review launch risk controls\n\n"
                    "user: Add deployment rollback checks"
                ),
                ts_start=150,
                ts_end=170,
            )
        ],
    )
    _write_jsonl(
        thread_c / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-c",
                "window-0001",
                message_ids=["c-1"],
                roles=["user"],
                text="user: Plan lunch options for next week",
                ts_start=200,
                ts_end=205,
            ),
            _message_window_row(
                "conv-c",
                "window-0002",
                message_ids=["c-2"],
                roles=["assistant"],
                text="assistant: Compare ramen shops and cafe seating",
                ts_start=206,
                ts_end=210,
            ),
        ],
    )

    _write_jsonl(
        thread_a / "window_clusters.jsonl",
        [
            _window_cluster_row(
                "conv-a",
                "window-0001",
                cluster_id="cluster_000001",
                cluster_size=3,
            ),
            _window_cluster_row(
                "conv-a",
                "window-0002",
                cluster_id="cluster_000001",
                cluster_size=3,
            ),
        ],
    )
    _write_jsonl(
        thread_b / "window_clusters.jsonl",
        [
            _window_cluster_row(
                "conv-b",
                "window-0001",
                cluster_id="cluster_000001",
                cluster_size=3,
            )
        ],
    )
    _write_jsonl(
        thread_c / "window_clusters.jsonl",
        [
            _window_cluster_row(
                "conv-c",
                "window-0001",
                cluster_id="cluster_000002",
                cluster_size=2,
            ),
            _window_cluster_row(
                "conv-c",
                "window-0002",
                cluster_id="cluster_000002",
                cluster_size=2,
            ),
        ],
    )


def test_write_semantic_topics_artifacts_happy_path(tmp_path, monkeypatch):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_topics_fixture(root)

    monkeypatch.setattr(
        "llm_logparser.core.analyzer_semantic_topic.urllib_request.urlopen",
        lambda request, timeout: _FakeHTTPResponse(
            {
                "response": json.dumps(
                    {
                        "topic_label": "Launch Readiness",
                        "summary": "Deployment readiness and rollback planning dominate the topic.",
                        "keywords": ["launch", "rollback", "monitoring"],
                    }
                )
            }
        ),
    )

    result = write_semantic_topics_artifacts(
        root,
        model="llama3.1:latest",
        cluster_id="cluster_000001",
    )

    topics_path = Path(result["topics_path"])
    membership_path = Path(result["membership_path"])
    topics_payload = json.loads(topics_path.read_text(encoding="utf-8"))
    membership_rows = [
        json.loads(line)
        for line in membership_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert result["topic_count"] == 1
    assert result["label_mode"] == "model-enriched"
    assert topics_payload["generation"]["label_mode"] == "model-enriched"
    assert topics_payload["topics"][0]["label"] == "Launch Readiness"
    assert topics_payload["topics"][0]["summary"] == (
        "Deployment readiness and rollback planning dominate the topic."
    )
    assert topics_payload["topics"][0]["keywords"] == [
        "launch",
        "rollback",
        "monitoring",
    ]
    assert topics_payload["topics"][0]["cluster_ids"] == ["cluster_000001"]
    assert topics_payload["topics"][0]["first_seen"] == 100
    assert topics_payload["topics"][0]["last_seen"] == 170

    topics_validator = load_topics_validator()
    membership_validator = load_topic_membership_validator()
    assert list(topics_validator.iter_errors(topics_payload)) == []
    assert all(list(membership_validator.iter_errors(row)) == [] for row in membership_rows)


def test_semantic_topics_reverse_lookup_and_deterministic_topic_ids(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_topics_fixture(root)

    artifact_a, membership_rows_a = build_semantic_topics_artifact(root)
    artifact_b, membership_rows_b = build_semantic_topics_artifact(root)

    topic_a = next(
        topic
        for topic in artifact_a["topics"]
        if topic["cluster_ids"] == ["cluster_000001"]
    )
    topic_b = next(
        topic
        for topic in artifact_b["topics"]
        if topic["cluster_ids"] == ["cluster_000001"]
    )

    assert topic_a["topic_id"] == topic_b["topic_id"]
    assert topic_a["label"] is None
    assert topic_a["summary"] is None
    assert topic_a["keywords"] == []

    cluster_rows = [
        row
        for row in membership_rows_a
        if row["membership_type"] == "cluster" and row["cluster_id"] == "cluster_000001"
    ]
    window_rows = [
        row
        for row in membership_rows_a
        if row["membership_type"] == "window"
        and row["cluster_id"] == "cluster_000001"
        and row["window_id"] == "window-0001"
    ]
    message_rows = [
        row
        for row in membership_rows_a
        if row["membership_type"] == "message" and row["message_id"] == "a-1"
    ]

    assert len(cluster_rows) == 1
    assert cluster_rows[0]["topic_id"] == topic_a["topic_id"]
    assert len(window_rows) == 2
    assert {row["topic_id"] for row in window_rows} == {topic_a["topic_id"]}
    assert len(message_rows) == 1
    assert message_rows[0]["topic_id"] == topic_a["topic_id"]
    assert membership_rows_a == membership_rows_b


def test_semantic_topics_structural_only_without_optional_model_or_neighbors(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_topics_fixture(root)

    result = write_semantic_topics_artifacts(root)
    topics_payload = json.loads(Path(result["topics_path"]).read_text(encoding="utf-8"))

    assert result["label_mode"] == "structural-only"
    assert topics_payload["generation"]["label_mode"] == "structural-only"
    assert topics_payload["generation"]["model"] is None
    assert all(topic["label"] is None for topic in topics_payload["topics"])
    assert all(topic["summary"] is None for topic in topics_payload["topics"])
    assert all(topic["keywords"] == [] for topic in topics_payload["topics"])


def test_analyze_semantic_topics_cli_happy_path(tmp_path, caplog):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_topics_fixture(root)

    caplog.set_level(logging.INFO)
    main(
        [
            "--locale",
            "en-US",
            "analyze",
            "semantic-topics",
            "--input",
            str(root),
            "--cluster-id",
            "cluster_000001",
        ]
    )

    assert "semantic topics artifacts written" in caplog.text
    assert "structural-only" in caplog.text
    assert (root / "l3" / "semantic-topics" / "topics.json").exists()
    assert (root / "l3" / "semantic-topics" / "topic_membership.jsonl").exists()
