from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from llm_logparser.cli.cli import main
from llm_logparser.core import analyzer_cross_thread_candidates as cross_thread_module
from llm_logparser.core.analyzer_cross_thread_candidates import (
    build_cross_thread_candidate_rows,
    cross_thread_candidates_path,
    write_cross_thread_candidates_artifact,
)
from llm_logparser.core.analyzer_semantic_prototype import derive_semantic_span_id
from llm_logparser.core.schema_validation import (
    load_cross_thread_candidate_validator,
    load_topics_validator,
)


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _representative_span(
    *,
    conversation_id: str,
    window_id: str,
    message_ids: list[str],
    excerpt: str,
    normalized_label: str | None = None,
    raw_label: str | None = None,
) -> dict:
    span_id = derive_semantic_span_id(
        provider_id="openai",
        conversation_id=conversation_id,
        message_ids=tuple(message_ids),
        window_id=window_id,
    )
    row = {
        "conversation_id": conversation_id,
        "span_id": span_id,
        "message_ids": message_ids,
        "excerpt": excerpt,
        "state": "in_progress",
        "state_confidence": 0.8,
        "state_signals": ["user_request"],
        "window_id": window_id,
    }
    if normalized_label is not None or raw_label is not None:
        row["semantic_normalization"] = {
            "conversation_id": conversation_id,
            "span_id": span_id,
            "window_id": window_id,
            "message_ids": message_ids,
            "unit_kind": "representative_span",
            "raw_label": raw_label or "request",
            "normalized_label": normalized_label,
            "mapping_status": "mapped",
            "confidence": 0.92,
            "method": {
                "kind": "rule",
                "model": None,
                "mapping_version": "seed_taxonomy_v0",
            },
        }
    return row


def _topic_record(
    *,
    topic_id: str,
    conversation_id: str,
    cluster_id: str,
    window_id: str,
    label: str,
    keywords: list[str],
    excerpt: str,
    message_ids: list[str],
    normalized_label: str | None = None,
    raw_label: str | None = None,
    first_seen: int | None = 100,
    last_seen: int | None = None,
) -> dict:
    span_row = _representative_span(
        conversation_id=conversation_id,
        window_id=window_id,
        message_ids=message_ids,
        excerpt=excerpt,
        normalized_label=normalized_label,
        raw_label=raw_label,
    )
    return {
        "topic_id": topic_id,
        "provider_id": "openai",
        "label": label,
        "summary": None,
        "keywords": keywords,
        "confidence": None,
        "state": "in_progress",
        "state_confidence": 0.8,
        "cluster_ids": [cluster_id],
        "conversation_ids": [conversation_id],
        "span_refs": [
            {
                "conversation_id": conversation_id,
                "span_id": span_row["span_id"],
                "message_ids": message_ids,
                "state": "in_progress",
                "state_confidence": 0.8,
                "state_signals": ["user_request"],
                "window_id": window_id,
            }
        ],
        "message_refs": [
            {
                "conversation_id": conversation_id,
                "message_id": message_id,
            }
            for message_id in message_ids
        ],
        "cluster_count": 1,
        "span_count": 1,
        "window_count": 1,
        "message_count": len(message_ids),
        "first_seen": first_seen,
        "last_seen": first_seen + len(message_ids) if last_seen is None and first_seen is not None else last_seen,
        "representative_spans": [span_row],
    }


def _topics_artifact(topics: list[dict]) -> dict:
    artifact = {
        "artifact_type": "semantic_topics",
        "schema_version": "2.2",
        "provider_id": "openai",
        "topic_count": len(topics),
        "generated_at": "2026-04-11T00:00:00Z",
        "source_inputs": ["parsed.jsonl", "window_clusters.jsonl"],
        "provenance": {
            "pipeline_version": "semantic-topics-v1",
            "membership_mode": "span-and-message-v2",
            "label_mode": "structural-only",
            "embedding_model": "test-embedding",
            "labeling_model": None,
            "prompt_hash": None,
            "prompt_variant": None,
            "window_cap": 3,
            "max_window_chars": 400,
            "clustering": {
                "method": "test",
                "edge_policy": "mutual-only",
                "neighbor_k": 5,
                "score_threshold_policy": "fixed-0.62",
            },
            "filters": {
                "cluster_id": None,
                "min_cluster_size": 1,
                "cross_thread_only": False,
            },
        },
        "topics": topics,
    }
    errors = list(load_topics_validator().iter_errors(artifact))
    assert not errors, errors[0].message if errors else ""
    return artifact


def _write_topics_fixture(root: Path) -> Path:
    topics = [
        _topic_record(
            topic_id="topic-a",
            conversation_id="conv-a",
            cluster_id="cluster-a",
            window_id="window-a",
            label="Migration checklist",
            keywords=["migration", "rollback", "checklist"],
            excerpt="Draft the migration checklist with rollback gates for production.",
            message_ids=["a-1", "a-2"],
            normalized_label="request",
            raw_label="implementation_request",
        ),
        _topic_record(
            topic_id="topic-b",
            conversation_id="conv-b",
            cluster_id="cluster-b",
            window_id="window-b",
            label="Migration checklist",
            keywords=["migration", "rollback", "checklist"],
            excerpt="Revise the migration checklist and confirm rollback gates before rollout.",
            message_ids=["b-1", "b-2"],
            normalized_label="request",
            raw_label="implementation_request",
        ),
        _topic_record(
            topic_id="topic-c",
            conversation_id="conv-c",
            cluster_id="cluster-c",
            window_id="window-c",
            label="Rollout monitoring",
            keywords=["migration", "monitoring"],
            excerpt="Add rollout monitoring checks for the migration checklist before release.",
            message_ids=["c-1", "c-2"],
            normalized_label="request",
            raw_label="implementation_request",
        ),
        _topic_record(
            topic_id="topic-d",
            conversation_id="conv-d",
            cluster_id="cluster-d",
            window_id="window-d",
            label="Deployment steps",
            keywords=["rollback"],
            excerpt="Track deployment steps and capture rollback notes for release handoff.",
            message_ids=["d-1", "d-2"],
            normalized_label="request",
            raw_label="implementation_request",
        ),
        _topic_record(
            topic_id="topic-z",
            conversation_id="conv-z",
            cluster_id="cluster-z",
            window_id="window-z",
            label="Lunch planning",
            keywords=["lunch", "cafe"],
            excerpt="Plan lunch options and compare nearby cafe seating.",
            message_ids=["z-1"],
            normalized_label="proposal",
            raw_label="social_plan",
        ),
        _topic_record(
            topic_id="topic-a-2",
            conversation_id="conv-a",
            cluster_id="cluster-a-2",
            window_id="window-a-2",
            label="Migration checklist",
            keywords=["migration", "rollback"],
            excerpt="Continue the migration checklist and double-check rollback gates.",
            message_ids=["a-3"],
            normalized_label="request",
            raw_label="implementation_request",
        ),
    ]
    topics_path = root / "l3" / "semantic-topics" / "topics.json"
    _write_json(topics_path, _topics_artifact(topics))
    return topics_path


def _message_row(
    *,
    conversation_id: str,
    message_id: str,
    role: str,
    text: str,
    ts: int,
) -> dict[str, Any]:
    return {
        "record_type": "message",
        "schema_version": "3.0",
        "provider_id": "openai",
        "conversation_id": conversation_id,
        "message_id": message_id,
        "role": role,
        "text": text,
        "ts": ts,
    }


def _message_window_row(
    *,
    conversation_id: str,
    window_id: str,
    message_ids: list[str],
    char_count: int,
    ts_start: int,
    ts_end: int,
) -> dict[str, Any]:
    return {
        "record_type": "message_window",
        "schema_version": "3.0",
        "provider_id": "openai",
        "conversation_id": conversation_id,
        "window_id": window_id,
        "message_ids": message_ids,
        "char_count": char_count,
        "ts_start": ts_start,
        "ts_end": ts_end,
        "window_size": len(message_ids),
        "window_stride": len(message_ids),
    }


def _write_preview_fixture(root: Path) -> None:
    conversation_rows: dict[str, dict[str, list[dict[str, Any]]]] = {
        "conv-a": {
            "messages": [
                _message_row(
                    conversation_id="conv-a",
                    message_id="a-1",
                    role="user",
                    text="Draft the migration checklist.",
                    ts=100,
                ),
                _message_row(
                    conversation_id="conv-a",
                    message_id="a-2",
                    role="assistant",
                    text="Add rollback gates for production.",
                    ts=110,
                ),
                _message_row(
                    conversation_id="conv-a",
                    message_id="a-3",
                    role="user",
                    text="Continue the migration checklist and verify rollback gates.",
                    ts=120,
                ),
            ],
            "windows": [
                _message_window_row(
                    conversation_id="conv-a",
                    window_id="window-a",
                    message_ids=["a-1", "a-2"],
                    char_count=len("Draft the migration checklist.") + len("Add rollback gates for production."),
                    ts_start=100,
                    ts_end=110,
                ),
                _message_window_row(
                    conversation_id="conv-a",
                    window_id="window-a-2",
                    message_ids=["a-3"],
                    char_count=len("Continue the migration checklist and verify rollback gates."),
                    ts_start=120,
                    ts_end=120,
                ),
            ],
        },
        "conv-b": {
            "messages": [
                _message_row(
                    conversation_id="conv-b",
                    message_id="b-1",
                    role="user",
                    text="Revise the migration checklist.",
                    ts=200,
                ),
                _message_row(
                    conversation_id="conv-b",
                    message_id="b-2",
                    role="assistant",
                    text="Confirm rollback gates before rollout.",
                    ts=210,
                ),
            ],
            "windows": [
                _message_window_row(
                    conversation_id="conv-b",
                    window_id="window-b",
                    message_ids=["b-1", "b-2"],
                    char_count=len("Revise the migration checklist.") + len("Confirm rollback gates before rollout."),
                    ts_start=200,
                    ts_end=210,
                ),
            ],
        },
        "conv-c": {
            "messages": [
                _message_row(
                    conversation_id="conv-c",
                    message_id="c-1",
                    role="user",
                    text="Add rollout monitoring checks.",
                    ts=300,
                ),
                _message_row(
                    conversation_id="conv-c",
                    message_id="c-2",
                    role="assistant",
                    text="Review the migration checklist before release.",
                    ts=310,
                ),
            ],
            "windows": [
                _message_window_row(
                    conversation_id="conv-c",
                    window_id="window-c",
                    message_ids=["c-1", "c-2"],
                    char_count=len("Add rollout monitoring checks.") + len("Review the migration checklist before release."),
                    ts_start=300,
                    ts_end=310,
                ),
            ],
        },
        "conv-d": {
            "messages": [
                _message_row(
                    conversation_id="conv-d",
                    message_id="d-1",
                    role="user",
                    text="Track deployment steps.",
                    ts=400,
                ),
                _message_row(
                    conversation_id="conv-d",
                    message_id="d-2",
                    role="assistant",
                    text="Capture rollback notes for the release handoff.",
                    ts=410,
                ),
            ],
            "windows": [
                _message_window_row(
                    conversation_id="conv-d",
                    window_id="window-d",
                    message_ids=["d-1", "d-2"],
                    char_count=len("Track deployment steps.") + len("Capture rollback notes for the release handoff."),
                    ts_start=400,
                    ts_end=410,
                ),
            ],
        },
        "conv-z": {
            "messages": [
                _message_row(
                    conversation_id="conv-z",
                    message_id="z-1",
                    role="user",
                    text="Plan lunch options and compare cafe seating.",
                    ts=500,
                ),
            ],
            "windows": [
                _message_window_row(
                    conversation_id="conv-z",
                    window_id="window-z",
                    message_ids=["z-1"],
                    char_count=len("Plan lunch options and compare cafe seating."),
                    ts_start=500,
                    ts_end=500,
                ),
            ],
        },
    }
    for conversation_id, payload in conversation_rows.items():
        thread_dir = root / f"thread-{conversation_id}"
        _write_jsonl(thread_dir / "parsed.jsonl", payload["messages"])
        _write_jsonl(thread_dir / "message_windows.jsonl", payload["windows"])


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped:
                rows.append(json.loads(stripped))
    return rows


class _FakeEmbeddingBackend:
    def __init__(self, vectors_by_text: dict[str, list[float]], calls: list[list[str]]) -> None:
        self.model_id = "fake/test-embedding"
        self._vectors_by_text = vectors_by_text
        self._calls = calls

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._calls.append(list(texts))
        return [self._vectors_by_text[text] for text in texts]


def test_build_cross_thread_candidate_rows_emits_clear_cross_thread_link(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_topics_fixture(root)

    rows = build_cross_thread_candidate_rows(root)

    row = next(
        candidate
        for candidate in rows
        if candidate["source_topic_id"] == "topic-a"
        and candidate["target_topic_id"] == "topic-b"
    )
    assert row["score"] >= 0.9
    assert row["timestamp_delta_ms"] == 0
    assert "normalized_label_match" in row["evidence"]["reason_codes"]
    assert "shared_keywords_high" in row["evidence"]["reason_codes"]


def test_build_cross_thread_candidate_rows_adds_embedding_similarity_when_enabled(
    tmp_path: Path,
    monkeypatch,
):
    root = tmp_path / "artifacts" / "openai"
    _write_topics_fixture(root)
    _write_preview_fixture(root)

    calls: list[list[str]] = []
    vectors_by_text = {
        "Draft the migration checklist.\n\nAdd rollback gates for production.": [1.0, 0.0],
        "Revise the migration checklist.\n\nConfirm rollback gates before rollout.": [0.95, 0.05],
        "Add rollout monitoring checks.\n\nReview the migration checklist before release.": [0.5, 0.5],
        "Track deployment steps.\n\nCapture rollback notes for the release handoff.": [0.0, 1.0],
        "Plan lunch options and compare cafe seating.": [-1.0, 0.0],
        "Continue the migration checklist and verify rollback gates.": [0.9, 0.1],
    }
    monkeypatch.setattr(
        cross_thread_module,
        "create_embedding_backend",
        lambda **_: _FakeEmbeddingBackend(vectors_by_text, calls),
    )

    baseline_rows = build_cross_thread_candidate_rows(root, top_per_source=2)
    embedded_rows = build_cross_thread_candidate_rows(
        root,
        top_per_source=2,
        embedding_model="fake-embedding",
    )

    assert len(embedded_rows) == len(baseline_rows)
    assert any("embedding_similarity" in row for row in embedded_rows)
    row = next(
        candidate
        for candidate in embedded_rows
        if candidate["source_topic_id"] == "topic-a"
        and candidate["target_topic_id"] == "topic-b"
    )
    assert row["embedding_similarity"] > 0.9
    unique_spans = {
        (row["source_conversation_id"], row["source_span_id"])
        for row in embedded_rows
    } | {
        (row["target_conversation_id"], row["target_span_id"])
        for row in embedded_rows
    }
    assert len(calls) == 1
    assert len(calls[0]) == len(unique_spans)


def test_build_cross_thread_candidate_rows_excludes_same_thread_and_unrelated_pairs(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    _write_topics_fixture(root)

    rows = build_cross_thread_candidate_rows(root)

    assert not any(
        row["source_conversation_id"] == "conv-a"
        and row["target_conversation_id"] == "conv-a"
        for row in rows
    )
    assert not any(
        row["source_topic_id"] == "topic-a"
        and row["target_topic_id"] == "topic-z"
        for row in rows
    )


def test_build_cross_thread_candidate_rows_respects_top_per_source_and_threshold(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    _write_topics_fixture(root)

    rows = build_cross_thread_candidate_rows(root, top_per_source=2)
    source_rows = [row for row in rows if row["source_topic_id"] == "topic-a"]

    assert len(source_rows) == 2
    assert [row["target_topic_id"] for row in source_rows] == ["topic-b", "topic-c"]

    strict_rows = build_cross_thread_candidate_rows(root, min_score=0.95, top_per_source=3)
    assert not any(row["source_topic_id"] == "topic-a" and row["target_topic_id"] == "topic-c" for row in strict_rows)


def test_build_cross_thread_candidate_rows_embedding_is_optional_and_unavailable_is_non_fatal(
    tmp_path: Path,
    monkeypatch,
):
    root = tmp_path / "artifacts" / "openai"
    _write_topics_fixture(root)
    _write_preview_fixture(root)

    baseline_rows = build_cross_thread_candidate_rows(root, top_per_source=2)
    monkeypatch.setattr(
        cross_thread_module,
        "create_embedding_backend",
        lambda **_: (_ for _ in ()).throw(RuntimeError("ollama unavailable")),
    )

    rows = build_cross_thread_candidate_rows(
        root,
        top_per_source=2,
        embedding_model="missing-local-model",
    )

    assert rows == baseline_rows
    assert all("embedding_similarity" not in row for row in rows)


def test_build_cross_thread_candidate_rows_embedding_tie_break_is_predictable(
    tmp_path: Path,
    monkeypatch,
):
    root = tmp_path / "artifacts" / "openai"
    _write_topics_fixture(root)
    _write_preview_fixture(root)

    evidence = cross_thread_module._Evidence(
        score=0.7,
        reason_codes=("excerpt_similarity_high",),
        excerpt_similarity=0.8,
        topic_label_similarity=0.1,
        shared_keywords=(),
        normalized_label_match=False,
        raw_label_match=False,
        timestamp_delta_ms=None,
    )

    def fake_evidence(source, target):
        if source.topic_id != "topic-a":
            return None
        if target.topic_id in {"topic-b", "topic-c"}:
            return evidence
        return None

    calls: list[list[str]] = []
    vectors_by_text = {
        "Draft the migration checklist.\n\nAdd rollback gates for production.": [1.0, 0.0],
        "Revise the migration checklist.\n\nConfirm rollback gates before rollout.": [0.2, 0.98],
        "Add rollout monitoring checks.\n\nReview the migration checklist before release.": [0.98, 0.2],
        "Track deployment steps.\n\nCapture rollback notes for the release handoff.": [0.0, 1.0],
        "Plan lunch options and compare cafe seating.": [-1.0, 0.0],
        "Continue the migration checklist and verify rollback gates.": [0.5, 0.5],
    }
    monkeypatch.setattr(cross_thread_module, "_evidence_for_pair", fake_evidence)
    monkeypatch.setattr(
        cross_thread_module,
        "create_embedding_backend",
        lambda **_: _FakeEmbeddingBackend(vectors_by_text, calls),
    )

    baseline_rows = build_cross_thread_candidate_rows(root, top_per_source=2)
    embedded_rows = build_cross_thread_candidate_rows(
        root,
        top_per_source=2,
        embedding_model="fake-embedding",
    )

    assert [row["target_topic_id"] for row in baseline_rows] == ["topic-b", "topic-c"]
    assert [row["target_topic_id"] for row in embedded_rows] == ["topic-c", "topic-b"]


def test_build_cross_thread_candidate_rows_applies_timestamp_distance_bonus(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    topics = [
        _topic_record(
            topic_id="topic-a",
            conversation_id="conv-a",
            cluster_id="cluster-a",
            window_id="window-a",
            label="Database rollout",
            keywords=["migration"],
            excerpt="Need migration review before deploy.",
            message_ids=["a-1", "a-2"],
            normalized_label="status_update",
            raw_label="status_note",
            first_seen=100,
        ),
        _topic_record(
            topic_id="topic-b",
            conversation_id="conv-b",
            cluster_id="cluster-b",
            window_id="window-b",
            label="Deploy review",
            keywords=["migration"],
            excerpt="Migration review needed before release deploy.",
            message_ids=["b-1", "b-2"],
            normalized_label="status_update",
            raw_label="status_note",
            first_seen=100 + (10 * 24 * 60 * 60 * 1000),
        ),
    ]
    _write_json(root / "l3" / "semantic-topics" / "topics.json", _topics_artifact(topics))

    rows = build_cross_thread_candidate_rows(root)

    assert len(rows) == 2
    row = next(
        candidate
        for candidate in rows
        if candidate["source_topic_id"] == "topic-a"
        and candidate["target_topic_id"] == "topic-b"
    )
    assert row["timestamp_delta_ms"] == 10 * 24 * 60 * 60 * 1000
    assert "timestamp_distance_high" in row["evidence"]["reason_codes"]
    assert row["score"] > 0.6


def test_build_cross_thread_candidate_rows_omits_timestamp_bonus_when_missing(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    timed_root = tmp_path / "artifacts-timed" / "openai"
    topics = [
        _topic_record(
            topic_id="topic-a",
            conversation_id="conv-a",
            cluster_id="cluster-a",
            window_id="window-a",
            label="Database rollout",
            keywords=["migration"],
            excerpt="Need migration review before deploy.",
            message_ids=["a-1", "a-2"],
            normalized_label="status_update",
            raw_label="status_note",
            first_seen=None,
            last_seen=None,
        ),
        _topic_record(
            topic_id="topic-b",
            conversation_id="conv-b",
            cluster_id="cluster-b",
            window_id="window-b",
            label="Deploy review",
            keywords=["migration"],
            excerpt="Migration review needed before release deploy.",
            message_ids=["b-1", "b-2"],
            normalized_label="status_update",
            raw_label="status_note",
            first_seen=100 + (10 * 24 * 60 * 60 * 1000),
        ),
    ]
    _write_json(root / "l3" / "semantic-topics" / "topics.json", _topics_artifact(topics))
    _write_json(
        timed_root / "l3" / "semantic-topics" / "topics.json",
        _topics_artifact(
            [
                _topic_record(
                    topic_id="topic-a",
                    conversation_id="conv-a",
                    cluster_id="cluster-a",
                    window_id="window-a",
                    label="Database rollout",
                    keywords=["migration"],
                    excerpt="Need migration review before deploy.",
                    message_ids=["a-1", "a-2"],
                    normalized_label="status_update",
                    raw_label="status_note",
                    first_seen=100,
                ),
                _topic_record(
                    topic_id="topic-b",
                    conversation_id="conv-b",
                    cluster_id="cluster-b",
                    window_id="window-b",
                    label="Deploy review",
                    keywords=["migration"],
                    excerpt="Migration review needed before release deploy.",
                    message_ids=["b-1", "b-2"],
                    normalized_label="status_update",
                    raw_label="status_note",
                    first_seen=100 + (10 * 24 * 60 * 60 * 1000),
                ),
            ]
        ),
    )

    rows = build_cross_thread_candidate_rows(root)
    timed_rows = build_cross_thread_candidate_rows(timed_root)

    row = next(
        candidate
        for candidate in rows
        if candidate["source_topic_id"] == "topic-a"
        and candidate["target_topic_id"] == "topic-b"
    )
    timed_row = next(
        candidate
        for candidate in timed_rows
        if candidate["source_topic_id"] == "topic-a"
        and candidate["target_topic_id"] == "topic-b"
    )
    assert row["timestamp_delta_ms"] is None
    assert "timestamp_distance_medium" not in row["evidence"]["reason_codes"]
    assert "timestamp_distance_high" not in row["evidence"]["reason_codes"]
    assert timed_row["score"] > row["score"]


def test_build_cross_thread_candidate_rows_applies_medium_timestamp_bonus_only_when_expected(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    topics = [
        _topic_record(
            topic_id="topic-a",
            conversation_id="conv-a",
            cluster_id="cluster-a",
            window_id="window-a",
            label="Database rollout",
            keywords=["migration"],
            excerpt="Need migration review before deploy.",
            message_ids=["a-1", "a-2"],
            normalized_label="status_update",
            raw_label="status_note",
            first_seen=100,
        ),
        _topic_record(
            topic_id="topic-b",
            conversation_id="conv-b",
            cluster_id="cluster-b",
            window_id="window-b",
            label="Deploy review",
            keywords=["migration"],
            excerpt="Migration review needed before release deploy.",
            message_ids=["b-1", "b-2"],
            normalized_label="status_update",
            raw_label="status_note",
            first_seen=100 + (3 * 24 * 60 * 60 * 1000),
        ),
    ]
    _write_json(root / "l3" / "semantic-topics" / "topics.json", _topics_artifact(topics))

    rows = build_cross_thread_candidate_rows(root)

    row = next(
        candidate
        for candidate in rows
        if candidate["source_topic_id"] == "topic-a"
        and candidate["target_topic_id"] == "topic-b"
    )
    assert row["timestamp_delta_ms"] == 3 * 24 * 60 * 60 * 1000
    assert "timestamp_distance_medium" in row["evidence"]["reason_codes"]
    assert "timestamp_distance_high" not in row["evidence"]["reason_codes"]
    assert row["score"] > 0.55


def test_cross_thread_candidate_rows_are_schema_valid(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_topics_fixture(root)

    result = write_cross_thread_candidates_artifact(root)
    rows = _read_jsonl(result["candidates_path"])
    validator = load_cross_thread_candidate_validator()

    assert rows
    for row in rows:
        errors = list(validator.iter_errors(row))
        assert not errors, errors[0].message if errors else ""


def test_cli_analyze_cross_thread_candidates_writes_artifact(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_topics_fixture(root)

    main(["analyze", "cross-thread-candidates", "--input", str(root)])

    assert cross_thread_candidates_path(root).exists()
    assert (root / "l3" / "cross-thread-candidates" / "summary.json").exists()


def test_cross_thread_candidates_do_not_modify_topics_json(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    topics_path = _write_topics_fixture(root)
    before = topics_path.read_text(encoding="utf-8")

    write_cross_thread_candidates_artifact(root)

    after = topics_path.read_text(encoding="utf-8")
    assert after == before
