from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_logparser.cli.cli import main
from llm_logparser.core.analyzer_policy_effectiveness import (
    PolicyEffectivenessError,
    policy_effectiveness_json_path,
    policy_effectiveness_markdown_path,
    write_policy_effectiveness_artifacts,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _lexical_candidate(
    *,
    candidate_id: str = "lexcand_alpha",
    candidate_type: str = "generic_scoring_token",
    already_active: bool = False,
) -> dict:
    return {
        "record_type": "lexical_rule_candidate",
        "schema_version": "0.1",
        "provider_id": "openai",
        "candidate_id": candidate_id,
        "candidate_type": candidate_type,
        "suggested_scope": "project",
        "suggested_rule_path": "rules.topic_summary.scoring.generic_tokens",
        "value": "alpha",
        "value_kind": "token",
        "normalized_value": "alpha",
        "status": "inactive",
        "activation_state": "requires_review",
        "evidence": {
            "score": 0.8,
            "reason_codes": ["high_conversation_spread", "broad_bundle_spread"],
        },
        "already_active": already_active,
    }


def _cross_thread_candidate(
    *,
    score: float = 0.4,
    continuity_mask: bool = True,
) -> dict:
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
        "evidence": {
            "reason_codes": ["anchor_overlap", "summary_source_heuristic_penalty"],
        },
    }


def test_policy_effectiveness_empty_inputs_warn(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    root.mkdir(parents=True)

    result = write_policy_effectiveness_artifacts(root)

    report = json.loads(policy_effectiveness_json_path(root).read_text(encoding="utf-8"))
    markdown = policy_effectiveness_markdown_path(root).read_text(encoding="utf-8")
    assert result["candidate_count"] == 0
    assert report["candidate_count"] == 0
    assert report["candidate_counts_by_type"] == {}
    assert len(report["warnings"]) == 2
    assert "diagnostics only" in markdown.lower()
    assert "does not activate, accept, reject" in markdown


def test_policy_effectiveness_counts_lexical_candidate_diagnostics(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_jsonl(
        root / "l3" / "lexical-rules" / "candidates.jsonl",
        [
            _lexical_candidate(already_active=True),
            _lexical_candidate(
                candidate_id="lexcand_beta",
                candidate_type="persona_weak_token",
            ),
        ],
    )

    write_policy_effectiveness_artifacts(root)

    report = json.loads(policy_effectiveness_json_path(root).read_text(encoding="utf-8"))
    lexical = report["lexical_candidates"]
    assert report["candidate_counts_by_type"] == {
        "generic_scoring_token": 1,
        "persona_weak_token": 1,
    }
    assert lexical["already_active_policy_candidate_count"] == 1
    assert lexical["reason_code_counts"] == {
        "broad_bundle_spread": 2,
        "high_conversation_spread": 2,
    }
    assert lexical["persona_generic_token_risk_counts"] == {
        "already_active_policy": 1,
        "generic_token_candidate": 1,
        "persona_token_candidate": 1,
        "token_candidate": 2,
    }


def test_policy_effectiveness_counts_cross_thread_candidate_diagnostics(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_jsonl(
        root / "l3" / "cross-thread-candidates" / "candidates.jsonl",
        [
            _cross_thread_candidate(score=0.4, continuity_mask=True),
            _cross_thread_candidate(score=0.7, continuity_mask=False),
        ],
    )

    write_policy_effectiveness_artifacts(root)

    report = json.loads(policy_effectiveness_json_path(root).read_text(encoding="utf-8"))
    cross_thread = report["cross_thread_candidates"]
    assert report["candidate_counts_by_type"] == {"cross_thread_link": 2}
    assert cross_thread["reason_code_counts"] == {
        "anchor_overlap": 2,
        "summary_source_heuristic_penalty": 2,
    }
    assert cross_thread["low_score_candidate_count"] == 1
    assert cross_thread["continuity_mask_candidate_count"] == 1
    assert cross_thread["risk_counts"] == {
        "continuity_mask": 1,
        "low_score": 1,
    }


def test_policy_effectiveness_requires_overwrite_for_existing_outputs(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_jsonl(root / "l3" / "lexical-rules" / "candidates.jsonl", [_lexical_candidate()])

    write_policy_effectiveness_artifacts(root)
    with pytest.raises(PolicyEffectivenessError, match="--overwrite"):
        write_policy_effectiveness_artifacts(root)

    write_policy_effectiveness_artifacts(root, overwrite=True)


def test_policy_effectiveness_does_not_modify_sources_or_policy(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    source_path = root / "l3" / "lexical-rules" / "candidates.jsonl"
    policy_path = root / "project_lexical_rules.yaml"
    _write_jsonl(source_path, [_lexical_candidate()])
    policy_path.write_text(
        'schema_version: "0.1"\nowner_scope: "project"\n',
        encoding="utf-8",
    )
    source_before = source_path.read_text(encoding="utf-8")
    policy_before = policy_path.read_text(encoding="utf-8")

    write_policy_effectiveness_artifacts(root)

    assert source_path.read_text(encoding="utf-8") == source_before
    assert policy_path.read_text(encoding="utf-8") == policy_before


def test_policy_effectiveness_cli_writes_artifacts(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_jsonl(root / "l3" / "lexical-rules" / "candidates.jsonl", [_lexical_candidate()])

    main(["analyze", "policy-effectiveness", "--input", str(root)])

    assert policy_effectiveness_json_path(root).exists()
    assert policy_effectiveness_markdown_path(root).exists()
