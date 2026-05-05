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
    lexical_rule_candidates_path,
    write_lexical_rule_candidate_artifacts,
)
from llm_logparser.core.analyzer_token_dictionary import token_bundles_path, token_dictionary_path


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
) -> Path:
    lines = [
        'schema_version: "0.1"',
        f'owner_scope: "{owner_scope}"',
        "rules:",
        "  topic_summary:",
        "    scoring:",
        "      generic_tokens:",
    ]
    for token in generic_tokens:
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

    with pytest.raises(LexicalRuleCandidateError, match="token dictionary not found"):
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
    assert row["sample_refs"]
    assert diagnostics["candidate_count"] == 1
    assert diagnostics["candidate_type_counts"] == {"generic_scoring_token": 1}
    assert diagnostics["thresholds"]["generic_min_conversation_count"] == 8
    assert diagnostics["topic_summaries"]["status"] == "not_found"
    assert diagnostics["active_policy"]["rule_family"] == "cross_thread"
    serialized = json.dumps(diagnostics, ensure_ascii=False)
    assert "broadnoise" not in serialized
    assert not (root / "l3" / "lexical-rules" / "reviewed.yaml").exists()


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


def test_lexical_rule_candidates_cli_without_input_fails_clearly(capsys):
    logging.getLogger("llm_logparser").handlers.clear()

    with pytest.raises(SystemExit) as exc:
        main(["--non-interactive", "analyze", "lexical-rule-candidates"])

    assert exc.value.code == 2
    output = capsys.readouterr().out
    assert "Missing required options for 'analyze lexical-rule-candidates'" in output
    assert "--input" in output
