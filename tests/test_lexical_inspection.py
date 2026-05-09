from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from llm_logparser.cli.cli import main
from llm_logparser.core.analyzer_lexical_rule_candidates import (
    lexical_rule_candidate_diagnostics_path,
    lexical_rule_candidates_path,
)
from llm_logparser.core.analyzer_token_dictionary import (
    legacy_token_dictionary_path,
    observed_tokens_path,
    token_bundles_path,
    token_dictionary_provenance_path,
)
from llm_logparser.core.lexical_inspection import (
    LexicalInspectionError,
    inspect_lexical_candidate,
    inspect_observed_token,
    list_lexical_candidates,
    list_observed_tokens,
    load_lexical_candidate_artifacts,
    load_observed_token_artifacts,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _token_row(token: str, *, count: int, conversation_count: int) -> dict:
    return {
        "token": token,
        "normalized": token.casefold(),
        "count": count,
        "first_seen": 1,
        "last_seen": 2,
        "conversations": [f"conv-{index}" for index in range(conversation_count)],
        "topics": ["topic-a"],
        "role_hints": {"user": count},
        "cooccurrence": ["investment", "strategy"],
        "conversation_count": conversation_count,
        "topic_count": 1,
    }


def _observed_artifact(tokens: list[dict]) -> dict:
    return {
        "artifact_type": "token_dictionary",
        "schema_version": "0.1",
        "producer_layer": "L3",
        "provider_id": "openai",
        "created_at": "2026-05-09T00:00:00Z",
        "source_inputs": ["parsed.jsonl"],
        "reproducibility_note": "test",
        "token_count": len(tokens),
        "tokens": tokens,
    }


def _bundles_artifact() -> dict:
    return {
        "artifact_type": "token_bundles",
        "schema_version": "0.1",
        "producer_layer": "L3",
        "provider_id": "openai",
        "created_at": "2026-05-09T00:00:00Z",
        "source_inputs": ["parsed.jsonl"],
        "reproducibility_note": "test",
        "bundle_count": 1,
        "bundles": [
            {
                "bundle_id": "bundle_001",
                "tokens": ["DALL-E", "investment"],
                "weight": 0.8,
            }
        ],
    }


def _provenance_artifact() -> dict:
    return {
        "artifact_type": "token_dictionary_provenance",
        "schema_version": "0.1",
        "producer_layer": "L3",
        "provider_id": "openai",
        "created_at": "2026-05-09T00:00:00Z",
        "source_inputs": ["parsed.jsonl"],
        "reproducibility_note": "test",
        "token_count": 2,
        "bundle_count": 1,
    }


def _write_observed_fixture(root: Path, *, legacy: bool = False) -> None:
    tokens = [
        _token_row("DALL-E", count=50, conversation_count=8),
        _token_row("small", count=2, conversation_count=1),
    ]
    _write_json(
        legacy_token_dictionary_path(root) if legacy else observed_tokens_path(root),
        _observed_artifact(tokens),
    )
    _write_json(token_bundles_path(root), _bundles_artifact())
    _write_json(token_dictionary_provenance_path(root), _provenance_artifact())


def _candidate_row(candidate_id: str, value: str, *, score: float) -> dict:
    return {
        "record_type": "lexical_rule_candidate",
        "schema_version": "0.1",
        "provider_id": "openai",
        "candidate_id": candidate_id,
        "candidate_type": "generic_scoring_token",
        "suggested_scope": "project",
        "suggested_rule_path": "topic_summary.scoring.generic_tokens",
        "value": value,
        "value_kind": "token",
        "normalized_value": value.casefold(),
        "status": "inactive",
        "activation_state": "requires_review",
        "source": {
            "method": "token_dictionary_spread_v0",
            "inputs": ["l3/token-dictionary/observed_tokens.json"],
        },
        "evidence": {
            "token_count": 100,
            "document_count": 30,
            "conversation_count": 10,
            "topic_count": 12,
            "bundle_count": 1,
            "topic_summary_total_count": 3,
        },
        "sample_refs": [
            {
                "conversation_id": "conv-a",
                "segment_id": "segment-a",
                "field": "topic_summary.summary",
                "excerpt": "A short sample reference.",
            }
        ],
        "already_active": False,
        "review": {
            "score": score,
            "reason_codes": ["high_conversation_spread"],
        },
    }


def _write_candidate_fixture(root: Path) -> None:
    _write_jsonl(
        lexical_rule_candidates_path(root),
        [
            _candidate_row("candidate_low", "lownoise", score=0.2),
            _candidate_row("candidate_high", "broadnoise", score=0.9),
        ],
    )
    _write_json(
        lexical_rule_candidate_diagnostics_path(root),
        {
            "artifact_type": "lexical_rule_candidates_diagnostics",
            "schema_version": "0.1",
            "provider_id": "openai",
            "candidate_count": 2,
            "candidate_type_counts": {"generic_scoring_token": 2},
            "skipped_counts": {"below_threshold": 4},
            "topic_summaries": {"status": "not_found"},
            "active_policy": {
                "rule_family": "cross_thread",
                "schema_version": "0.1",
                "resolved_locale": "en-US",
                "project_rules": {"status": "not_provided"},
                "user_rules": {"status": "not_provided"},
                "category_counts": {"topic_summary_scoring.generic_tokens": 10},
            },
            "notes": ["Candidates are inactive and require review before use."],
        },
    )


def test_load_observed_token_artifacts_prefers_observed_tokens(tmp_path: Path):
    root = tmp_path / "openai"
    _write_observed_fixture(root)

    artifacts = load_observed_token_artifacts(root)
    payload = list_observed_tokens(root)

    assert artifacts["observed_tokens_source"] == "observed_tokens"
    assert payload["tokens"][0]["token"] == "DALL-E"
    assert payload["provenance_summary"]["source_inputs"] == ["parsed.jsonl"]


def test_load_observed_token_artifacts_falls_back_to_legacy_dictionary(tmp_path: Path):
    root = tmp_path / "openai"
    _write_observed_fixture(root, legacy=True)

    artifacts = load_observed_token_artifacts(root)
    payload = inspect_observed_token(root, token="dall-e")

    assert artifacts["observed_tokens_source"] == "legacy_dictionary"
    assert payload["token"] == "DALL-E"
    assert payload["bundle_evidence"][0]["bundle_id"] == "bundle_001"


def test_lexical_observed_cli_list_and_inspect_json(tmp_path: Path, capsys):
    logging.getLogger("llm_logparser").handlers.clear()
    root = tmp_path / "openai"
    _write_observed_fixture(root)

    main(["lexical", "observed", "list", "--input", str(root), "--limit", "1", "--json"])
    listed = json.loads(capsys.readouterr().out)
    assert listed["returned_count"] == 1
    assert listed["tokens"][0]["token"] == "DALL-E"

    main(
        [
            "lexical",
            "observed",
            "inspect",
            "--input",
            str(root),
            "--token",
            "DALL-E",
            "--json",
        ]
    )
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["artifact_type"] == "observed_token_inspection"
    assert inspected["bundle_evidence"][0]["tokens"] == ["DALL-E", "investment"]


def test_observed_token_missing_artifact_fails(tmp_path: Path):
    root = tmp_path / "openai"
    root.mkdir()

    with pytest.raises(LexicalInspectionError, match="observed token artifact not found"):
        list_observed_tokens(root)


def test_load_lexical_candidate_artifacts_and_list(tmp_path: Path):
    root = tmp_path / "openai"
    _write_candidate_fixture(root)

    artifacts = load_lexical_candidate_artifacts(root)
    payload = list_lexical_candidates(root)

    assert len(artifacts["candidates"]) == 2
    assert payload["candidates"][0]["candidate_id"] == "candidate_high"
    assert payload["diagnostics_summary"]["active_policy"]["category_counts"] == {
        "topic_summary_scoring.generic_tokens": 10
    }
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "full token list" not in serialized


def test_lexical_candidate_cli_list_and_inspect_text(tmp_path: Path, capsys):
    logging.getLogger("llm_logparser").handlers.clear()
    root = tmp_path / "openai"
    _write_candidate_fixture(root)

    main(["lexical", "candidates", "list", "--input", str(root), "--limit", "1"])
    listed = capsys.readouterr().out
    assert "Lexical-rule candidates" in listed
    assert "candidate_high" in listed
    assert "candidate_low" not in listed

    main(
        [
            "lexical",
            "candidates",
            "inspect",
            "--input",
            str(root),
            "--candidate-id",
            "candidate_high",
        ]
    )
    inspected = capsys.readouterr().out
    assert "Lexical-rule candidate inspection" in inspected
    assert "topic_summary.scoring.generic_tokens" in inspected
    assert "high_conversation_spread" in inspected


def test_lexical_candidate_cli_inspect_json(tmp_path: Path, capsys):
    logging.getLogger("llm_logparser").handlers.clear()
    root = tmp_path / "openai"
    _write_candidate_fixture(root)

    main(
        [
            "lexical",
            "candidates",
            "inspect",
            "--input",
            str(root),
            "--candidate-id",
            "candidate_high",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["status"] == "inactive"
    assert payload["activation_state"] == "requires_review"
    assert payload["review"]["score"] == 0.9


def test_lexical_candidate_missing_artifact_fails(tmp_path: Path):
    root = tmp_path / "openai"
    root.mkdir()

    with pytest.raises(LexicalInspectionError, match="lexical-rule candidates not found"):
        list_lexical_candidates(root)


def test_inspect_lexical_candidate_missing_id_fails(tmp_path: Path):
    root = tmp_path / "openai"
    _write_candidate_fixture(root)

    with pytest.raises(LexicalInspectionError, match="lexical-rule candidate not found"):
        inspect_lexical_candidate(root, candidate_id="missing")
