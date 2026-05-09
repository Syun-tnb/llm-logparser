from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from llm_logparser.cli.cli import main
from llm_logparser.core.analyzer_lexical_rule_candidates import (
    LexicalRuleCandidateError,
    build_lexical_rule_candidate_rows,
    lexical_rule_candidate_diagnostics_path,
    lexical_rule_candidate_review_path,
    lexical_rule_candidates_path,
    write_lexical_rule_candidate_artifacts,
)
from llm_logparser.core.analyzer_token_dictionary import (
    legacy_token_dictionary_path,
    token_bundles_path,
    token_dictionary_path,
)


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped:
                rows.append(json.loads(stripped))
    return rows


def _dictionary_artifact(tokens: list[dict]) -> dict:
    return {
        "artifact_type": "token_dictionary",
        "schema_version": "0.1",
        "producer_layer": "L3",
        "provider_id": "openai",
        "created_at": "2026-05-05T00:00:00Z",
        "source_inputs": ["parsed.jsonl"],
        "reproducibility_note": "test",
        "token_count": len(tokens),
        "tokens": tokens,
    }


def _token_row(
    token: str,
    *,
    count: int,
    conversation_count: int,
    topic_count: int | None = None,
) -> dict:
    conversations = [f"conv-{index:02d}" for index in range(conversation_count)]
    effective_topic_count = topic_count if topic_count is not None else conversation_count
    return {
        "token": token,
        "normalized": token.casefold(),
        "count": count,
        "first_seen": 1,
        "last_seen": 2,
        "conversations": conversations,
        "topics": [f"topic-{index:02d}" for index in range(effective_topic_count)],
        "role_hints": {"user": count},
        "cooccurrence": [],
        "conversation_count": conversation_count,
        "topic_count": effective_topic_count,
    }


def _write_dictionary(root: Path, tokens: list[dict]) -> Path:
    path = token_dictionary_path(root)
    _write_json(path, _dictionary_artifact(tokens))
    return path


def _write_bundles(root: Path) -> Path:
    path = token_bundles_path(root)
    _write_json(
        path,
        {
            "artifact_type": "token_bundles",
            "schema_version": "0.1",
            "producer_layer": "L3",
            "provider_id": "openai",
            "created_at": "2026-05-05T00:00:00Z",
            "source_inputs": ["parsed.jsonl"],
            "reproducibility_note": "test",
            "bundle_count": 1,
            "bundles": [
                {
                    "bundle_id": "bundle_001",
                    "tokens": ["broadnoise", "neighbor"],
                    "weight": 0.7,
                }
            ],
        },
    )
    return path


def _write_topic_summaries(root: Path, thread_id: str, lines: list[dict | str]) -> Path:
    path = root / thread_id / "l3" / "intra-thread-topics" / "topic-summaries.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered: list[str] = []
    for line in lines:
        if isinstance(line, str):
            rendered.append(line)
        else:
            rendered.append(json.dumps(line, ensure_ascii=False))
    path.write_text("\n".join(rendered) + "\n", encoding="utf-8")
    return path


def _write_reviewed_lexical_rules(
    path: Path,
    *,
    owner_scope: str,
    generic_tokens: list[str],
    persona_weak_tokens: list[str] | None = None,
    distinctive_allow_tokens: list[str] | None = None,
) -> Path:
    lines = [
        'schema_version: "0.1"',
        f'owner_scope: "{owner_scope}"',
        "rules:",
        "  topic_summary:",
        "    scoring:",
    ]
    sections = {"generic_tokens": generic_tokens}
    if distinctive_allow_tokens is not None:
        sections["distinctive_allow_tokens"] = distinctive_allow_tokens
    if persona_weak_tokens is not None:
        sections["persona_weak_tokens"] = persona_weak_tokens
    for section, tokens in sections.items():
        if not tokens:
            lines.append(f"      {section}: []")
            continue
        lines.append(f"      {section}:")
        for token in tokens:
            lines.append(f"        - {json.dumps(token)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_lexical_rule_candidates_missing_provider_root_fails(tmp_path: Path):
    with pytest.raises(LexicalRuleCandidateError, match="provider root not found"):
        write_lexical_rule_candidate_artifacts(tmp_path / "missing")


def test_lexical_rule_candidates_missing_dictionary_fails(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    root.mkdir(parents=True)

    with pytest.raises(LexicalRuleCandidateError, match="observed token artifact not found"):
        write_lexical_rule_candidate_artifacts(root)


def test_lexical_rule_candidates_generate_inactive_generic_candidate(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_dictionary(
        root,
        [
            _token_row("broadnoise", count=120, conversation_count=12, topic_count=30),
            _token_row("tiny", count=3, conversation_count=2, topic_count=2),
            _token_row("link", count=200, conversation_count=20, topic_count=40),
        ],
    )
    _write_bundles(root)

    result = write_lexical_rule_candidate_artifacts(root)
    rows = _read_jsonl(result["candidates_path"])
    diagnostics = json.loads(result["diagnostics_path"].read_text(encoding="utf-8"))

    assert [row["normalized_value"] for row in rows] == ["broadnoise"]
    row = rows[0]
    assert row["record_type"] == "lexical_rule_candidate"
    assert row["candidate_type"] == "generic_scoring_token"
    assert row["status"] == "inactive"
    assert row["activation_state"] == "requires_review"
    assert row["already_active"] is False
    assert row["source"]["method"] == "token_dictionary_spread_v0"
    assert row["evidence"]["bundle_count"] == 1
    assert 0.0 < row["evidence"]["score"] < 1.0
    assert set(row["evidence"]["score_components"]) == {
        "conversation_score",
        "document_score",
        "frequency_score",
        "topic_summary_score",
        "shape_score",
        "spread_score",
    }
    assert row["sample_refs"]
    assert diagnostics["candidate_count"] == 1
    assert diagnostics["candidate_type_counts"] == {"generic_scoring_token": 1}
    assert diagnostics["thresholds"]["generic_min_conversation_count"] == 8
    assert diagnostics["topic_summaries"]["status"] == "not_found"
    assert diagnostics["active_policy"]["rule_family"] == "cross_thread"
    serialized = json.dumps(diagnostics, ensure_ascii=False)
    assert "broadnoise" not in serialized
    assert not (root / "l3" / "lexical-rules" / "reviewed.yaml").exists()


def test_lexical_rule_candidates_read_legacy_dictionary_alias(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_json(
        legacy_token_dictionary_path(root),
        _dictionary_artifact(
            [_token_row("broadnoise", count=120, conversation_count=12, topic_count=30)]
        ),
    )

    rows, diagnostics = build_lexical_rule_candidate_rows(root)

    assert [row["normalized_value"] for row in rows] == ["broadnoise"]
    assert diagnostics["generated_from"][0] == "l3/token-dictionary/observed_tokens.json"
    assert "dictionary.json remains readable" in " ".join(diagnostics["notes"])


def test_lexical_rule_candidates_generate_inactive_persona_candidates(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    _write_dictionary(
        root,
        [
            _token_row("シュンさん", count=500, conversation_count=50, topic_count=80),
            _token_row("シグレ", count=300, conversation_count=40, topic_count=60),
            _token_row("reyna", count=300, conversation_count=40, topic_count=60),
            _token_row("broadnoise", count=120, conversation_count=12, topic_count=30),
        ],
    )
    _write_topic_summaries(
        root,
        "thread-a",
        [
            {
                "conversation_id": "conv-a",
                "segment_id": "seg-a",
                "summary": "Reyna and シグレ discuss the same persona scene.",
                "keywords": ["Reyna", "シグレ"],
            }
        ],
    )

    rows, diagnostics = build_lexical_rule_candidate_rows(root)
    by_value = {row["normalized_value"]: row for row in rows}

    assert by_value["シュンさん"]["candidate_type"] == "persona_weak_token"
    assert by_value["シグレ"]["candidate_type"] == "persona_weak_token"
    assert by_value["reyna"]["candidate_type"] == "persona_weak_token"
    assert by_value["broadnoise"]["candidate_type"] == "generic_scoring_token"
    assert by_value["reyna"]["suggested_rule_path"] == (
        "topic_summary.scoring.persona_weak_tokens"
    )
    assert by_value["reyna"]["status"] == "inactive"
    assert by_value["reyna"]["activation_state"] == "requires_review"
    assert "topic_summary_capitalized_name_usage" in by_value["reyna"]["evidence"][
        "reason_codes"
    ]
    assert diagnostics["candidate_type_counts"] == {
        "generic_scoring_token": 1,
        "persona_weak_token": 3,
    }
    assert diagnostics["skipped_counts"]["generic_suppressed_by_persona_candidate"] == 3


def test_lexical_rule_candidates_skip_active_persona_weak_policy(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_dictionary(
        root,
        [
            _token_row("TestPersona", count=120, conversation_count=12, topic_count=30),
            _token_row("OtherPersona", count=120, conversation_count=12, topic_count=30),
        ],
    )
    project_rules = _write_reviewed_lexical_rules(
        tmp_path / "project.yaml",
        owner_scope="project",
        generic_tokens=[],
        persona_weak_tokens=["TestPersona"],
    )

    rows, diagnostics = build_lexical_rule_candidate_rows(
        root,
        project_lexical_rules=project_rules,
    )

    assert [row["normalized_value"] for row in rows] == ["otherpersona"]
    assert rows[0]["candidate_type"] == "persona_weak_token"
    assert diagnostics["skipped_counts"]["already_active_policy"] == 1


def test_lexical_rule_candidates_generate_inactive_distinctive_allow_candidates(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    _write_dictionary(
        root,
        [
            _token_row("L3", count=80, conversation_count=12, topic_count=20),
            _token_row("cross-thread", count=90, conversation_count=10, topic_count=20),
            _token_row("analyzer", count=100, conversation_count=9, topic_count=25),
            _token_row("broadnoise", count=120, conversation_count=12, topic_count=30),
        ],
    )
    _write_topic_summaries(
        root,
        "thread-a",
        [
            {
                "conversation_id": "conv-a",
                "segment_id": "seg-a",
                "title": "L3 cross-thread analyzer design",
                "summary": "The analyzer links cross-thread topic summaries.",
                "keywords": ["L3", "cross-thread", "analyzer"],
            }
        ],
    )

    rows, diagnostics = build_lexical_rule_candidate_rows(root)
    by_value = {row["normalized_value"]: row for row in rows}

    assert by_value["l3"]["candidate_type"] == "distinctive_allow_token"
    assert by_value["cross-thread"]["candidate_type"] == "distinctive_allow_token"
    assert by_value["analyzer"]["candidate_type"] == "distinctive_allow_token"
    assert by_value["broadnoise"]["candidate_type"] == "generic_scoring_token"
    assert by_value["l3"]["suggested_rule_path"] == (
        "topic_summary.scoring.distinctive_allow_tokens"
    )
    assert by_value["l3"]["status"] == "inactive"
    assert by_value["l3"]["activation_state"] == "requires_review"
    assert "distinctive_acronym_or_layer_shape" in by_value["l3"]["evidence"][
        "reason_codes"
    ]
    assert diagnostics["candidate_type_counts"] == {
        "distinctive_allow_token": 3,
        "generic_scoring_token": 1,
    }
    assert diagnostics["skipped_counts"]["generic_suppressed_by_distinctive_allow_candidate"] == 3


def test_lexical_rule_candidates_skip_active_distinctive_allow_policy(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_dictionary(
        root,
        [
            _token_row("L3", count=80, conversation_count=12, topic_count=20),
            _token_row("L4", count=80, conversation_count=12, topic_count=20),
        ],
    )
    project_rules = _write_reviewed_lexical_rules(
        tmp_path / "project.yaml",
        owner_scope="project",
        generic_tokens=[],
        distinctive_allow_tokens=["L3"],
    )

    rows, diagnostics = build_lexical_rule_candidate_rows(
        root,
        project_lexical_rules=project_rules,
    )

    assert [row["normalized_value"] for row in rows] == ["l4"]
    assert rows[0]["candidate_type"] == "distinctive_allow_token"
    assert diagnostics["skipped_counts"]["already_active_policy"] == 1


def test_lexical_rule_candidates_persona_precedence_over_distinctive(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    _write_dictionary(
        root,
        [_token_row("Analyzerさん", count=120, conversation_count=12, topic_count=30)],
    )
    _write_topic_summaries(
        root,
        "thread-a",
        [
            {
                "conversation_id": "conv-a",
                "segment_id": "seg-a",
                "keywords": ["Analyzerさん"],
            }
        ],
    )

    rows, diagnostics = build_lexical_rule_candidate_rows(root)

    assert [row["candidate_type"] for row in rows] == ["persona_weak_token"]
    assert diagnostics["skipped_counts"]["generic_suppressed_by_persona_candidate"] == 1
    assert "generic_suppressed_by_distinctive_allow_candidate" not in diagnostics[
        "skipped_counts"
    ]


def test_lexical_rule_candidates_skip_noisy_token_shapes(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_dictionary(
        root,
        [
            _token_row("http://example.com", count=300, conversation_count=30, topic_count=80),
            _token_row("src/main.py", count=300, conversation_count=30, topic_count=80),
            _token_row("2026-05-05", count=300, conversation_count=30, topic_count=80),
            _token_row("123456", count=300, conversation_count=30, topic_count=80),
            _token_row("abc123def", count=300, conversation_count=30, topic_count=80),
            _token_row(
                "thisisaverylongidentifierliketokenthatshouldskip",
                count=300,
                conversation_count=30,
                topic_count=80,
            ),
            _token_row("!!!", count=300, conversation_count=30, topic_count=80),
            _token_row("broadnoise", count=120, conversation_count=12, topic_count=30),
        ],
    )

    rows, diagnostics = build_lexical_rule_candidate_rows(root)

    assert [row["normalized_value"] for row in rows] == ["broadnoise"]
    skipped = diagnostics["skipped_counts"]
    assert skipped["shape_url_like"] == 1
    assert skipped["shape_path_like"] == 1
    assert skipped["shape_date_like"] == 1
    assert skipped["shape_numeric"] == 1
    assert skipped["shape_identifier_like"] == 1
    assert skipped["shape_too_long"] == 1
    assert skipped["shape_symbol_like"] == 1


def test_lexical_rule_candidates_short_cjk_filtering_is_conservative(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_dictionary(
        root,
        [
            _token_row("語", count=300, conversation_count=30, topic_count=80),
            _token_row("確認", count=120, conversation_count=12, topic_count=30),
        ],
    )

    rows, diagnostics = build_lexical_rule_candidate_rows(root)

    assert [row["normalized_value"] for row in rows] == ["確認"]
    assert diagnostics["skipped_counts"]["shape_too_short"] == 1


def test_lexical_rule_candidate_reason_codes_stay_review_oriented(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_dictionary(
        root,
        [_token_row("broadnoise", count=120, conversation_count=12, topic_count=30)],
    )
    _write_bundles(root)

    rows, _diagnostics = build_lexical_rule_candidate_rows(root)

    assert rows[0]["evidence"]["reason_codes"] == [
        "high_conversation_spread",
        "high_document_spread",
        "high_frequency",
        "broad_corpus_token",
    ]


def test_lexical_rule_candidate_scores_use_normalized_components(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_dictionary(
        root,
        [
            _token_row("hightoken", count=5000, conversation_count=80, topic_count=200),
            _token_row("mediumtoken", count=500, conversation_count=10, topic_count=25),
        ],
    )

    rows, _diagnostics = build_lexical_rule_candidate_rows(root)
    by_token = {row["normalized_value"]: row for row in rows}

    assert by_token["hightoken"]["evidence"]["score"] > by_token["mediumtoken"][
        "evidence"
    ]["score"]
    assert by_token["hightoken"]["evidence"]["score"] < 1.0
    assert by_token["mediumtoken"]["evidence"]["score_components"]["spread_score"] < 1.0


def test_lexical_rule_candidate_score_does_not_saturate_for_realistic_high_counts(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    _write_dictionary(
        root,
        [
            _token_row("styleword", count=5000, conversation_count=245, topic_count=245),
            _token_row("anotherstyle", count=2500, conversation_count=160, topic_count=160),
        ],
    )

    rows, _diagnostics = build_lexical_rule_candidate_rows(root)

    assert {row["normalized_value"] for row in rows} == {"styleword", "anotherstyle"}
    assert {row["evidence"]["score"] for row in rows} != {1.0}
    assert all(row["evidence"]["score"] < 1.0 for row in rows)


def test_lexical_rule_candidate_ids_do_not_depend_on_score_inputs(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_dictionary(
        root,
        [_token_row("stabletoken", count=120, conversation_count=12, topic_count=30)],
    )

    rows_without_evidence, _diagnostics = build_lexical_rule_candidate_rows(root)
    _write_topic_summaries(
        root,
        "thread-a",
        [
            {
                "conversation_id": "conv-a",
                "segment_id": "seg-a",
                "summary": "stabletoken appears as topic-summary evidence.",
            }
        ],
    )
    rows_with_evidence, _diagnostics = build_lexical_rule_candidate_rows(root)

    assert rows_without_evidence[0]["candidate_id"] == rows_with_evidence[0][
        "candidate_id"
    ]
    assert rows_without_evidence[0]["evidence"]["score"] != rows_with_evidence[0][
        "evidence"
    ]["score"]


def test_lexical_rule_candidates_add_topic_summary_evidence(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_dictionary(
        root,
        [_token_row("broadnoise", count=120, conversation_count=12, topic_count=30)],
    )
    _write_topic_summaries(
        root,
        "thread-a",
        [
            {
                "conversation_id": "conv-a",
                "segment_id": "seg-a",
                "title": "Broadnoise review",
                "summary": "The broadnoise token is recurring in generated summaries.",
                "keywords": ["broadnoise", "other"],
                "conclusion_text": "Treat broadnoise as review evidence.",
            },
            {
                "conversation_id": "conv-b",
                "segment_id": "seg-b",
                "title": "Other topic",
                "summary": "No candidate token here.",
                "keywords": ["other"],
                "conclusion_text": None,
            },
        ],
    )

    rows, diagnostics = build_lexical_rule_candidate_rows(root)

    row = rows[0]
    assert row["evidence"]["topic_summary_title_count"] == 1
    assert row["evidence"]["topic_summary_summary_count"] == 1
    assert row["evidence"]["topic_summary_keyword_count"] == 1
    assert row["evidence"]["topic_summary_conclusion_count"] == 1
    assert row["evidence"]["topic_summary_total_count"] == 4
    assert row["sample_refs"][0] == {
        "conversation_id": "conv-a",
        "field": "topic_summary.title",
        "excerpt": "Broadnoise review",
        "segment_id": "seg-a",
    }
    assert diagnostics["topic_summaries"]["status"] == "loaded"
    assert diagnostics["topic_summaries"]["files_found"] == 1
    assert diagnostics["topic_summaries"]["rows_loaded"] == 2
    assert diagnostics["topic_summaries"]["rows_malformed"] == 0
    assert diagnostics["topic_summaries"]["fields_indexed"]["topic_summary.title"] == 2
    assert "thread-*/l3/intra-thread-topics/topic-summaries.jsonl" in diagnostics[
        "generated_from"
    ]


def test_lexical_rule_candidates_topic_summary_sample_refs_are_capped(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    _write_dictionary(
        root,
        [_token_row("broadnoise", count=120, conversation_count=12, topic_count=30)],
    )
    _write_topic_summaries(
        root,
        "thread-a",
        [
            {
                "conversation_id": "conv-a",
                "segment_id": "seg-a",
                "title": "Broadnoise title",
                "summary": "Broadnoise summary",
                "keywords": ["broadnoise"],
                "conclusion_text": "Broadnoise conclusion",
            }
        ],
    )

    rows, _diagnostics = build_lexical_rule_candidate_rows(root, sample_limit=2)

    assert len(rows[0]["sample_refs"]) == 2
    assert [ref["field"] for ref in rows[0]["sample_refs"]] == [
        "topic_summary.title",
        "topic_summary.summary",
    ]


def test_lexical_rule_candidates_malformed_topic_summary_rows_are_counted(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    _write_dictionary(
        root,
        [_token_row("broadnoise", count=120, conversation_count=12, topic_count=30)],
    )
    path = _write_topic_summaries(
        root,
        "thread-a",
        [
            "{not-json",
            [],
            {"conversation_id": "conv-a", "segment_id": "seg-a"},
            {
                "conversation_id": "conv-b",
                "segment_id": "seg-b",
                "summary": "Broadnoise summary",
            },
        ],
    )
    before = path.read_text(encoding="utf-8")

    rows, diagnostics = build_lexical_rule_candidate_rows(root)

    assert rows[0]["evidence"]["topic_summary_summary_count"] == 1
    assert diagnostics["topic_summaries"]["status"] == "loaded"
    assert diagnostics["topic_summaries"]["rows_loaded"] == 1
    assert diagnostics["topic_summaries"]["rows_malformed"] == 3
    assert path.read_text(encoding="utf-8") == before


def test_lexical_rule_candidates_topic_summary_latin_match_is_boundary_aware(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    _write_dictionary(
        root,
        [
            _token_row("art", count=120, conversation_count=12, topic_count=30),
            _token_row("token", count=120, conversation_count=12, topic_count=30),
        ],
    )
    _write_topic_summaries(
        root,
        "thread-a",
        [
            {
                "conversation_id": "conv-a",
                "segment_id": "seg-a",
                "title": "Artifact review",
                "summary": "The token appears as a whole word.",
                "keywords": ["artifact", "token"],
            }
        ],
    )

    rows, _diagnostics = build_lexical_rule_candidate_rows(root)
    by_token = {row["normalized_value"]: row for row in rows}

    assert by_token["art"]["evidence"]["topic_summary_total_count"] == 0
    assert by_token["token"]["evidence"]["topic_summary_total_count"] == 2
    assert by_token["token"]["sample_refs"][0]["field"] == "topic_summary.summary"


def test_lexical_rule_candidates_topic_summary_cjk_keeps_substring_matching(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    _write_dictionary(
        root,
        [_token_row("確認", count=120, conversation_count=12, topic_count=30)],
    )
    _write_topic_summaries(
        root,
        "thread-a",
        [
            {
                "conversation_id": "conv-a",
                "segment_id": "seg-a",
                "summary": "再確認します。",
            }
        ],
    )

    rows, _diagnostics = build_lexical_rule_candidate_rows(root)

    assert rows[0]["evidence"]["topic_summary_summary_count"] == 1


def test_lexical_rule_candidates_topic_summary_mixed_token_uses_substring_fallback(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    _write_dictionary(
        root,
        [_token_row("api確認", count=120, conversation_count=12, topic_count=30)],
    )
    _write_topic_summaries(
        root,
        "thread-a",
        [
            {
                "conversation_id": "conv-a",
                "segment_id": "seg-a",
                "summary": "api確認フローを見直す。",
            }
        ],
    )

    rows, _diagnostics = build_lexical_rule_candidate_rows(root)

    assert rows[0]["evidence"]["topic_summary_summary_count"] == 1


def test_lexical_rule_candidate_ids_and_sorting_are_deterministic(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_dictionary(
        root,
        [
            _token_row("zzztoken", count=80, conversation_count=8, topic_count=25),
            _token_row("aaatoken", count=80, conversation_count=8, topic_count=25),
        ],
    )

    rows_a, _diagnostics_a = build_lexical_rule_candidate_rows(root)
    rows_b, _diagnostics_b = build_lexical_rule_candidate_rows(root)

    assert [row["normalized_value"] for row in rows_a] == ["aaatoken", "zzztoken"]
    assert [row["candidate_id"] for row in rows_a] == [
        row["candidate_id"] for row in rows_b
    ]


def test_lexical_rule_candidates_skip_reviewed_active_policy(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_dictionary(
        root,
        [
            _token_row("projectactive", count=100, conversation_count=10, topic_count=25),
            _token_row("useractive", count=100, conversation_count=10, topic_count=25),
            _token_row("candidate", count=100, conversation_count=10, topic_count=25),
        ],
    )
    project_rules = _write_reviewed_lexical_rules(
        tmp_path / "project.yaml",
        owner_scope="project",
        generic_tokens=["projectactive"],
    )
    user_rules = _write_reviewed_lexical_rules(
        tmp_path / "user.yaml",
        owner_scope="user",
        generic_tokens=["useractive"],
    )

    rows, diagnostics = build_lexical_rule_candidate_rows(
        root,
        project_lexical_rules=project_rules,
        user_lexical_rules=user_rules,
    )

    assert [row["normalized_value"] for row in rows] == ["candidate"]
    assert diagnostics["active_policy"]["project_rules"]["status"] == "loaded"
    assert diagnostics["active_policy"]["user_rules"]["status"] == "loaded"
    assert diagnostics["skipped_counts"]["already_active_policy"] == 2
    serialized = json.dumps(diagnostics, ensure_ascii=False)
    assert "projectactive" not in serialized
    assert "useractive" not in serialized


def test_lexical_rule_candidates_existing_outputs_require_overwrite(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    dictionary_path = _write_dictionary(
        root,
        [_token_row("broadnoise", count=120, conversation_count=12, topic_count=30)],
    )
    before = dictionary_path.read_text(encoding="utf-8")

    write_lexical_rule_candidate_artifacts(root)
    with pytest.raises(LexicalRuleCandidateError, match="use --overwrite"):
        write_lexical_rule_candidate_artifacts(root)

    write_lexical_rule_candidate_artifacts(root, overwrite=True)
    assert dictionary_path.read_text(encoding="utf-8") == before


def test_lexical_rule_candidates_cli_writes_outputs(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_dictionary(
        root,
        [_token_row("broadnoise", count=120, conversation_count=12, topic_count=30)],
    )
    project_rules = _write_reviewed_lexical_rules(
        tmp_path / "project.yaml",
        owner_scope="project",
        generic_tokens=[],
    )
    user_rules = _write_reviewed_lexical_rules(
        tmp_path / "user.yaml",
        owner_scope="user",
        generic_tokens=[],
    )

    main(
        [
            "analyze",
            "lexical-rule-candidates",
            "--input",
            str(root),
            "--project-lexical-rules",
            str(project_rules),
            "--user-lexical-rules",
            str(user_rules),
            "--max-candidates-per-type",
            "10",
            "--sample-limit",
            "2",
        ]
    )

    assert lexical_rule_candidates_path(root).exists()
    assert lexical_rule_candidate_diagnostics_path(root).exists()
    assert lexical_rule_candidate_review_path(root).exists()


def test_lexical_rule_candidates_cli_without_input_fails_clearly(capsys):
    logging.getLogger("llm_logparser").handlers.clear()

    with pytest.raises(SystemExit) as exc:
        main(["--non-interactive", "analyze", "lexical-rule-candidates"])

    assert exc.value.code == 2
    output = capsys.readouterr().out
    assert "Missing required options for 'analyze lexical-rule-candidates'" in output
    assert "--input" in output


def test_lexical_rule_candidates_review_markdown_is_generated(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    dictionary_path = _write_dictionary(
        root,
        [_token_row("broadnoise", count=120, conversation_count=12, topic_count=30)],
    )
    _write_topic_summaries(
        root,
        "thread-a",
        [
            {
                "conversation_id": "conv-a",
                "segment_id": "seg-a",
                "summary": "Broadnoise review evidence.",
                "keywords": ["broadnoise"],
            }
        ],
    )
    before_dictionary = dictionary_path.read_text(encoding="utf-8")

    result = write_lexical_rule_candidate_artifacts(root)

    review_path = result["review_path"]
    review = review_path.read_text(encoding="utf-8")
    assert review_path == lexical_rule_candidate_review_path(root)
    assert "# Lexical Rule Candidates Review" in review
    assert "## Summary" in review
    assert "- total candidates: 1" in review
    assert "## generic_scoring_token" in review
    assert "Do not promote personal names" in review
    assert "topic_summary.scoring.persona_weak_tokens" in review
    assert "### broadnoise" in review
    assert "- score:" in review
    assert "- score_components:" in review
    assert "spread_score:" in review
    assert "topic_summary_total_count: 2" in review
    assert "topic_summary.summary" in review
    assert "Broadnoise review evidence." in review
    assert "```yaml" in review
    assert "topic_summary:" in review
    assert "generic_tokens:" in review
    assert '      - "broadnoise"' in review
    assert not (root / "l3" / "lexical-rules" / "reviewed.yaml").exists()
    assert dictionary_path.read_text(encoding="utf-8") == before_dictionary


def test_lexical_rule_candidates_review_warns_on_name_like_latin_candidate(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    _write_dictionary(
        root,
        [_token_row("TestPersona", count=120, conversation_count=12, topic_count=30)],
    )

    result = write_lexical_rule_candidate_artifacts(root)

    rows = _read_jsonl(result["candidates_path"])
    review = result["review_path"].read_text(encoding="utf-8")
    assert rows[0]["status"] == "inactive"
    assert rows[0]["activation_state"] == "requires_review"
    assert rows[0]["candidate_type"] == "persona_weak_token"
    assert "review_note" not in rows[0]
    assert "alternative_rule" not in rows[0]
    assert "## persona_weak_token" in review
    assert "### testpersona" in review
    assert "persona_weak_tokens:" in review
    assert '      - "testpersona"' in review
    assert not (root / "l3" / "lexical-rules" / "reviewed.yaml").exists()


def test_lexical_rule_candidates_review_warns_on_honorific_candidate(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    _write_dictionary(
        root,
        [_token_row("テストさん", count=120, conversation_count=12, topic_count=30)],
    )

    result = write_lexical_rule_candidate_artifacts(root)

    rows = _read_jsonl(result["candidates_path"])
    review = result["review_path"].read_text(encoding="utf-8")
    assert rows[0]["candidate_type"] == "persona_weak_token"
    assert "### テストさん" in review
    assert "persona_weak_tokens:" in review
    assert '      - "テストさん"' in review


def test_lexical_rule_candidates_review_renders_distinctive_allow_section(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    _write_dictionary(
        root,
        [_token_row("L3", count=80, conversation_count=12, topic_count=20)],
    )

    result = write_lexical_rule_candidate_artifacts(root)

    rows = _read_jsonl(result["candidates_path"])
    review = result["review_path"].read_text(encoding="utf-8")
    assert rows[0]["candidate_type"] == "distinctive_allow_token"
    assert rows[0]["suggested_rule_path"] == (
        "topic_summary.scoring.distinctive_allow_tokens"
    )
    assert "## distinctive_allow_token" in review
    assert "high-value domain/project/topic tokens" in review
    assert "### l3" in review
    assert "distinctive_allow_tokens:" in review
    assert '      - "l3"' in review


def test_lexical_rule_candidate_name_like_detection_has_no_builtin_name_list():
    resource_dir = Path("src/llm_logparser/resources/cross_thread")
    serialized_resources = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(resource_dir.glob("*.yaml"))
    ).casefold()
    forbidden_fixture_names = {"testpersona", "reyna", "shigure", "shizuku"}

    assert all(name not in serialized_resources for name in forbidden_fixture_names)


def test_lexical_rule_candidates_review_ordering_is_deterministic(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_dictionary(
        root,
        [
            _token_row("aaatoken", count=100, conversation_count=10, topic_count=25),
            _token_row("zzztoken", count=100, conversation_count=10, topic_count=25),
        ],
    )
    _write_topic_summaries(
        root,
        "thread-a",
        [
            {
                "conversation_id": "conv-a",
                "segment_id": "seg-a",
                "summary": "zzztoken appears in topic summary evidence.",
            }
        ],
    )

    result = write_lexical_rule_candidate_artifacts(root)

    review = result["review_path"].read_text(encoding="utf-8")
    assert review.index("### zzztoken") < review.index("### aaatoken")


def test_lexical_rule_candidates_review_sample_refs_are_capped(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_dictionary(
        root,
        [_token_row("broadnoise", count=120, conversation_count=12, topic_count=30)],
    )
    _write_topic_summaries(
        root,
        "thread-a",
        [
            {
                "conversation_id": "conv-a",
                "segment_id": "seg-a",
                "title": "Broadnoise title",
                "summary": "Broadnoise summary",
                "keywords": ["broadnoise"],
                "conclusion_text": "Broadnoise conclusion",
            }
        ],
    )

    result = write_lexical_rule_candidate_artifacts(root, sample_limit=2)

    review = result["review_path"].read_text(encoding="utf-8")
    assert "topic_summary.title" in review
    assert "topic_summary.summary" in review
    assert "topic_summary.keywords" not in review
    assert "topic_summary.conclusion_text" not in review
