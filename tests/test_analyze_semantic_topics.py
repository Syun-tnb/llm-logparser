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


def _window_neighbor_row(
    conversation_id: str,
    window_id: str,
    *,
    embedding_model: str,
    neighbors: list[dict],
) -> dict:
    return {
        "record_type": "window_neighbors",
        "schema_version": "0.1",
        "provider_id": "openai",
        "conversation_id": conversation_id,
        "window_id": window_id,
        "embedding_model": embedding_model,
        "neighbor_count": len(neighbors),
        "neighbors": neighbors,
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


def _write_representative_selection_fixture(root: Path) -> None:
    thread_a = root / "thread-conv-a"
    thread_b = root / "thread-conv-b"
    thread_c = root / "thread-conv-c"
    thread_d = root / "thread-conv-d"

    _write_jsonl(
        thread_a / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-a",
                "window-0001",
                message_ids=["a-1"],
                roles=["user"],
                text="user: quick note about release prep",
                ts_start=10,
                ts_end=12,
            )
        ],
    )
    _write_jsonl(
        thread_b / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-b",
                "window-0001",
                message_ids=["b-1"],
                roles=["assistant"],
                text=(
                    "assistant: long noisy aside about launch context, related ideas, "
                    "and extra prose that is less central to the rollout checklist"
                ),
                ts_start=20,
                ts_end=22,
            )
        ],
    )
    _write_jsonl(
        thread_c / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-c",
                "window-0001",
                message_ids=["c-1", "c-2"],
                roles=["user", "assistant"],
                text=(
                    "user: finalize rollout checklist and rollback guardrails\n\n"
                    "assistant: confirm monitoring gates and deployment checks"
                ),
                ts_start=30,
                ts_end=35,
            )
        ],
    )
    _write_jsonl(
        thread_d / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-d",
                "window-0001",
                message_ids=["d-1"],
                roles=["user"],
                text="user: standalone checkpoint note",
                ts_start=40,
                ts_end=41,
            )
        ],
    )

    _write_jsonl(
        thread_a / "window_clusters.jsonl",
        [
            _window_cluster_row(
                "conv-a",
                "window-0001",
                cluster_id="cluster_000010",
                cluster_size=3,
            )
        ],
    )
    _write_jsonl(
        thread_b / "window_clusters.jsonl",
        [
            _window_cluster_row(
                "conv-b",
                "window-0001",
                cluster_id="cluster_000010",
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
                cluster_id="cluster_000010",
                cluster_size=3,
            )
        ],
    )
    _write_jsonl(
        thread_d / "window_clusters.jsonl",
        [
            _window_cluster_row(
                "conv-d",
                "window-0001",
                cluster_id="cluster_000020",
                cluster_size=1,
            )
        ],
    )

    _write_jsonl(
        thread_a / "window_neighbors.jsonl",
        [
            _window_neighbor_row(
                "conv-a",
                "window-0001",
                embedding_model="ollama/nomic-embed-text-v2-moe",
                neighbors=[
                    {
                        "provider_id": "openai",
                        "conversation_id": "conv-c",
                        "window_id": "window-0001",
                        "score": 0.88,
                    }
                ],
            )
        ],
    )
    _write_jsonl(
        thread_b / "window_neighbors.jsonl",
        [
            _window_neighbor_row(
                "conv-b",
                "window-0001",
                embedding_model="ollama/nomic-embed-text-v2-moe",
                neighbors=[
                    {
                        "provider_id": "openai",
                        "conversation_id": "conv-c",
                        "window_id": "window-0001",
                        "score": 0.75,
                    }
                ],
            )
        ],
    )
    _write_jsonl(
        thread_c / "window_neighbors.jsonl",
        [
            _window_neighbor_row(
                "conv-c",
                "window-0001",
                embedding_model="ollama/nomic-embed-text-v2-moe",
                neighbors=[
                    {
                        "provider_id": "openai",
                        "conversation_id": "conv-a",
                        "window_id": "window-0001",
                        "score": 0.88,
                    },
                    {
                        "provider_id": "openai",
                        "conversation_id": "conv-b",
                        "window_id": "window-0001",
                        "score": 0.75,
                    },
                ],
            )
        ],
    )


def test_write_semantic_topics_artifacts_happy_path(tmp_path, monkeypatch):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_topics_fixture(root)
    _write_jsonl(
        root / "thread-conv-a" / "window_neighbors.jsonl",
        [
            _window_neighbor_row(
                "conv-a",
                "window-0001",
                embedding_model="ollama/nomic-embed-text-v2-moe",
                neighbors=[
                    {
                        "provider_id": "openai",
                        "conversation_id": "conv-a",
                        "window_id": "window-0002",
                        "score": 0.91,
                    },
                    {
                        "provider_id": "openai",
                        "conversation_id": "conv-b",
                        "window_id": "window-0001",
                        "score": 0.89,
                    },
                ],
            ),
            _window_neighbor_row(
                "conv-a",
                "window-0002",
                embedding_model="ollama/nomic-embed-text-v2-moe",
                neighbors=[
                    {
                        "provider_id": "openai",
                        "conversation_id": "conv-a",
                        "window_id": "window-0001",
                        "score": 0.91,
                    }
                ],
            ),
        ],
    )

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
    assert topics_payload["artifact_type"] == "semantic_topics"
    assert topics_payload["schema_version"] == "1.0"
    assert topics_payload["generated_at"].endswith("Z")
    assert topics_payload["provenance"]["label_mode"] == "model-enriched"
    assert topics_payload["provenance"]["pipeline_version"]
    assert topics_payload["provenance"]["membership_mode"] == "cluster-is-topic-v1"
    assert (
        topics_payload["provenance"]["embedding_model"]
        == "ollama/nomic-embed-text-v2-moe"
    )
    assert topics_payload["provenance"]["labeling_model"] == "ollama/llama3.1:latest"
    assert topics_payload["provenance"]["prompt_variant"] == "prompt_b"
    assert topics_payload["provenance"]["window_cap"] == 8
    assert topics_payload["provenance"]["max_window_chars"] == 300
    assert topics_payload["provenance"]["prompt_hash"].startswith("sha256:")
    assert len(topics_payload["provenance"]["prompt_hash"]) == 71
    assert topics_payload["provenance"]["clustering"] == {
        "method": "connected-components",
        "edge_policy": "mutual-only",
        "neighbor_k": 2,
        "score_threshold_policy": (
            "same-thread-shared-messages<=1;"
            "cross-thread-mutual-score>=runtime-p75"
        ),
    }
    assert topics_payload["provenance"]["filters"] == {
        "cluster_id": "cluster_000001",
        "min_cluster_size": 1,
        "cross_thread_only": False,
    }
    assert topics_payload["source_inputs"] == [
        "message_windows.jsonl",
        "window_clusters.jsonl",
        "window_neighbors.jsonl",
    ]
    assert topics_payload["topics"][0]["label"] == "Launch Readiness"
    assert topics_payload["topics"][0]["summary"] == (
        "Deployment readiness and rollback planning dominate the topic."
    )
    assert topics_payload["topics"][0]["keywords"] == [
        "launch",
        "rollback",
        "monitoring",
    ]
    assert topics_payload["topics"][0]["state"] is None
    assert topics_payload["topics"][0]["cluster_ids"] == ["cluster_000001"]
    assert topics_payload["topics"][0]["quality_signals"]["cluster_size"] == 3
    assert topics_payload["topics"][0]["quality_signals"]["conversation_count"] == 2
    assert topics_payload["topics"][0]["quality_signals"]["single_window"] is False
    assert topics_payload["topics"][0]["quality_signals"]["avg_intra_cluster_score"] == pytest.approx(
        (0.91 + 0.89 + 0.91) / 3
    )
    assert topics_payload["topics"][0]["quality_signals"]["max_intra_cluster_score"] == pytest.approx(
        0.91
    )
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
    assert topic_a["state"] is None
    assert topic_a["quality_signals"] == topic_b["quality_signals"]
    assert artifact_a["provenance"]["prompt_hash"] == artifact_b["provenance"]["prompt_hash"]
    assert artifact_a["provenance"]["prompt_hash"] is None
    assert artifact_a["provenance"]["prompt_variant"] is None

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


def test_semantic_topics_representative_window_selection_is_deterministic(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_representative_selection_fixture(root)

    artifact_a, _membership_rows_a = build_semantic_topics_artifact(
        root,
        cluster_id="cluster_000010",
    )
    artifact_b, _membership_rows_b = build_semantic_topics_artifact(
        root,
        cluster_id="cluster_000010",
    )

    assert (
        artifact_a["topics"][0]["representative_windows"]
        == artifact_b["topics"][0]["representative_windows"]
    )


def test_semantic_topics_representative_windows_prefer_central_members(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_representative_selection_fixture(root)

    artifact, _membership_rows = build_semantic_topics_artifact(
        root,
        cluster_id="cluster_000010",
    )

    representative = artifact["topics"][0]["representative_windows"][0]

    assert representative["conversation_id"] == "conv-c"
    assert representative["window_id"] == "window-0001"
    assert "rollout checklist" in representative["excerpt"]


def test_semantic_topics_single_window_cluster_uses_its_only_window(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_representative_selection_fixture(root)

    artifact, _membership_rows = build_semantic_topics_artifact(
        root,
        cluster_id="cluster_000020",
    )

    assert artifact["topics"][0]["representative_windows"] == [
        {
            "conversation_id": "conv-d",
            "window_id": "window-0001",
            "excerpt": "user: standalone checkpoint note",
        }
    ]
    assert artifact["topics"][0]["quality_signals"] == {
        "cluster_size": 1,
        "conversation_count": 1,
        "avg_intra_cluster_score": None,
        "max_intra_cluster_score": None,
        "single_window": True,
    }


def test_semantic_topics_structural_only_without_optional_model_or_neighbors(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_topics_fixture(root)

    result = write_semantic_topics_artifacts(root)
    topics_payload = json.loads(Path(result["topics_path"]).read_text(encoding="utf-8"))

    assert result["label_mode"] == "structural-only"
    assert topics_payload["provenance"]["label_mode"] == "structural-only"
    assert topics_payload["provenance"]["labeling_model"] is None
    assert topics_payload["provenance"]["prompt_variant"] is None
    assert topics_payload["provenance"]["prompt_hash"] is None
    assert topics_payload["provenance"]["embedding_model"] is None
    assert topics_payload["provenance"]["clustering"]["neighbor_k"] is None
    assert all(topic["label"] is None for topic in topics_payload["topics"])
    assert all(topic["summary"] is None for topic in topics_payload["topics"])
    assert all(topic["keywords"] == [] for topic in topics_payload["topics"])
    assert all(topic["state"] is None for topic in topics_payload["topics"])
    assert all(topic["quality_signals"]["avg_intra_cluster_score"] is None for topic in topics_payload["topics"])
    assert all(topic["quality_signals"]["max_intra_cluster_score"] is None for topic in topics_payload["topics"])
    assert {topic["quality_signals"]["cluster_size"] for topic in topics_payload["topics"]} == {2, 3}


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
