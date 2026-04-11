from __future__ import annotations

import json
from pathlib import Path

from llm_logparser.cli.cli import main
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
        "first_seen": 100,
        "last_seen": 100 + len(message_ids),
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


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped:
                rows.append(json.loads(stripped))
    return rows


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
    assert "normalized_label_match" in row["evidence"]["reason_codes"]
    assert "shared_keywords_high" in row["evidence"]["reason_codes"]


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
