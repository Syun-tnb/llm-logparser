from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_logparser.cli.cli import main
from llm_logparser.core.analyzer_topic_lifecycle import (
    TopicLifecycleError,
    topic_lifecycle_json_path,
    topic_lifecycle_markdown_path,
    write_topic_lifecycle_artifacts,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _cross_thread_candidate(
    *,
    score: float = 0.4,
    continuity_mask: bool = True,
    temporal_gap_seconds: int = 86400,
    dormancy_score: float = 0.5,
    reason_codes: list[str] | None = None,
) -> dict:
    if reason_codes is None:
        reason_codes = [
            "timestamp_distance_high",
            "dormant_gap",
            "shared_keywords_high",
        ]
    return {
        "record_type": "cross_thread_candidate",
        "schema_version": "0.3",
        "provider_id": "openai",
        "source_conversation_id": "conv-a",
        "target_conversation_id": "conv-b",
        "source_topic_id": "topic-a",
        "target_topic_id": "topic-b",
        "source_span_id": "span-a",
        "target_span_id": "span-b",
        "source_message_ids": ["a1"],
        "target_message_ids": ["b1"],
        "score": score,
        "rank": 1,
        "continuity_mask": continuity_mask,
        "temporal_gap_seconds": temporal_gap_seconds,
        "dormancy_score": dormancy_score,
        "evidence": {
            "reason_codes": reason_codes,
            "continuity_mask": continuity_mask,
            "temporal_gap_seconds": temporal_gap_seconds,
            "dormancy_score": dormancy_score,
        },
    }


def _review_candidate() -> dict:
    return {
        "record_type": "review_candidate",
        "schema_version": "0.1",
        "candidate_id": "review_alpha",
        "candidate_type": "cross_thread_link",
        "status": "candidate",
        "activation_state": "requires_review",
        "source_artifact": "l3/cross-thread-candidates/candidates.jsonl",
        "source_candidate_id": "cross_alpha",
        "source_command": "analyze cross-thread-candidates",
        "provider_id": "openai",
        "scope": "provider",
        "evidence_refs": [],
        "diagnostics": {
            "reason_codes": ["anchor_overlap"],
            "score": 0.4,
        },
        "proposed_change": {},
        "risk_flags": ["low_score", "continuity_masked"],
        "review_notes": None,
    }


def _lexical_candidate() -> dict:
    return {
        "record_type": "lexical_rule_candidate",
        "schema_version": "0.1",
        "provider_id": "openai",
        "candidate_id": "lex_alpha",
        "candidate_type": "generic_scoring_token",
        "value": "alpha",
        "value_kind": "token",
        "evidence": {
            "reason_codes": ["high_conversation_spread"],
        },
    }


def _topic_summary() -> dict:
    return {
        "record_type": "topic_summary",
        "schema_version": "0.1",
        "conversation_id": "conv-a",
        "segment_id": "seg-a",
        "title": "Open follow-up",
        "summary": "A topic with no clear closure.",
        "conclusion_status": "unknown",
    }


def test_topic_lifecycle_missing_inputs_warn(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    root.mkdir(parents=True)

    result = write_topic_lifecycle_artifacts(root)

    report = json.loads(topic_lifecycle_json_path(root).read_text(encoding="utf-8"))
    markdown = topic_lifecycle_markdown_path(root).read_text(encoding="utf-8")
    assert result["candidate_count"] == 0
    assert report["candidate_count"] == 0
    assert report["total_input_rows"] == 0
    assert report["topic_summary_row_count"] == 0
    assert report["candidate_counts_by_type"] == {}
    assert len(report["warnings"]) == 4
    assert report["diagnostics_mode"] == "candidate_lifecycle_proxy_only"
    assert report["diagnostic_thresholds"] == {
        "low_score_threshold": 0.45,
        "resurfaced_min_score": 0.6,
        "stale_max_score": 0.5,
    }
    assert "Diagnostics only" in markdown
    assert "candidate-lifecycle proxy" in markdown


def test_topic_lifecycle_counts_cross_thread_candidates_and_proxies(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_jsonl(
        root / "l3" / "cross-thread-candidates" / "candidates.jsonl",
        [_cross_thread_candidate(score=0.62)],
    )

    write_topic_lifecycle_artifacts(root)

    report = json.loads(topic_lifecycle_json_path(root).read_text(encoding="utf-8"))
    cross = report["cross_thread_candidates"]
    assert report["candidate_counts_by_source_artifact"] == {
        "l3/cross-thread-candidates/candidates.jsonl": 1,
    }
    assert report["candidate_counts_by_type"] == {"cross_thread_link": 1}
    assert cross["low_score_candidate_count"] == 0
    assert cross["continuity_mask_candidate_count"] == 1
    assert cross["recurring_or_resurfaced_proxy_count"] == 1
    assert cross["resurfaced_candidate_proxy_count"] == 1
    assert cross["stale_candidate_proxy_count"] == 0
    assert report["lifecycle_proxy_counts"] == {
        "continuity_masked": 1,
        "cross_thread_candidate": 1,
        "dormancy_signal": 1,
        "resurfaced_candidate_proxy": 1,
        "temporal_gap_signal": 1,
    }
    assert report["risk_counts"] == {
        "continuity_mask": 1,
    }


def test_topic_lifecycle_counts_review_queue_and_lexical_sources(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_jsonl(root / "l3" / "review-queue" / "candidates.jsonl", [_review_candidate()])
    _write_jsonl(root / "l3" / "lexical-rules" / "candidates.jsonl", [_lexical_candidate()])

    write_topic_lifecycle_artifacts(root)

    report = json.loads(topic_lifecycle_json_path(root).read_text(encoding="utf-8"))
    assert report["candidate_count"] == 2
    assert report["candidate_counts_by_type"] == {
        "cross_thread_link": 1,
        "generic_scoring_token": 1,
    }
    assert report["review_queue_candidates"]["risk_counts"] == {
        "continuity_masked": 1,
        "low_score": 1,
    }
    assert report["lexical_candidates"]["reason_code_counts"] == {
        "high_conversation_spread": 1,
    }


def test_topic_lifecycle_counts_topic_summary_rows(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_jsonl(
        root / "thread-conv-a" / "l3" / "intra-thread-topics" / "topic-summaries.jsonl",
        [_topic_summary()],
    )

    write_topic_lifecycle_artifacts(root)

    report = json.loads(topic_lifecycle_json_path(root).read_text(encoding="utf-8"))
    assert report["candidate_count"] == 0
    assert report["topic_summary_row_count"] == 1
    assert report["total_input_rows"] == 1
    assert report["candidate_counts_by_source_artifact"] == {}
    assert report["row_counts_by_source_artifact"] == {
        "thread-*/l3/intra-thread-topics/topic-summaries.jsonl": 1,
    }
    assert report["candidate_counts_by_type"] == {}
    assert report["topic_summaries"]["row_count"] == 1
    assert report["topic_summaries"]["conclusion_status_counts"] == {"unknown": 1}
    assert report["topic_summaries"]["lifecycle_proxy_counts"] == {
        "topic_summary_unknown": 1,
    }


def test_topic_lifecycle_does_not_promote_generic_timestamp_or_dormant_reasons(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    _write_jsonl(
        root / "l3" / "cross-thread-candidates" / "candidates.jsonl",
        [
            _cross_thread_candidate(
                score=0.62,
                continuity_mask=False,
                dormancy_score=0.0,
                reason_codes=["timestamp_distance_high"],
            ),
            _cross_thread_candidate(
                score=0.62,
                continuity_mask=False,
                temporal_gap_seconds=0,
                reason_codes=["dormant_gap"],
            ),
            _cross_thread_candidate(
                score=0.4,
                continuity_mask=False,
                temporal_gap_seconds=0,
                reason_codes=["dormant_gap"],
            ),
        ],
    )

    write_topic_lifecycle_artifacts(root)

    report = json.loads(topic_lifecycle_json_path(root).read_text(encoding="utf-8"))
    lifecycle_counts = report["lifecycle_proxy_counts"]
    assert lifecycle_counts["cross_thread_candidate"] == 3
    assert lifecycle_counts["temporal_gap_signal"] == 1
    assert lifecycle_counts["dormancy_signal"] == 2
    assert "resurfaced_candidate_proxy" not in lifecycle_counts
    assert lifecycle_counts["stale_candidate_proxy"] == 1


def test_topic_lifecycle_requires_overwrite_for_existing_outputs(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_jsonl(
        root / "l3" / "cross-thread-candidates" / "candidates.jsonl",
        [_cross_thread_candidate()],
    )

    write_topic_lifecycle_artifacts(root)
    with pytest.raises(TopicLifecycleError, match="--overwrite"):
        write_topic_lifecycle_artifacts(root)

    write_topic_lifecycle_artifacts(root, overwrite=True)


def test_topic_lifecycle_output_is_deterministic(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_jsonl(
        root / "l3" / "cross-thread-candidates" / "candidates.jsonl",
        [_cross_thread_candidate()],
    )
    _write_jsonl(root / "l3" / "review-queue" / "candidates.jsonl", [_review_candidate()])

    write_topic_lifecycle_artifacts(root)
    first = topic_lifecycle_json_path(root).read_text(encoding="utf-8")
    write_topic_lifecycle_artifacts(root, overwrite=True)
    second = topic_lifecycle_json_path(root).read_text(encoding="utf-8")

    assert first == second


def test_topic_lifecycle_does_not_modify_sources_or_policy(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    source_path = root / "l3" / "cross-thread-candidates" / "candidates.jsonl"
    policy_path = root / "project_lexical_rules.yaml"
    _write_jsonl(source_path, [_cross_thread_candidate()])
    policy_path.write_text(
        'schema_version: "0.1"\nowner_scope: "project"\n',
        encoding="utf-8",
    )
    source_before = source_path.read_text(encoding="utf-8")
    policy_before = policy_path.read_text(encoding="utf-8")

    write_topic_lifecycle_artifacts(root)

    assert source_path.read_text(encoding="utf-8") == source_before
    assert policy_path.read_text(encoding="utf-8") == policy_before


def test_topic_lifecycle_cli_writes_artifacts(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_jsonl(
        root / "l3" / "cross-thread-candidates" / "candidates.jsonl",
        [_cross_thread_candidate()],
    )

    main(["analyze", "topic-lifecycle", "--input", str(root)])

    assert topic_lifecycle_json_path(root).exists()
    assert topic_lifecycle_markdown_path(root).exists()
