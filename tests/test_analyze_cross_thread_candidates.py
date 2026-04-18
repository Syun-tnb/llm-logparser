from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from llm_logparser.cli.cli import main
from llm_logparser.core import analyzer_cross_thread_candidates as cross_thread_module
from llm_logparser.core.analyzer_token_dictionary import (
    load_token_dictionary_signals,
    token_bundles_path,
    token_dictionary_lexical_rules_path,
    token_dictionary_path,
)
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


def _write_token_dictionary_signals_fixture(
    root: Path,
    *,
    tokens: list[dict[str, Any]],
    bundles: list[dict[str, Any]],
) -> None:
    _write_json(
        token_dictionary_path(root),
        {
            "artifact_type": "token_dictionary",
            "schema_version": "0.1",
            "producer_layer": "L3",
            "provider_id": "openai",
            "created_at": "2026-04-18T00:00:00Z",
            "source_inputs": ["parsed.jsonl"],
            "reproducibility_note": "Rebuildable from canonical inputs",
            "token_count": len(tokens),
            "tokens": tokens,
        },
    )
    _write_json(
        token_bundles_path(root),
        {
            "artifact_type": "token_bundles",
            "schema_version": "0.1",
            "producer_layer": "L3",
            "provider_id": "openai",
            "created_at": "2026-04-18T00:00:00Z",
            "source_inputs": ["parsed.jsonl"],
            "reproducibility_note": "Rebuildable from canonical inputs",
            "bundle_count": len(bundles),
            "bundles": bundles,
        },
    )


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

    rows = build_cross_thread_candidate_rows(root, min_score=0.58)

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

    rows = build_cross_thread_candidate_rows(root, min_score=0.58)

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
        volume_gap=None,
        temporal_gap_seconds=None,
        continuity_mask=False,
        dormancy_score=0.0,
        specificity_score=0.4,
        local_context_delta=None,
    )

    def fake_evidence(source, target, *, recurrence_context, token_dictionary_signals=None):
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

    rows = build_cross_thread_candidate_rows(root, min_score=0.58)

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


def test_build_cross_thread_candidate_rows_computes_message_volume_gap_and_temporal_gap_seconds(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    topics = [
        _topic_record(
            topic_id="topic-a",
            conversation_id="conv-a",
            cluster_id="cluster-a",
            window_id="window-a",
            label="Migration checklist",
            keywords=["migration", "rollback"],
            excerpt="Draft the migration checklist and add rollback gates.",
            message_ids=["a-1"],
            normalized_label="request",
            raw_label="implementation_request",
            first_seen=1000,
            last_seen=1100,
        ),
        _topic_record(
            topic_id="topic-b",
            conversation_id="conv-b",
            cluster_id="cluster-b",
            window_id="window-b",
            label="Migration checklist",
            keywords=["migration", "rollback"],
            excerpt="Draft the migration checklist and add rollback gates.",
            message_ids=["b-1"],
            normalized_label="request",
            raw_label="implementation_request",
            first_seen=4000,
            last_seen=4100,
        ),
    ]
    _write_json(root / "l3" / "semantic-topics" / "topics.json", _topics_artifact(topics))
    _write_jsonl(
        root / "thread-conv-a" / "parsed.jsonl",
        [_message_row(conversation_id="conv-a", message_id="a-1", role="user", text="Draft the migration checklist.", ts=1000)],
    )
    _write_jsonl(
        root / "thread-mid" / "parsed.jsonl",
        [
            _message_row(conversation_id="conv-mid", message_id="m-1", role="user", text="Intervening 1", ts=1500),
            _message_row(conversation_id="conv-mid", message_id="m-2", role="assistant", text="Intervening 2", ts=2500),
            _message_row(conversation_id="conv-mid", message_id="m-3", role="user", text="Intervening 3", ts=3500),
        ],
    )
    _write_jsonl(
        root / "thread-conv-b" / "parsed.jsonl",
        [_message_row(conversation_id="conv-b", message_id="b-1", role="user", text="Confirm rollback gates.", ts=4000)],
    )

    rows = build_cross_thread_candidate_rows(root)

    row = next(
        candidate
        for candidate in rows
        if candidate["source_topic_id"] == "topic-a"
        and candidate["target_topic_id"] == "topic-b"
    )
    assert row["volume_gap"] == 3
    assert row["temporal_gap_seconds"] == 3
    assert row["evidence"]["volume_gap"] == 3


def test_build_cross_thread_candidate_rows_marks_small_volume_gap_as_continuity_like(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    topics = [
        _topic_record(
            topic_id="topic-a",
            conversation_id="conv-a",
            cluster_id="cluster-a",
            window_id="window-a",
            label="Release note",
            keywords=["release", "deploy"],
            excerpt="Prepare the release note and final deploy summary.",
            message_ids=["a-1"],
            normalized_label="status_update",
            raw_label="status_note",
            first_seen=1000,
            last_seen=1100,
        ),
        _topic_record(
            topic_id="topic-b",
            conversation_id="conv-b",
            cluster_id="cluster-b",
            window_id="window-b",
            label="Release note",
            keywords=["release", "deploy"],
            excerpt="Prepare the release note and final deploy summary.",
            message_ids=["b-1"],
            normalized_label="status_update",
            raw_label="status_note",
            first_seen=3000,
            last_seen=3100,
        ),
    ]
    _write_json(root / "l3" / "semantic-topics" / "topics.json", _topics_artifact(topics))
    _write_jsonl(
        root / "thread-conv-a" / "parsed.jsonl",
        [_message_row(conversation_id="conv-a", message_id="a-1", role="user", text="Prepare release note.", ts=1000)],
    )
    _write_jsonl(
        root / "thread-mid" / "parsed.jsonl",
        [_message_row(conversation_id="conv-mid", message_id="m-1", role="assistant", text="Short local continuation", ts=2000)],
    )
    _write_jsonl(
        root / "thread-conv-b" / "parsed.jsonl",
        [_message_row(conversation_id="conv-b", message_id="b-1", role="user", text="Final deploy summary.", ts=3000)],
    )

    rows = build_cross_thread_candidate_rows(root)

    row = next(
        candidate
        for candidate in rows
        if candidate["source_topic_id"] == "topic-a"
        and candidate["target_topic_id"] == "topic-b"
    )
    assert row["volume_gap"] == 1
    assert row["continuity_mask"] is True
    assert row["evidence"]["continuity_mask"] is True


def test_build_cross_thread_candidate_rows_dormancy_score_increases_with_larger_volume_gap(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    topics = [
        _topic_record(
            topic_id="topic-a",
            conversation_id="conv-a",
            cluster_id="cluster-a",
            window_id="window-a",
            label="Migration checklist",
            keywords=["migration", "rollback"],
            excerpt="Migration checklist with rollback gates.",
            message_ids=["a-1"],
            normalized_label="request",
            raw_label="implementation_request",
            first_seen=1000,
            last_seen=1100,
        ),
        _topic_record(
            topic_id="topic-b-near",
            conversation_id="conv-b",
            cluster_id="cluster-b",
            window_id="window-b",
            label="Migration checklist",
            keywords=["migration", "rollback"],
            excerpt="Migration checklist with rollback gates.",
            message_ids=["b-1"],
            normalized_label="request",
            raw_label="implementation_request",
            first_seen=3000,
            last_seen=3100,
        ),
        _topic_record(
            topic_id="topic-c-far",
            conversation_id="conv-c",
            cluster_id="cluster-c",
            window_id="window-c",
            label="Migration checklist",
            keywords=["migration", "rollback"],
            excerpt="Migration checklist with rollback gates.",
            message_ids=["c-1"],
            normalized_label="request",
            raw_label="implementation_request",
            first_seen=9000,
            last_seen=9100,
        ),
    ]
    _write_json(root / "l3" / "semantic-topics" / "topics.json", _topics_artifact(topics))
    _write_jsonl(
        root / "thread-conv-a" / "parsed.jsonl",
        [_message_row(conversation_id="conv-a", message_id="a-1", role="user", text="Source", ts=1000)],
    )
    _write_jsonl(
        root / "thread-gap" / "parsed.jsonl",
        [
            _message_row(conversation_id="conv-gap", message_id="g-1", role="user", text="gap1", ts=2000),
            _message_row(conversation_id="conv-gap", message_id="g-2", role="assistant", text="gap2", ts=2500),
            _message_row(conversation_id="conv-gap", message_id="g-3", role="user", text="gap3", ts=4000),
            _message_row(conversation_id="conv-gap", message_id="g-4", role="assistant", text="gap4", ts=5000),
            _message_row(conversation_id="conv-gap", message_id="g-5", role="user", text="gap5", ts=6000),
            _message_row(conversation_id="conv-gap", message_id="g-6", role="assistant", text="gap6", ts=7000),
            _message_row(conversation_id="conv-gap", message_id="g-7", role="user", text="gap7", ts=8000),
        ],
    )
    _write_jsonl(
        root / "thread-conv-b" / "parsed.jsonl",
        [_message_row(conversation_id="conv-b", message_id="b-1", role="user", text="Near target", ts=3000)],
    )
    _write_jsonl(
        root / "thread-conv-c" / "parsed.jsonl",
        [_message_row(conversation_id="conv-c", message_id="c-1", role="user", text="Far target", ts=9000)],
    )

    rows = build_cross_thread_candidate_rows(root)
    near_row = next(row for row in rows if row["target_topic_id"] == "topic-b-near")
    far_row = next(row for row in rows if row["target_topic_id"] == "topic-c-far")
    assert near_row["dormancy_score"] < far_row["dormancy_score"]


def test_text_specificity_score_prefers_concrete_task_text_over_generic_short_text():
    generic = cross_thread_module._text_specificity_score("Okay, sounds good.")
    concrete = cross_thread_module._text_specificity_score(
        "Update the migration checklist, verify rollback gates, and review deploy notes."
    )
    assert generic < concrete


def test_cross_thread_candidates_source_no_longer_embeds_large_lexical_rule_sets():
    source = Path(cross_thread_module.__file__).read_text(encoding="utf-8")

    assert "_SPECIFICITY_GENERIC_TOKENS = frozenset(" not in source
    assert "_WEAK_RECURRENCE_REFLECTIVE_TOKENS = frozenset(" not in source
    assert "_TASK_FRAGMENT_ACTION_TOKENS = frozenset(" not in source
    assert "_TASK_FRAGMENT_STATE_TOKENS = frozenset(" not in source
    assert "_TASK_FRAGMENT_EXPLANATORY_TOKENS = frozenset(" not in source
    assert "_TASK_FRAGMENT_NOISE_MARKERS = (" not in source


def test_split_span_into_fragments_is_deterministic():
    text = "First sentence. Second sentence!\nThird block."

    left = cross_thread_module._split_span_into_fragments(text)
    right = cross_thread_module._split_span_into_fragments(text)

    assert left == ["First sentence", "Second sentence", "Third block"]
    assert left == right


def test_build_task_nucleus_is_shorter_and_more_focused_than_original():
    text = (
        "Let's compare Dia and Fellou and explain why each one feels useful. "
        "Retry migration_checklist.yml, validate rollback-plan, and rerun the failed rollout before release. "
        "This should make the organized explanation easier to follow."
    )

    nucleus = cross_thread_module._build_task_nucleus(
        text,
        lexical_rules=cross_thread_module.default_token_dictionary_lexical_rules(),
        token_dictionary_signals=None,
    )

    assert len(nucleus) < len(" ".join(text.split()))
    assert "migration_checklist.yml" in nucleus.lower()
    assert "rollback-plan" in nucleus.lower()
    assert "compare dia and fellou" not in nucleus.lower()


def test_task_fragment_view_prefers_task_bearing_content_over_explainer_filler():
    view = cross_thread_module._task_fragment_view(
        "Let's compare Dia and Fellou and explain why each one feels useful. "
        "Retry migration_checklist.yml, validate rollback-plan, and rerun the failed rollout before release."
    )

    normalized_view = " ".join(view.lower().split())
    assert "migration_checklist.yml" in normalized_view
    assert "rollback-plan" in normalized_view
    assert "compare dia and fellou" not in normalized_view
    assert "feels useful" not in normalized_view


def test_meta_structural_prompt_fragment_is_penalized():
    lexical_rules = cross_thread_module.default_token_dictionary_lexical_rules()

    score = cross_thread_module._score_fragment(
        "Make sure to include schema_version and return a JSON object with the output fields.",
        lexical_rules=lexical_rules,
        token_dictionary_signals=None,
    )

    assert score < 0


def test_prompt_residue_fragment_is_strongly_penalized():
    lexical_rules = cross_thread_module.default_token_dictionary_lexical_rules()

    score = cross_thread_module._score_fragment(
        "GPT-4o returned 1 images. From now on, do not say or show anything. Please end this turn now.",
        lexical_rules=lexical_rules,
        token_dictionary_signals=None,
    )

    assert score <= -1.0


def test_pure_formatting_schema_guidance_does_not_become_task_nucleus():
    view = cross_thread_module._task_fragment_view(
        "Return a JSON object with schema_version and message_idx fields. "
        "Use markdown code fences for the output format."
    )

    assert view == ""


def test_prompt_residue_does_not_become_task_nucleus():
    nucleus = cross_thread_module._task_nucleus_text(
        "GPT-4o returned 1 images. From now on, do not say or show anything. Please end this turn now.",
        lexical_rules=cross_thread_module.default_token_dictionary_lexical_rules(),
        token_dictionary_signals=None,
    )

    assert nucleus == ""


def test_concrete_task_fragment_outranks_meta_structural_fragment():
    lexical_rules = cross_thread_module.default_token_dictionary_lexical_rules()

    meta_score = cross_thread_module._score_fragment(
        "The output should contain schema_version and markdown fields.",
        lexical_rules=lexical_rules,
        token_dictionary_signals=None,
    )
    task_score = cross_thread_module._score_fragment(
        "Fix relay_config.json and retry the failed rollout before deploy.",
        lexical_rules=lexical_rules,
        token_dictionary_signals=None,
    )

    assert task_score > meta_score
    assert task_score >= cross_thread_module._SELECTIVE_CONTEXT_MIN_FRAGMENT_SCORE


def test_build_cross_thread_candidate_rows_loads_external_lexical_rules(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    topics = [
        _topic_record(
            topic_id="topic-source",
            conversation_id="conv-a",
            cluster_id="cluster-a",
            window_id="window-a",
            label="deployment planning",
            keywords=[],
            excerpt=(
                "Reopen the release notes for migration_checklist.yml and validate the rollback-plan"
                " before tonight's deploy."
            ),
            message_ids=["a-1"],
            first_seen=100,
        ),
        _topic_record(
            topic_id="topic-target",
            conversation_id="conv-b",
            cluster_id="cluster-b",
            window_id="window-b",
            label="release retry",
            keywords=[],
            excerpt=(
                "After last week's failure, retry the rollout and update rollback-plan plus"
                " migration_checklist.yml for the next attempt."
            ),
            message_ids=["b-1"],
            first_seen=100 + (3 * 24 * 60 * 60 * 1000),
        ),
    ]
    _write_json(root / "l3" / "semantic-topics" / "topics.json", _topics_artifact(topics))
    lexical_rules = {
        "artifact_type": "token_dictionary_lexical_rules",
        "schema_version": "0.1",
        "producer_layer": "L3",
        "provider_id": "openai",
        "created_at": "2026-04-18T00:00:00Z",
        "source_inputs": ["seeded_lexical_rules"],
        "reproducibility_note": "Rebuildable from seeded lexical resources bundled with this build",
        "seeded_rules": {
                "specificity_generic_tokens": [
                    "migration_checklist.yml",
                    "rollback-plan",
                    "deploy",
                    "failure",
                    "release",
                    "notes",
                    "retry",
                    "rollout",
                    "update",
                    "validate"
                ],
                "reflective_tokens": [],
                "task_fragment_action_tokens": [],
                "task_fragment_state_tokens": [],
                "task_fragment_explanatory_tokens": [],
                "task_fragment_noise_markers": []
            },
    }
    _write_json(token_dictionary_lexical_rules_path(root), lexical_rules)

    rows = build_cross_thread_candidate_rows(root)

    assert rows == []


def test_build_cross_thread_candidate_rows_keeps_existing_behavior_without_token_dictionary_artifacts(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    _write_topics_fixture(root)

    rows = build_cross_thread_candidate_rows(root)

    assert rows
    assert all(
        not any(
            code.startswith("dictionary_token_overlap")
            or code.startswith("bundle_overlap")
            or code == "nucleus_overlap_specific"
            for code in row["evidence"]["reason_codes"]
        )
        for row in rows
    )


def test_build_cross_thread_candidate_rows_adds_dictionary_token_overlap_and_bundle_evidence(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    topics = [
        _topic_record(
            topic_id="topic-a",
            conversation_id="conv-a",
            cluster_id="cluster-a",
            window_id="window-a",
            label="release planning",
            keywords=[],
            excerpt=(
                "Retry migration_checklist.yml and validate rollback-plan before the next deploy."
            ),
            message_ids=["a-1"],
            first_seen=100,
        ),
        _topic_record(
            topic_id="topic-b",
            conversation_id="conv-b",
            cluster_id="cluster-b",
            window_id="window-b",
            label="release planning",
            keywords=[],
            excerpt=(
                "Update rollback-plan and rerun migration_checklist.yml checks before deploy."
            ),
            message_ids=["b-1"],
            first_seen=100 + (3 * 24 * 60 * 60 * 1000),
        ),
    ]
    _write_json(root / "l3" / "semantic-topics" / "topics.json", _topics_artifact(topics))
    _write_token_dictionary_signals_fixture(
        root,
        tokens=[
            {
                "token": "migration_checklist.yml",
                "normalized": "migration_checklist.yml",
                "count": 4,
                "first_seen": 100,
                "last_seen": 300,
                "conversations": ["conv-a", "conv-b"],
                "topics": ["topic-a", "topic-b"],
                "role_hints": {"user": 4},
                "cooccurrence": ["rollback-plan"],
                "conversation_count": 2,
                "topic_count": 2,
            },
            {
                "token": "rollback-plan",
                "normalized": "rollback-plan",
                "count": 4,
                "first_seen": 100,
                "last_seen": 300,
                "conversations": ["conv-a", "conv-b"],
                "topics": ["topic-a", "topic-b"],
                "role_hints": {"user": 4},
                "cooccurrence": ["migration_checklist.yml"],
                "conversation_count": 2,
                "topic_count": 2,
            },
        ],
        bundles=[
            {
                "bundle_id": "bundle_001",
                "tokens": ["migration_checklist.yml", "rollback-plan"],
                "weight": 0.84,
            }
        ],
    )

    rows = build_cross_thread_candidate_rows(root)

    row = next(
        candidate
        for candidate in rows
        if candidate["source_topic_id"] == "topic-a"
        and candidate["target_topic_id"] == "topic-b"
    )
    assert "dictionary_token_overlap_dense" in row["evidence"]["reason_codes"]
    assert "bundle_overlap_concentrated" in row["evidence"]["reason_codes"]
    assert "nucleus_overlap_specific" in row["evidence"]["reason_codes"]


def test_build_cross_thread_candidate_rows_adds_bundle_overlap_without_exact_dictionary_token_overlap(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    topics = [
        _topic_record(
            topic_id="topic-a",
            conversation_id="conv-a",
            cluster_id="cluster-a",
            window_id="window-a",
            label="release planning",
            keywords=[],
            excerpt="Validate migration_checklist.yml before tonight's deploy.",
            message_ids=["a-1"],
            first_seen=100,
        ),
        _topic_record(
            topic_id="topic-b",
            conversation_id="conv-b",
            cluster_id="cluster-b",
            window_id="window-b",
            label="release planning",
            keywords=[],
            excerpt="Confirm rollback-plan for the deploy handoff.",
            message_ids=["b-1"],
            first_seen=100 + (3 * 24 * 60 * 60 * 1000),
        ),
    ]
    _write_json(root / "l3" / "semantic-topics" / "topics.json", _topics_artifact(topics))
    _write_token_dictionary_signals_fixture(
        root,
        tokens=[
            {
                "token": "migration_checklist.yml",
                "normalized": "migration_checklist.yml",
                "count": 2,
                "first_seen": 100,
                "last_seen": 100,
                "conversations": ["conv-a"],
                "topics": ["topic-a"],
                "role_hints": {"user": 2},
                "cooccurrence": ["rollback-plan"],
                "conversation_count": 1,
                "topic_count": 1,
            },
            {
                "token": "rollback-plan",
                "normalized": "rollback-plan",
                "count": 2,
                "first_seen": 200,
                "last_seen": 200,
                "conversations": ["conv-b"],
                "topics": ["topic-b"],
                "role_hints": {"user": 2},
                "cooccurrence": ["migration_checklist.yml"],
                "conversation_count": 1,
                "topic_count": 1,
            },
        ],
        bundles=[
            {
                "bundle_id": "bundle_001",
                "tokens": ["migration_checklist.yml", "rollback-plan"],
                "weight": 0.79,
            }
        ],
    )

    rows = build_cross_thread_candidate_rows(root, min_score=0.0)

    row = next(
        candidate
        for candidate in rows
        if candidate["source_topic_id"] == "topic-a"
        and candidate["target_topic_id"] == "topic-b"
    )
    assert "dictionary_token_overlap_weak" not in row["evidence"]["reason_codes"]
    assert "dictionary_token_overlap_dense" not in row["evidence"]["reason_codes"]
    assert "bundle_overlap_broad" not in row["evidence"]["reason_codes"]


def test_dense_nucleus_overlap_scores_higher_than_broad_technical_overlap(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    topics = [
        _topic_record(
            topic_id="topic-source",
            conversation_id="conv-a",
            cluster_id="cluster-a",
            window_id="window-a",
            label="release planning",
            keywords=[],
            excerpt=(
                "Retry migration_checklist.yml and validate rollback-plan before release deploy."
            ),
            message_ids=["a-1"],
            first_seen=100,
        ),
        _topic_record(
            topic_id="topic-dense",
            conversation_id="conv-b",
            cluster_id="cluster-b",
            window_id="window-b",
            label="release planning",
            keywords=[],
            excerpt=(
                "Update rollback-plan and rerun migration_checklist.yml before release deploy."
            ),
            message_ids=["b-1"],
            first_seen=100 + (3 * 24 * 60 * 60 * 1000),
        ),
        _topic_record(
            topic_id="topic-broad",
            conversation_id="conv-c",
            cluster_id="cluster-c",
            window_id="window-c",
            label="technical guidance",
            keywords=[],
            excerpt=(
                "Explain json markdown output schema and release deploy handoff formatting for operators."
            ),
            message_ids=["c-1"],
            first_seen=100 + (4 * 24 * 60 * 60 * 1000),
        ),
    ]
    _write_json(root / "l3" / "semantic-topics" / "topics.json", _topics_artifact(topics))
    _write_token_dictionary_signals_fixture(
        root,
        tokens=[
            {
                "token": "migration_checklist.yml",
                "normalized": "migration_checklist.yml",
                "count": 3,
                "first_seen": 100,
                "last_seen": 300,
                "conversations": ["conv-a", "conv-b"],
                "topics": ["topic-source", "topic-dense"],
                "role_hints": {"user": 3},
                "cooccurrence": ["rollback-plan", "deploy"],
                "conversation_count": 2,
                "topic_count": 2,
            },
            {
                "token": "rollback-plan",
                "normalized": "rollback-plan",
                "count": 3,
                "first_seen": 100,
                "last_seen": 300,
                "conversations": ["conv-a", "conv-b"],
                "topics": ["topic-source", "topic-dense"],
                "role_hints": {"user": 3},
                "cooccurrence": ["migration_checklist.yml", "deploy"],
                "conversation_count": 2,
                "topic_count": 2,
            },
            {
                "token": "deploy",
                "normalized": "deploy",
                "count": 6,
                "first_seen": 100,
                "last_seen": 400,
                "conversations": ["conv-a", "conv-b", "conv-c"],
                "topics": ["topic-source", "topic-dense", "topic-broad"],
                "role_hints": {"user": 6},
                "cooccurrence": ["migration_checklist.yml", "rollback-plan", "json"],
                "conversation_count": 3,
                "topic_count": 3,
            },
            {
                "token": "release",
                "normalized": "release",
                "count": 6,
                "first_seen": 100,
                "last_seen": 400,
                "conversations": ["conv-a", "conv-b", "conv-c"],
                "topics": ["topic-source", "topic-dense", "topic-broad"],
                "role_hints": {"user": 6},
                "cooccurrence": ["deploy", "migration_checklist.yml", "rollback-plan"],
                "conversation_count": 3,
                "topic_count": 3,
            },
            {
                "token": "json",
                "normalized": "json",
                "count": 4,
                "first_seen": 100,
                "last_seen": 400,
                "conversations": ["conv-c"],
                "topics": ["topic-broad"],
                "role_hints": {"user": 4},
                "cooccurrence": ["markdown", "schema"],
                "conversation_count": 1,
                "topic_count": 1,
            },
            {
                "token": "markdown",
                "normalized": "markdown",
                "count": 4,
                "first_seen": 100,
                "last_seen": 400,
                "conversations": ["conv-c"],
                "topics": ["topic-broad"],
                "role_hints": {"user": 4},
                "cooccurrence": ["json", "schema"],
                "conversation_count": 1,
                "topic_count": 1,
            },
            {
                "token": "schema",
                "normalized": "schema",
                "count": 4,
                "first_seen": 100,
                "last_seen": 400,
                "conversations": ["conv-c"],
                "topics": ["topic-broad"],
                "role_hints": {"user": 4},
                "cooccurrence": ["json", "markdown"],
                "conversation_count": 1,
                "topic_count": 1,
            },
        ],
        bundles=[
            {
                "bundle_id": "bundle_001",
                "tokens": ["migration_checklist.yml", "rollback-plan", "deploy", "release"],
                "weight": 0.88,
            },
            {
                "bundle_id": "bundle_002",
                "tokens": ["json", "markdown", "schema"],
                "weight": 0.81,
            },
        ],
    )

    rows = build_cross_thread_candidate_rows(root, min_score=0.0)
    dense_row = next(
        candidate
        for candidate in rows
        if candidate["source_topic_id"] == "topic-source"
        and candidate["target_topic_id"] == "topic-dense"
    )
    broad_row = next(
        candidate
        for candidate in rows
        if candidate["source_topic_id"] == "topic-source"
        and candidate["target_topic_id"] == "topic-broad"
    )

    assert dense_row["score"] > broad_row["score"]
    assert "dictionary_token_overlap_dense" in dense_row["evidence"]["reason_codes"]
    assert "bundle_overlap_concentrated" in dense_row["evidence"]["reason_codes"]
    assert "nucleus_overlap_specific" in dense_row["evidence"]["reason_codes"]
    assert "dictionary_token_overlap_dense" not in broad_row["evidence"]["reason_codes"]


def test_task_fragment_view_can_use_token_dictionary_support_when_present(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_token_dictionary_signals_fixture(
        root,
        tokens=[
            {
                "token": "falcon",
                "normalized": "falcon",
                "count": 3,
                "first_seen": 100,
                "last_seen": 300,
                "conversations": ["conv-a", "conv-b"],
                "topics": ["topic-a"],
                "role_hints": {"user": 3},
                "cooccurrence": ["handoff", "relay"],
                "conversation_count": 2,
                "topic_count": 1,
            },
            {
                "token": "handoff",
                "normalized": "handoff",
                "count": 3,
                "first_seen": 100,
                "last_seen": 300,
                "conversations": ["conv-a", "conv-b"],
                "topics": ["topic-a"],
                "role_hints": {"user": 3},
                "cooccurrence": ["falcon", "relay"],
                "conversation_count": 2,
                "topic_count": 1,
            },
            {
                "token": "relay",
                "normalized": "relay",
                "count": 3,
                "first_seen": 100,
                "last_seen": 300,
                "conversations": ["conv-a", "conv-b"],
                "topics": ["topic-a"],
                "role_hints": {"user": 3},
                "cooccurrence": ["falcon", "handoff"],
                "conversation_count": 2,
                "topic_count": 1,
            },
        ],
        bundles=[
            {
                "bundle_id": "bundle_001",
                "tokens": ["falcon", "handoff", "relay"],
                "weight": 0.82,
            }
        ],
    )
    signals = load_token_dictionary_signals(root)

    text = (
        "This is mostly an explanation of tradeoffs and background context. "
        "After falcon handoff relay."
    )

    without_dictionary = cross_thread_module._task_fragment_view(text)
    with_dictionary = cross_thread_module._task_fragment_view(
        text,
        token_dictionary_signals=signals,
    )

    assert without_dictionary == ""
    assert "after falcon handoff relay" in with_dictionary.lower()


def test_dictionary_support_alone_does_not_rescue_meta_fragment(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_token_dictionary_signals_fixture(
        root,
        tokens=[
            {
                "token": "schema_version",
                "normalized": "schema_version",
                "count": 5,
                "first_seen": 100,
                "last_seen": 300,
                "conversations": ["conv-a"],
                "topics": ["topic-a"],
                "role_hints": {"user": 5},
                "cooccurrence": ["message_idx"],
                "conversation_count": 1,
                "topic_count": 1,
            },
            {
                "token": "message_idx",
                "normalized": "message_idx",
                "count": 5,
                "first_seen": 100,
                "last_seen": 300,
                "conversations": ["conv-a"],
                "topics": ["topic-a"],
                "role_hints": {"user": 5},
                "cooccurrence": ["schema_version"],
                "conversation_count": 1,
                "topic_count": 1,
            },
        ],
        bundles=[
            {
                "bundle_id": "bundle_001",
                "tokens": ["schema_version", "message_idx"],
                "weight": 0.9,
            }
        ],
    )
    signals = load_token_dictionary_signals(root)

    view = cross_thread_module._task_fragment_view(
        "Return a JSON object with schema_version and message_idx fields.",
        token_dictionary_signals=signals,
    )

    assert view == ""


def test_prompt_residue_pair_does_not_emit_dense_overlap_reasons(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    topics = [
        _topic_record(
            topic_id="topic-a",
            conversation_id="conv-a",
            cluster_id="cluster-a",
            window_id="window-a",
            label="image wrapper",
            keywords=[],
            excerpt=(
                "GPT-4o returned 1 images. From now on, do not say or show ANYTHING. "
                "Please end this turn now."
            ),
            message_ids=["a-1"],
            first_seen=100,
        ),
        _topic_record(
            topic_id="topic-b",
            conversation_id="conv-b",
            cluster_id="cluster-b",
            window_id="window-b",
            label="image wrapper",
            keywords=[],
            excerpt=(
                "GPT-4o returned 1 images. Do not summarize the image and do not ask followup question. "
                "Please end this turn now."
            ),
            message_ids=["b-1"],
            first_seen=100 + (2 * 24 * 60 * 60 * 1000),
        ),
    ]
    _write_json(root / "l3" / "semantic-topics" / "topics.json", _topics_artifact(topics))
    _write_token_dictionary_signals_fixture(
        root,
        tokens=[
            {
                "token": "returned",
                "normalized": "returned",
                "count": 4,
                "first_seen": 100,
                "last_seen": 200,
                "conversations": ["conv-a", "conv-b"],
                "topics": ["topic-a", "topic-b"],
                "role_hints": {"assistant": 4},
                "cooccurrence": ["images", "turn"],
                "conversation_count": 2,
                "topic_count": 2,
            },
            {
                "token": "images",
                "normalized": "images",
                "count": 4,
                "first_seen": 100,
                "last_seen": 200,
                "conversations": ["conv-a", "conv-b"],
                "topics": ["topic-a", "topic-b"],
                "role_hints": {"assistant": 4},
                "cooccurrence": ["returned", "turn"],
                "conversation_count": 2,
                "topic_count": 2,
            },
            {
                "token": "turn",
                "normalized": "turn",
                "count": 4,
                "first_seen": 100,
                "last_seen": 200,
                "conversations": ["conv-a", "conv-b"],
                "topics": ["topic-a", "topic-b"],
                "role_hints": {"assistant": 4},
                "cooccurrence": ["returned", "images"],
                "conversation_count": 2,
                "topic_count": 2,
            },
        ],
        bundles=[
            {
                "bundle_id": "bundle_001",
                "tokens": ["returned", "images", "turn"],
                "weight": 0.9,
            }
        ],
    )

    rows = build_cross_thread_candidate_rows(root, min_score=0.0)

    assert rows == []


def test_build_task_nucleus_falls_back_to_original_text_when_no_strong_fragments():
    text = "This is a broad explanation of tradeoffs and background context."

    nucleus = cross_thread_module._task_nucleus_text(
        text,
        lexical_rules=cross_thread_module.default_token_dictionary_lexical_rules(),
        token_dictionary_signals=None,
    )

    assert nucleus == text


def test_real_task_bearing_technical_fragment_still_survives():
    view = cross_thread_module._task_fragment_view(
        "Update relay_config.json, remove the stale field, and retry the failed deploy."
    )

    normalized_view = view.lower()
    assert "relay_config.json" in normalized_view
    assert "retry the failed deploy" in normalized_view


def test_real_task_pair_outranks_prompt_residue_pair(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    topics = [
        _topic_record(
            topic_id="topic-source-task",
            conversation_id="conv-a",
            cluster_id="cluster-a",
            window_id="window-a",
            label="release repair",
            keywords=[],
            excerpt="Fix relay_config.json and retry the failed deploy before release.",
            message_ids=["a-1"],
            first_seen=100,
        ),
        _topic_record(
            topic_id="topic-target-task",
            conversation_id="conv-b",
            cluster_id="cluster-b",
            window_id="window-b",
            label="release repair",
            keywords=[],
            excerpt="Update relay_config.json and rerun the failed deploy before release.",
            message_ids=["b-1"],
            first_seen=100 + (2 * 24 * 60 * 60 * 1000),
        ),
        _topic_record(
            topic_id="topic-source-residue",
            conversation_id="conv-c",
            cluster_id="cluster-c",
            window_id="window-c",
            label="image wrapper",
            keywords=[],
            excerpt="GPT-4o returned 1 images. Please end this turn now and do not summarize the image.",
            message_ids=["c-1"],
            first_seen=100,
        ),
        _topic_record(
            topic_id="topic-target-residue",
            conversation_id="conv-d",
            cluster_id="cluster-d",
            window_id="window-d",
            label="image wrapper",
            keywords=[],
            excerpt="GPT-4o returned 1 images. From now on, do not ask followup question. Please end this turn now.",
            message_ids=["d-1"],
            first_seen=100 + (2 * 24 * 60 * 60 * 1000),
        ),
    ]
    _write_json(root / "l3" / "semantic-topics" / "topics.json", _topics_artifact(topics))
    _write_token_dictionary_signals_fixture(
        root,
        tokens=[
            {
                "token": "relay_config.json",
                "normalized": "relay_config.json",
                "count": 2,
                "first_seen": 100,
                "last_seen": 200,
                "conversations": ["conv-a", "conv-b"],
                "topics": ["topic-source-task", "topic-target-task"],
                "role_hints": {"user": 2},
                "cooccurrence": ["deploy", "release"],
                "conversation_count": 2,
                "topic_count": 2,
            },
            {
                "token": "deploy",
                "normalized": "deploy",
                "count": 2,
                "first_seen": 100,
                "last_seen": 200,
                "conversations": ["conv-a", "conv-b"],
                "topics": ["topic-source-task", "topic-target-task"],
                "role_hints": {"user": 2},
                "cooccurrence": ["relay_config.json", "release"],
                "conversation_count": 2,
                "topic_count": 2,
            },
            {
                "token": "release",
                "normalized": "release",
                "count": 2,
                "first_seen": 100,
                "last_seen": 200,
                "conversations": ["conv-a", "conv-b"],
                "topics": ["topic-source-task", "topic-target-task"],
                "role_hints": {"user": 2},
                "cooccurrence": ["relay_config.json", "deploy"],
                "conversation_count": 2,
                "topic_count": 2,
            },
            {
                "token": "returned",
                "normalized": "returned",
                "count": 2,
                "first_seen": 100,
                "last_seen": 200,
                "conversations": ["conv-c", "conv-d"],
                "topics": ["topic-source-residue", "topic-target-residue"],
                "role_hints": {"assistant": 2},
                "cooccurrence": ["images", "turn"],
                "conversation_count": 2,
                "topic_count": 2,
            },
            {
                "token": "images",
                "normalized": "images",
                "count": 2,
                "first_seen": 100,
                "last_seen": 200,
                "conversations": ["conv-c", "conv-d"],
                "topics": ["topic-source-residue", "topic-target-residue"],
                "role_hints": {"assistant": 2},
                "cooccurrence": ["returned", "turn"],
                "conversation_count": 2,
                "topic_count": 2,
            },
            {
                "token": "turn",
                "normalized": "turn",
                "count": 2,
                "first_seen": 100,
                "last_seen": 200,
                "conversations": ["conv-c", "conv-d"],
                "topics": ["topic-source-residue", "topic-target-residue"],
                "role_hints": {"assistant": 2},
                "cooccurrence": ["returned", "images"],
                "conversation_count": 2,
                "topic_count": 2,
            },
        ],
        bundles=[
            {
                "bundle_id": "bundle_001",
                "tokens": ["relay_config.json", "deploy", "release"],
                "weight": 0.86,
            },
            {
                "bundle_id": "bundle_002",
                "tokens": ["returned", "images", "turn"],
                "weight": 0.9,
            },
        ],
    )

    rows = build_cross_thread_candidate_rows(root, min_score=0.0)

    task_row = next(
        row
        for row in rows
        if row["source_topic_id"] == "topic-source-task"
        and row["target_topic_id"] == "topic-target-task"
    )
    assert "dictionary_token_overlap_dense" in task_row["evidence"]["reason_codes"]
    assert "bundle_overlap_concentrated" in task_row["evidence"]["reason_codes"]
    assert "nucleus_overlap_specific" in task_row["evidence"]["reason_codes"]
    assert not any(
        row["source_topic_id"] == "topic-source-residue"
        and row["target_topic_id"] == "topic-target-residue"
        for row in rows
    )


def test_build_cross_thread_candidate_rows_computes_local_context_delta_for_reentry_like_match(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    topics = [
        _topic_record(
            topic_id="topic-source",
            conversation_id="conv-a",
            cluster_id="cluster-a",
            window_id="window-a",
            label="Migration checklist",
            keywords=["migration", "rollback"],
            excerpt="Finalize the migration checklist and confirm rollback gates before release.",
            message_ids=["a-1"],
            normalized_label="request",
            raw_label="implementation_request",
            first_seen=1000,
            last_seen=1100,
        ),
        _topic_record(
            topic_id="topic-target-prev",
            conversation_id="conv-b",
            cluster_id="cluster-b-prev",
            window_id="window-b-prev",
            label="Lunch planning",
            keywords=["lunch"],
            excerpt="Compare nearby cafes and decide on lunch seating.",
            message_ids=["b-1"],
            normalized_label="proposal",
            raw_label="social_plan",
            first_seen=2000,
            last_seen=2100,
        ),
        _topic_record(
            topic_id="topic-target",
            conversation_id="conv-b",
            cluster_id="cluster-b",
            window_id="window-b",
            label="Migration checklist",
            keywords=["migration", "rollback"],
            excerpt="Finalize the migration checklist and confirm rollback gates before release.",
            message_ids=["b-2"],
            normalized_label="request",
            raw_label="implementation_request",
            first_seen=4000,
            last_seen=4100,
        ),
    ]
    _write_json(root / "l3" / "semantic-topics" / "topics.json", _topics_artifact(topics))

    rows = build_cross_thread_candidate_rows(root)

    row = next(
        candidate
        for candidate in rows
        if candidate["source_topic_id"] == "topic-source"
        and candidate["target_topic_id"] == "topic-target"
    )
    assert row["local_context_delta"] is not None
    assert row["local_context_delta"] > 0.5


def test_build_cross_thread_candidate_rows_applies_high_topic_excerpt_combination_bonus(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    topics = [
        _topic_record(
            topic_id="topic-a",
            conversation_id="conv-a",
            cluster_id="cluster-a",
            window_id="window-a",
            label="dia use case browser",
            keywords=[],
            excerpt=(
                "Dia is already installed, but the useful scenario is still unclear. "
                "We should explain when it actually helps."
            ),
            message_ids=["a-1"],
            first_seen=100,
        ),
        _topic_record(
            topic_id="topic-b",
            conversation_id="conv-b",
            cluster_id="cluster-b",
            window_id="window-b",
            label="dia use case browser",
            keywords=[],
            excerpt=(
                "Dia is already installed, but the useful scenario is still unclear. "
                "Let's explain where it actually helps."
            ),
            message_ids=["b-1"],
            first_seen=100,
        ),
    ]
    _write_json(root / "l3" / "semantic-topics" / "topics.json", _topics_artifact(topics))

    rows = build_cross_thread_candidate_rows(root, min_score=0.58)

    assert len(rows) == 2
    row = next(
        candidate
        for candidate in rows
        if candidate["source_topic_id"] == "topic-a"
        and candidate["target_topic_id"] == "topic-b"
    )
    assert row["score"] > 0.58
    assert "topic_label_similarity_high" in row["evidence"]["reason_codes"]
    assert "excerpt_similarity_high" in row["evidence"]["reason_codes"]
    assert "topic_excerpt_combination_high" in row["evidence"]["reason_codes"]


def test_build_cross_thread_candidate_rows_does_not_apply_high_topic_excerpt_combination_bonus_to_partial_match(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    topics = [
        _topic_record(
            topic_id="topic-a",
            conversation_id="conv-a",
            cluster_id="cluster-a",
            window_id="window-a",
            label="dia use case browser",
            keywords=[],
            excerpt=(
                "Dia is already installed, but the useful scenario is still unclear. "
                "We should explain when it actually helps."
            ),
            message_ids=["a-1"],
            first_seen=100,
        ),
        _topic_record(
            topic_id="topic-b",
            conversation_id="conv-b",
            cluster_id="cluster-b",
            window_id="window-b",
            label="dia use case browser",
            keywords=[],
            excerpt=(
                "Dia seems interesting, but the actual use case is still somewhat vague. "
                "We can summarize a few possible benefits."
            ),
            message_ids=["b-1"],
            first_seen=100,
        ),
    ]
    _write_json(root / "l3" / "semantic-topics" / "topics.json", _topics_artifact(topics))

    rows = build_cross_thread_candidate_rows(root)

    assert rows == []
    strict_rows = build_cross_thread_candidate_rows(root, min_score=0.0)
    assert len(strict_rows) == 2
    row = next(
        candidate
        for candidate in strict_rows
        if candidate["source_topic_id"] == "topic-a"
        and candidate["target_topic_id"] == "topic-b"
    )
    assert "topic_label_similarity_high" in row["evidence"]["reason_codes"]
    assert "excerpt_similarity_high" not in row["evidence"]["reason_codes"]
    assert "topic_excerpt_combination_high" not in row["evidence"]["reason_codes"]
    assert row["score"] < 0.58


def test_build_cross_thread_candidate_rows_emits_weak_recurrence_candidate_below_similarity_threshold(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    topics = [
        _topic_record(
            topic_id="topic-source",
            conversation_id="conv-a",
            cluster_id="cluster-a",
            window_id="window-a",
            label="deployment planning",
            keywords=[],
            excerpt=(
                "Reopen the release notes for migration_checklist.yml and validate the rollback-plan"
                " before tonight's deploy."
            ),
            message_ids=["a-1"],
            first_seen=100,
        ),
        _topic_record(
            topic_id="topic-target",
            conversation_id="conv-b",
            cluster_id="cluster-b",
            window_id="window-b",
            label="release retry",
            keywords=[],
            excerpt=(
                "After last week's failure, retry the rollout and update rollback-plan plus"
                " migration_checklist.yml for the next attempt."
            ),
            message_ids=["b-1"],
            first_seen=100 + (3 * 24 * 60 * 60 * 1000),
        ),
    ]
    _write_json(root / "l3" / "semantic-topics" / "topics.json", _topics_artifact(topics))

    rows = build_cross_thread_candidate_rows(root)

    assert len(rows) == 2
    row = next(
        candidate
        for candidate in rows
        if candidate["source_topic_id"] == "topic-source"
        and candidate["target_topic_id"] == "topic-target"
    )
    assert 0.1 <= row["score"] < 0.6
    assert "dormant_gap" in row["evidence"]["reason_codes"]
    assert any(
        code in {"anchor_overlap", "anchor_overlap_strong"}
        for code in row["evidence"]["reason_codes"]
    )
    assert "task_like_signal" in row["evidence"]["reason_codes"]
    assert not any(
        code.startswith("weak_recurrence_")
        for code in row["evidence"]["reason_codes"]
    )
    assert row["temporal_gap_seconds"] >= 3 * 24 * 60 * 60


def test_build_cross_thread_candidate_rows_does_not_emit_weak_recurrence_candidate_without_gap(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    topics = [
        _topic_record(
            topic_id="topic-source",
            conversation_id="conv-a",
            cluster_id="cluster-a",
            window_id="window-a",
            label="deployment planning",
            keywords=[],
            excerpt=(
                "Reopen the release notes for migration_checklist.yml and validate the rollback-plan"
                " before tonight's deploy."
            ),
            message_ids=["a-1"],
            first_seen=100,
        ),
        _topic_record(
            topic_id="topic-target",
            conversation_id="conv-b",
            cluster_id="cluster-b",
            window_id="window-b",
            label="release retry",
            keywords=[],
            excerpt=(
                "After the last failure, retry the rollout and update rollback-plan plus"
                " migration_checklist.yml for the next attempt."
            ),
            message_ids=["b-1"],
            first_seen=100 + 1000,
        ),
    ]
    _write_json(root / "l3" / "semantic-topics" / "topics.json", _topics_artifact(topics))

    rows = build_cross_thread_candidate_rows(root)

    assert rows == []


def test_build_cross_thread_candidate_rows_does_not_emit_weak_recurrence_candidate_for_single_anchor_overlap(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    topics = [
        _topic_record(
            topic_id="topic-source",
            conversation_id="conv-a",
            cluster_id="cluster-a",
            window_id="window-a",
            label="deployment planning",
            keywords=[],
            excerpt=(
                "Retry notes mention migration_checklist.yml, but the real task is still undecided."
            ),
            message_ids=["a-1"],
            first_seen=100,
        ),
        _topic_record(
            topic_id="topic-target",
            conversation_id="conv-b",
            cluster_id="cluster-b",
            window_id="window-b",
            label="retrospective",
            keywords=[],
            excerpt=(
                "We vaguely referenced migration_checklist.yml while reflecting on what felt unclear"
                " after the meeting."
            ),
            message_ids=["b-1"],
            first_seen=100 + (3 * 24 * 60 * 60 * 1000),
        ),
    ]
    _write_json(root / "l3" / "semantic-topics" / "topics.json", _topics_artifact(topics))

    rows = build_cross_thread_candidate_rows(root)

    assert rows == []


def test_build_cross_thread_candidate_rows_does_not_emit_weak_recurrence_candidate_for_reflective_span(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    topics = [
        _topic_record(
            topic_id="topic-source",
            conversation_id="conv-a",
            cluster_id="cluster-a",
            window_id="window-a",
            label="meta discussion",
            keywords=[],
            excerpt=(
                "I remember rollback-plan and migration_checklist.yml, but this is mostly about how the"
                " conversation felt and what vibe we had."
            ),
            message_ids=["a-1"],
            first_seen=100,
        ),
        _topic_record(
            topic_id="topic-target",
            conversation_id="conv-b",
            cluster_id="cluster-b",
            window_id="window-b",
            label="reflection",
            keywords=[],
            excerpt=(
                "Thinking again about rollback-plan and migration_checklist.yml, I mostly want to reflect"
                " on the feeling, memory, and relationship around that exchange."
            ),
            message_ids=["b-1"],
            first_seen=100 + (4 * 24 * 60 * 60 * 1000),
        ),
    ]
    _write_json(root / "l3" / "semantic-topics" / "topics.json", _topics_artifact(topics))

    rows = build_cross_thread_candidate_rows(root, min_score=0.0)

    assert len(rows) == 2
    assert all(
        "dormant_gap" not in row["evidence"]["reason_codes"]
        for row in rows
    )


def test_build_cross_thread_candidate_rows_does_not_emit_weak_recurrence_candidate_for_broad_explainer_overlap(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    topics = [
        _topic_record(
            topic_id="topic-source",
            conversation_id="conv-a",
            cluster_id="cluster-a",
            window_id="window-a",
            label="browser agent comparison",
            keywords=[],
            excerpt=(
                "Let's compare Dia and Fellou and explain the difference in browsing style. "
                "migration_checklist.yml and rollback-plan are only examples of structured prompts in that explanation."
            ),
            message_ids=["a-1"],
            first_seen=100,
        ),
        _topic_record(
            topic_id="topic-target",
            conversation_id="conv-b",
            cluster_id="cluster-b",
            window_id="window-b",
            label="browser agent explainer",
            keywords=[],
            excerpt=(
                "This is another organized explanation of Dia versus GenSpark and why each approach feels useful. "
                "rollback-plan together with migration_checklist.yml is just an example scaffold for the comparison."
            ),
            message_ids=["b-1"],
            first_seen=100 + (4 * 24 * 60 * 60 * 1000),
        ),
    ]
    _write_json(root / "l3" / "semantic-topics" / "topics.json", _topics_artifact(topics))

    rows = build_cross_thread_candidate_rows(root)

    assert rows == []


def test_build_cross_thread_candidate_rows_unions_similarity_and_weak_recurrence_routes(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    topics = [
        _topic_record(
            topic_id="topic-source",
            conversation_id="conv-a",
            cluster_id="cluster-a",
            window_id="window-a",
            label="migration checklist",
            keywords=["migration", "rollback"],
            excerpt=(
                "Finalize migration_checklist.yml and verify rollback-plan before the production rollout."
            ),
            message_ids=["a-1"],
            first_seen=100,
        ),
        _topic_record(
            topic_id="topic-strong",
            conversation_id="conv-b",
            cluster_id="cluster-b",
            window_id="window-b",
            label="migration checklist",
            keywords=["migration", "rollback"],
            excerpt=(
                "Finalize migration_checklist.yml and confirm rollback-plan before production rollout."
            ),
            message_ids=["b-1"],
            first_seen=100 + 1000,
        ),
        _topic_record(
            topic_id="topic-weak",
            conversation_id="conv-c",
            cluster_id="cluster-c",
            window_id="window-c",
            label="release retry",
            keywords=[],
            excerpt=(
                "Retry the rollout after the failed deploy and revise migration_checklist.yml"
                " together with rollback-plan before the next attempt."
            ),
            message_ids=["c-1"],
            first_seen=100 + (5 * 24 * 60 * 60 * 1000),
        ),
    ]
    _write_json(root / "l3" / "semantic-topics" / "topics.json", _topics_artifact(topics))

    rows = build_cross_thread_candidate_rows(root, top_per_source=1)
    source_rows = [
        row
        for row in rows
        if row["source_topic_id"] == "topic-source"
    ]

    assert {row["target_topic_id"] for row in source_rows} == {"topic-strong", "topic-weak"}
    weak_row = next(row for row in source_rows if row["target_topic_id"] == "topic-weak")
    assert "dormant_gap" in weak_row["evidence"]["reason_codes"]
    assert any(
        code in {"anchor_overlap", "anchor_overlap_strong"}
        for code in weak_row["evidence"]["reason_codes"]
    )


def test_build_cross_thread_candidate_rows_filters_repeated_artifact_instruction_pairs(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    topics = [
        _topic_record(
            topic_id="topic-a",
            conversation_id="conv-a",
            cluster_id="cluster-a",
            window_id="window-a",
            label="Image return instruction",
            keywords=["image", "turn", "instruction"],
            excerpt=(
                "GPT-4O returned 1 images...\n\nFrom now on, DO NOT say or show "
                "ANYTHING.   Please end this turn now."
            ),
            message_ids=["a-1"],
            normalized_label="status_update",
            raw_label="status_note",
            first_seen=100,
        ),
        _topic_record(
            topic_id="topic-b",
            conversation_id="conv-b",
            cluster_id="cluster-b",
            window_id="window-b",
            label="Image return instruction",
            keywords=["image", "turn", "instruction"],
            excerpt=(
                "gpt-4o returned 1 images.  From now on,\nDO NOT say or show "
                "anything. Please end this turn now..."
            ),
            message_ids=["b-1"],
            normalized_label="status_update",
            raw_label="status_note",
            first_seen=100 + (10 * 24 * 60 * 60 * 1000),
        ),
    ]
    _write_json(root / "l3" / "semantic-topics" / "topics.json", _topics_artifact(topics))

    rows = build_cross_thread_candidate_rows(root)
    result = write_cross_thread_candidates_artifact(root)
    summary = json.loads(result["summary_path"].read_text(encoding="utf-8"))

    assert rows == []
    assert summary["candidate_link_count"] == 0
    assert "filtered_low_value_pair_count" not in summary


def test_build_cross_thread_candidate_rows_keeps_repeated_project_summary_pairs(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    topics = [
        _topic_record(
            topic_id="topic-a",
            conversation_id="conv-a",
            cluster_id="cluster-a",
            window_id="window-a",
            label="Project summary",
            keywords=["llm-logparser", "summary", "project"],
            excerpt=(
                "現状サマリ: llm-logparser のCLI構成と責務分離を新規レイナ向けに整理した。"
            ),
            message_ids=["a-1"],
            normalized_label="status_update",
            raw_label="status_note",
            first_seen=100,
        ),
        _topic_record(
            topic_id="topic-b",
            conversation_id="conv-b",
            cluster_id="cluster-b",
            window_id="window-b",
            label="Project summary",
            keywords=["llm-logparser", "summary", "project"],
            excerpt=(
                "現状サマリ: llm-logparser のCLI構成と責務分離を新規メンバー向けに整理した。"
            ),
            message_ids=["b-1"],
            normalized_label="status_update",
            raw_label="status_note",
            first_seen=100 + (3 * 24 * 60 * 60 * 1000),
        ),
    ]
    _write_json(root / "l3" / "semantic-topics" / "topics.json", _topics_artifact(topics))

    rows = build_cross_thread_candidate_rows(root)

    assert len(rows) == 2
    assert {
        (row["source_topic_id"], row["target_topic_id"])
        for row in rows
    } == {
        ("topic-a", "topic-b"),
        ("topic-b", "topic-a"),
    }


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
