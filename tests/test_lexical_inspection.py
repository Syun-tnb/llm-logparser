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
    render_lexical_candidate_inspection_text,
    render_lexical_candidate_list_text,
    render_lexical_inspection_json,
    render_observed_token_inspection_text,
    render_observed_token_list_text,
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
            "score": score,
            "reason_codes": ["high_conversation_spread"],
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


def test_lexical_candidate_list_uses_legacy_evidence_score_and_reasons(
    tmp_path: Path,
):
    root = tmp_path / "openai"
    _write_jsonl(
        lexical_rule_candidates_path(root),
        [
            {
                **_candidate_row("candidate_old_low", "oldlow", score=0.1),
                "review": {"recommendation": "consider"},
            },
            {
                **_candidate_row("candidate_old_high", "oldhigh", score=0.9),
                "review": {"recommendation": "consider"},
            },
        ],
    )

    payload = list_lexical_candidates(root)
    inspected = inspect_lexical_candidate(root, candidate_id="candidate_old_high")

    assert [row["candidate_id"] for row in payload["candidates"]] == [
        "candidate_old_high",
        "candidate_old_low",
    ]
    assert payload["candidates"][0]["score"] == 0.9
    assert payload["candidates"][0]["reason_codes"] == ["high_conversation_spread"]
    assert inspected["review"]["score"] == 0.9
    assert inspected["review"]["reason_codes"] == ["high_conversation_spread"]


def test_lexical_candidate_list_prefers_review_score_over_evidence_score(
    tmp_path: Path,
):
    root = tmp_path / "openai"
    high_review = _candidate_row("candidate_new_high", "newhigh", score=0.1)
    high_review["review"]["score"] = 0.95
    high_review["review"]["reason_codes"] = ["review_reason"]
    low_review = _candidate_row("candidate_old_high", "oldhigh", score=0.9)
    _write_jsonl(lexical_rule_candidates_path(root), [low_review, high_review])

    payload = list_lexical_candidates(root)

    assert payload["candidates"][0]["candidate_id"] == "candidate_new_high"
    assert payload["candidates"][0]["score"] == 0.95
    assert payload["candidates"][0]["reason_codes"] == ["review_reason"]


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


def test_observed_token_list_escapes_markdown_table_cells():
    payload = {
        "provider_id": "openai",
        "token_count": 1,
        "returned_count": 1,
        "observed_tokens_source": "observed_tokens",
        "tokens": [
            {
                "token": "foo|bar\nbaz",
                "count": 1,
                "conversation_count": 1,
                "topic_count": 0,
            }
        ],
    }

    rendered = render_observed_token_list_text(payload)

    assert "foo\\|bar<br>baz" in rendered
    assert "foo|bar\nbaz" not in rendered


def test_observed_token_inspect_escapes_bundle_and_cooccurrence_text():
    payload = {
        "token": "DALL|E",
        "normalized": "dall|e",
        "count": 3,
        "conversation_count": 2,
        "topic_count": 1,
        "observed_tokens_source": "observed_tokens",
        "cooccurrence": ["pipe|token", "multi\nline"],
        "bundle_evidence": [
            {
                "bundle_id": "bundle|001",
                "tokens": ["DALL|E", "multi\nbundle"],
                "weight": 0.5,
            }
        ],
        "provenance_summary": {
            "source_inputs": ["parsed|jsonl"],
            "created_at": "2026-05-09\n00:00",
        },
    }

    rendered = render_observed_token_inspection_text(payload)

    assert "DALL\\|E" in rendered
    assert "pipe\\|token" in rendered
    assert "multi<br>line" in rendered
    assert "bundle\\|001" in rendered
    assert "multi<br>bundle" in rendered
    assert "parsed\\|jsonl" in rendered


def test_lexical_candidate_list_escapes_phrase_table_cells():
    payload = {
        "provider_id": "openai",
        "candidate_count": 1,
        "returned_count": 1,
        "candidates": [
            {
                "candidate_id": "candidate|x",
                "candidate_type": "generic_scoring_token",
                "value": "phrase|with\nnewline",
                "suggested_rule_path": "topic_summary.scoring.generic_tokens",
                "score": 0.5,
                "status": "inactive",
                "already_active": False,
            }
        ],
    }

    rendered = render_lexical_candidate_list_text(payload)

    assert "candidate\\|x" in rendered
    assert "phrase\\|with<br>newline" in rendered
    assert "phrase|with\nnewline" not in rendered


def test_lexical_candidate_inspect_escapes_multiline_diagnostic_snippets():
    payload = {
        "candidate_id": "candidate|x",
        "candidate_type": "generic_scoring_token",
        "value": "phrase|value",
        "normalized_value": "phrase|value",
        "suggested_rule_path": "topic_summary.scoring.generic_tokens",
        "status": "inactive",
        "activation_state": "requires_review",
        "already_active": False,
        "review": {
            "score": 0.5,
            "reason_codes": ["reason|one", "multi\nreason"],
        },
        "evidence": {
            "token_count": 10,
            "topic_summary_total_count": "line|one\nline two",
        },
        "sample_refs": [
            {
                "field": "topic_summary.summary|field",
                "excerpt": "first line\nsecond | line",
            }
        ],
    }

    rendered = render_lexical_candidate_inspection_text(payload)
    rendered_json = render_lexical_inspection_json(payload)

    assert "candidate\\|x" in rendered
    assert "reason\\|one" in rendered
    assert "multi<br>reason" in rendered
    assert "line\\|one<br>line two" in rendered
    assert "topic_summary.summary\\|field" in rendered
    assert "first line<br>second \\| line" in rendered
    assert json.loads(rendered_json)["sample_refs"][0]["excerpt"] == (
        "first line\nsecond | line"
    )
