from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_logparser.cli.cli import main
from llm_logparser.core.analyzer_review_candidates import (
    ReviewCandidateError,
    review_queue_candidates_path,
    review_queue_markdown_path,
    review_queue_report_path,
    write_review_candidate_artifacts,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped:
                rows.append(json.loads(stripped))
    return rows


def _lexical_candidate() -> dict:
    return {
        "record_type": "lexical_rule_candidate",
        "schema_version": "0.1",
        "provider_id": "openai",
        "candidate_id": "lexcand_alpha",
        "candidate_type": "generic_scoring_token",
        "suggested_scope": "project",
        "suggested_rule_path": "rules.topic_summary.scoring.generic_tokens",
        "value": "alpha",
        "value_kind": "token",
        "normalized_value": "alpha",
        "status": "inactive",
        "activation_state": "requires_review",
        "source": {
            "method": "token_dictionary_spread_v0",
            "inputs": ["l3/token-dictionary/observed_tokens.json"],
        },
        "evidence": {
            "score": 0.8,
            "conversation_count": 10,
            "document_count": 12,
            "reason_codes": ["high_conversation_spread"],
        },
        "sample_refs": [
            {
                "conversation_id": "conv-a",
                "field": "token_dictionary.token",
            }
        ],
        "already_active": False,
        "review": {
            "recommendation": "consider",
            "notes": None,
        },
    }


def _cross_thread_candidate() -> dict:
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
        "source_message_ids": ["a1", "a2"],
        "target_message_ids": ["b1"],
        "source_excerpt": "source task",
        "target_excerpt": "target task",
        "source_topic_label": "Source",
        "target_topic_label": "Target",
        "score": 0.72,
        "rank": 1,
        "continuity_mask": False,
        "evidence": {
            "reason_codes": ["excerpt_similarity_medium"],
            "excerpt_similarity": 0.7,
            "topic_label_similarity": 0.2,
        },
    }


def test_review_candidates_generates_empty_report_with_warnings(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    root.mkdir(parents=True)

    result = write_review_candidate_artifacts(root)

    assert result["candidate_count"] == 0
    rows = _read_jsonl(review_queue_candidates_path(root))
    report = json.loads(review_queue_report_path(root).read_text(encoding="utf-8"))
    markdown = review_queue_markdown_path(root).read_text(encoding="utf-8")
    assert rows == []
    assert report["candidate_count"] == 0
    assert report["candidate_counts_by_type"] == {}
    assert len(report["warnings"]) == 2
    assert "inactive" in markdown
    assert "does not accept, reject, promote" in markdown


def test_review_candidates_aggregates_lexical_rule_candidates(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    source_path = root / "l3" / "lexical-rules" / "candidates.jsonl"
    _write_jsonl(source_path, [_lexical_candidate()])

    write_review_candidate_artifacts(root)

    rows = _read_jsonl(review_queue_candidates_path(root))
    assert len(rows) == 1
    row = rows[0]
    assert row["record_type"] == "review_candidate"
    assert row["schema_version"] == "0.1"
    assert row["candidate_type"] == "lexical_rule"
    assert row["status"] == "candidate"
    assert row["activation_state"] == "requires_review"
    assert row["source_artifact"] == "l3/lexical-rules/candidates.jsonl"
    assert row["source_candidate_id"] == "lexcand_alpha"
    assert row["source_command"] == "analyze lexical-rule-candidates"
    assert row["provider_id"] == "openai"
    assert row["scope"] == "project"
    assert row["evidence_refs"] == [
        {"conversation_id": "conv-a", "field": "token_dictionary.token"}
    ]
    assert row["proposed_change"]["normalized_value"] == "alpha"
    assert row["review_notes"] is None
    assert row["diagnostics"]["source_payload"]["candidate_id"] == "lexcand_alpha"


def test_review_candidates_aggregates_cross_thread_candidates(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    source_path = root / "l3" / "cross-thread-candidates" / "candidates.jsonl"
    _write_jsonl(source_path, [_cross_thread_candidate()])

    write_review_candidate_artifacts(root)

    rows = _read_jsonl(review_queue_candidates_path(root))
    assert len(rows) == 1
    row = rows[0]
    assert row["candidate_type"] == "cross_thread_link"
    assert row["source_artifact"] == "l3/cross-thread-candidates/candidates.jsonl"
    assert row["source_command"] == "analyze cross-thread-candidates"
    assert row["provider_id"] == "openai"
    assert row["scope"] == "provider"
    assert row["evidence_refs"][0]["conversation_id"] == "conv-a"
    assert row["evidence_refs"][1]["conversation_id"] == "conv-b"
    assert row["proposed_change"]["link_type"] == "cross_thread_candidate"
    assert row["diagnostics"]["source_payload"]["source_span_id"] == "span-a"


def test_review_candidate_ids_are_deterministic_across_repeated_runs(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_jsonl(root / "l3" / "lexical-rules" / "candidates.jsonl", [_lexical_candidate()])
    _write_jsonl(
        root / "l3" / "cross-thread-candidates" / "candidates.jsonl",
        [_cross_thread_candidate()],
    )

    write_review_candidate_artifacts(root)
    first = _read_jsonl(review_queue_candidates_path(root))
    write_review_candidate_artifacts(root, overwrite=True)
    second = _read_jsonl(review_queue_candidates_path(root))

    assert [row["candidate_id"] for row in first] == [
        row["candidate_id"] for row in second
    ]
    assert first == second


def test_review_candidates_requires_overwrite_for_existing_outputs(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_jsonl(root / "l3" / "lexical-rules" / "candidates.jsonl", [_lexical_candidate()])

    write_review_candidate_artifacts(root)
    with pytest.raises(ReviewCandidateError, match="--overwrite"):
        write_review_candidate_artifacts(root)

    write_review_candidate_artifacts(root, overwrite=True)


def test_review_candidates_report_counts_sources_and_types(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_jsonl(root / "l3" / "lexical-rules" / "candidates.jsonl", [_lexical_candidate()])
    _write_jsonl(
        root / "l3" / "cross-thread-candidates" / "candidates.jsonl",
        [_cross_thread_candidate()],
    )

    write_review_candidate_artifacts(root)

    report = json.loads(review_queue_report_path(root).read_text(encoding="utf-8"))
    assert report["candidate_count"] == 2
    assert report["candidate_counts_by_type"] == {
        "cross_thread_link": 1,
        "lexical_rule": 1,
    }
    assert report["candidate_counts_by_source_artifact"] == {
        "l3/cross-thread-candidates/candidates.jsonl": 1,
        "l3/lexical-rules/candidates.jsonl": 1,
    }
    assert report["warnings"] == []
    assert {item["status"] for item in report["source_inputs"]} == {"loaded"}


def test_review_candidates_does_not_modify_sources_or_reviewed_policy(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    source_path = root / "l3" / "lexical-rules" / "candidates.jsonl"
    policy_path = root / "reviewed.yaml"
    _write_jsonl(source_path, [_lexical_candidate()])
    policy_path.write_text(
        'schema_version: "0.1"\nowner_scope: "project"\n',
        encoding="utf-8",
    )
    source_before = source_path.read_text(encoding="utf-8")
    policy_before = policy_path.read_text(encoding="utf-8")

    write_review_candidate_artifacts(root)

    assert source_path.read_text(encoding="utf-8") == source_before
    assert policy_path.read_text(encoding="utf-8") == policy_before


def test_review_candidates_cli_writes_artifacts(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_jsonl(root / "l3" / "lexical-rules" / "candidates.jsonl", [_lexical_candidate()])

    main(["analyze", "review-candidates", "--input", str(root)])

    assert review_queue_candidates_path(root).exists()
    assert review_queue_report_path(root).exists()
    assert review_queue_markdown_path(root).exists()
