from __future__ import annotations

import json
from pathlib import Path

from llm_logparser.cli.cli import main
from llm_logparser.core import analyzer_token_dictionary
from llm_logparser.core.analyzer_token_dictionary import (
    load_token_dictionary_lexical_rules,
    token_bundles_path,
    token_dictionary_path,
    token_dictionary_lexical_rules_path,
    token_dictionary_provenance_path,
    write_token_dictionary_artifacts,
)
from llm_logparser.core.schema_validation import (
    load_token_bundles_validator,
    load_token_dictionary_lexical_rules_validator,
    load_token_dictionary_provenance_validator,
    load_token_dictionary_validator,
)


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _thread_row(conversation_id: str, provider_id: str = "openai") -> dict:
    return {
        "record_type": "thread",
        "schema_version": "1.3",
        "provider_id": provider_id,
        "conversation_id": conversation_id,
    }


def _message_row(
    *,
    conversation_id: str,
    message_id: str,
    role: str,
    text: str,
    ts: int,
) -> dict:
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


def _token_stats_artifact(conversation_id: str) -> dict:
    return {
        "artifact_type": "token_stats",
        "schema_version": "2.0",
        "provider_id": "openai",
        "conversation_id": conversation_id,
        "tokenizer": {
            "family": "gpt_bpe",
            "library": "tiktoken",
            "library_version": "0.0",
            "resolved_model": None,
            "resolved_encoding": "o200k_base",
            "resolution_source": "provider_default",
            "text_policy": "canonical_text_only",
            "special_token_policy": "ordinary_text",
        },
        "summary": {
            "message_count": 2,
            "turn_count": 1,
            "tokens_total": 10,
            "tokens_user": 5,
            "tokens_assistant": 5,
            "avg_tokens_per_message": 5.0,
            "avg_tokens_per_turn": 10.0,
            "empty_text_messages": 0,
        },
        "by_role": {
            "user": {"messages": 1, "tokens": 5},
            "assistant": {"messages": 1, "tokens": 5},
        },
        "messages": [
            {"message_id": f"{conversation_id}-1", "role": "user", "token_count": 5, "text_source": "text"},
            {"message_id": f"{conversation_id}-2", "role": "assistant", "token_count": 5, "text_source": "text"},
        ],
    }


def _topics_artifact() -> dict:
    return {
        "artifact_type": "semantic_topics",
        "schema_version": "2.2",
        "provider_id": "openai",
        "topic_count": 1,
        "generated_at": "2026-04-18T00:00:00Z",
        "source_inputs": ["parsed.jsonl", "window_clusters.jsonl"],
        "provenance": {
            "pipeline_version": "semantic-topics-v1",
            "membership_mode": "span-and-message-v2",
            "label_mode": "structural-only",
            "embedding_model": "test",
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
        "topics": [
            {
                "topic_id": "topic-config",
                "provider_id": "openai",
                "label": "Config rollout retry",
                "summary": None,
                "keywords": ["config.yaml", "deploy", "rollback-plan"],
                "confidence": None,
                "state": "in_progress",
                "state_confidence": 0.8,
                "cluster_ids": ["cluster-a"],
                "conversation_ids": ["conv-a", "conv-b"],
                "span_refs": [
                    {
                        "conversation_id": "conv-a",
                        "span_id": "span-a",
                        "message_ids": ["a-1"],
                        "state": "in_progress",
                        "state_confidence": 0.8,
                        "state_signals": ["user_request"],
                        "window_id": "window-a",
                    }
                ],
                "message_refs": [
                    {"conversation_id": "conv-a", "message_id": "a-1"},
                    {"conversation_id": "conv-b", "message_id": "b-1"},
                ],
                "cluster_count": 1,
                "span_count": 1,
                "window_count": 1,
                "message_count": 2,
                "first_seen": 1000,
                "last_seen": 4000,
                "representative_spans": [
                    {
                        "conversation_id": "conv-a",
                        "span_id": "span-a",
                        "message_ids": ["a-1"],
                        "excerpt": "Retry config.yaml rollout and review rollback-plan.",
                        "state": "in_progress",
                        "state_confidence": 0.8,
                        "state_signals": ["user_request"],
                        "window_id": "window-a",
                    }
                ],
            }
        ],
    }


def _write_provider_fixture(root: Path) -> None:
    _write_jsonl(
        root / "thread-conv-a" / "parsed.jsonl",
        [
            _thread_row("conv-a"),
            _message_row(
                conversation_id="conv-a",
                message_id="a-1",
                role="user",
                text="Retry config.yaml rollout and review rollback-plan after timeout.",
                ts=1000,
            ),
            _message_row(
                conversation_id="conv-a",
                message_id="a-2",
                role="assistant",
                text="Updated config.yaml and reran deploy after timeout.",
                ts=2000,
            ),
        ],
    )
    _write_json(
        root / "thread-conv-a" / "token_stats.json",
        _token_stats_artifact("conv-a"),
    )
    _write_jsonl(
        root / "thread-conv-b" / "parsed.jsonl",
        [
            _thread_row("conv-b"),
            _message_row(
                conversation_id="conv-b",
                message_id="b-1",
                role="user",
                text="Resume config.yaml deploy fix and confirm rollback-plan before release.",
                ts=4000,
            ),
            _message_row(
                conversation_id="conv-b",
                message_id="b-2",
                role="assistant",
                text="Deploy fix completed after config.yaml review.",
                ts=5000,
            ),
        ],
    )
    _write_json(
        root / "thread-conv-b" / "token_stats.json",
        _token_stats_artifact("conv-b"),
    )
    _write_json(root / "l3" / "semantic-topics" / "topics.json", _topics_artifact())


def test_write_token_dictionary_artifacts_writes_schema_valid_outputs(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_provider_fixture(root)

    result = write_token_dictionary_artifacts(root)

    assert result["token_count"] is not None
    assert token_dictionary_path(root).exists()
    assert token_bundles_path(root).exists()
    assert token_dictionary_provenance_path(root).exists()
    assert token_dictionary_lexical_rules_path(root).exists()

    dictionary = json.loads(token_dictionary_path(root).read_text(encoding="utf-8"))
    bundles = json.loads(token_bundles_path(root).read_text(encoding="utf-8"))
    provenance = json.loads(token_dictionary_provenance_path(root).read_text(encoding="utf-8"))
    lexical_rules = json.loads(token_dictionary_lexical_rules_path(root).read_text(encoding="utf-8"))

    assert list(load_token_dictionary_validator().iter_errors(dictionary)) == []
    assert list(load_token_bundles_validator().iter_errors(bundles)) == []
    assert list(load_token_dictionary_provenance_validator().iter_errors(provenance)) == []
    assert list(load_token_dictionary_lexical_rules_validator().iter_errors(lexical_rules)) == []

    config_token = next(row for row in dictionary["tokens"] if row["token"] == "config.yaml")
    assert config_token["count"] >= 4
    assert "topic-config" in config_token["topics"]
    assert "deploy" in config_token["cooccurrence"]
    assert dictionary["source_inputs"] == ["parsed.jsonl", "token_stats.json", "topics.json"]
    assert any("config.yaml" in bundle["tokens"] for bundle in bundles["bundles"])
    assert "reflective_tokens" in lexical_rules["seeded_rules"]
    loaded_rules = load_token_dictionary_lexical_rules(root)
    assert "memory" in loaded_rules.reflective_tokens
def test_token_dictionary_accept_token_filters_universal_noise():
    assert not analyzer_token_dictionary._accept_token("---")
    assert not analyzer_token_dictionary._accept_token("...")
    assert not analyzer_token_dictionary._accept_token("1.")
    assert not analyzer_token_dictionary._accept_token("quot")
    assert not analyzer_token_dictionary._accept_token("!")


def test_token_dictionary_accept_token_keeps_meaningful_tokens():
    assert analyzer_token_dictionary._accept_token("ai")
    assert analyzer_token_dictionary._accept_token("note")
    assert analyzer_token_dictionary._accept_token("openai")
    assert analyzer_token_dictionary._accept_token("config.yaml")


def test_token_dictionary_accept_token_uses_minimal_soft_stopwords():
    assert not analyzer_token_dictionary._accept_token("the")
    assert not analyzer_token_dictionary._accept_token("and")
    assert not analyzer_token_dictionary._accept_token("でも")
    assert not analyzer_token_dictionary._accept_token("って")
    assert not analyzer_token_dictionary._accept_token("to")
    assert not analyzer_token_dictionary._accept_token("or")
    assert analyzer_token_dictionary._accept_token("chatgpt")
    assert analyzer_token_dictionary._accept_token("note")


def test_token_dictionary_overdistributed_filter_suppresses_generic_spread_tokens():
    assert analyzer_token_dictionary._is_overdistributed_token(
        "also",
        conversation_count=4,
        topic_count=4,
        total_conversation_count=5,
        total_topic_count=5,
    )
    assert not analyzer_token_dictionary._is_overdistributed_token(
        "openai",
        conversation_count=5,
        topic_count=5,
        total_conversation_count=5,
        total_topic_count=5,
    )


def test_write_token_dictionary_artifacts_filters_noise_before_aggregation(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_provider_fixture(root)
    _write_jsonl(
        root / "thread-noise" / "parsed.jsonl",
        [
            _thread_row("conv-noise"),
            _message_row(
                conversation_id="conv-noise",
                message_id="n-1",
                role="user",
                text="The and quot config.yaml openai note",
                ts=9000,
            ),
        ],
    )

    write_token_dictionary_artifacts(root)
    dictionary = json.loads(token_dictionary_path(root).read_text(encoding="utf-8"))
    tokens = {row["token"] for row in dictionary["tokens"]}

    assert "the" not in tokens
    assert "and" not in tokens
    assert "quot" not in tokens
    assert "config.yaml" in tokens
    assert "openai" in tokens
    assert "note" in tokens


def test_write_token_dictionary_artifacts_suppresses_overdistributed_generic_tokens_and_cleans_bundles(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    _write_provider_fixture(root)
    for index in range(1, 6):
        conversation_id = f"conv-generic-{index}"
        _write_jsonl(
            root / f"thread-{conversation_id}" / "parsed.jsonl",
            [
                _thread_row(conversation_id),
                _message_row(
                    conversation_id=conversation_id,
                    message_id=f"{conversation_id}-1",
                    role="user",
                    text=f"also openai note config.yaml rollout retry {index}",
                    ts=10000 + index,
                ),
            ],
        )

    write_token_dictionary_artifacts(root)
    dictionary = json.loads(token_dictionary_path(root).read_text(encoding="utf-8"))
    bundles = json.loads(token_bundles_path(root).read_text(encoding="utf-8"))

    tokens = {row["token"] for row in dictionary["tokens"]}
    assert "also" not in tokens
    assert "openai" in tokens
    assert "config.yaml" in tokens
    assert all("also" not in bundle["tokens"] for bundle in bundles["bundles"])


def test_write_token_dictionary_artifacts_skip_existing_preserves_existing_outputs(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_provider_fixture(root)
    write_token_dictionary_artifacts(root)
    marker = {"artifact_type": "marker"}
    token_dictionary_path(root).write_text(json.dumps(marker), encoding="utf-8")

    result = write_token_dictionary_artifacts(root, skip_existing=True)

    assert result["skipped"] is True
    assert json.loads(token_dictionary_path(root).read_text(encoding="utf-8")) == marker


def test_cli_analyze_token_dictionary_writes_artifacts(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_provider_fixture(root)

    main(["analyze", "token-dictionary", "--input", str(root)])

    assert token_dictionary_path(root).exists()
    assert token_bundles_path(root).exists()
    assert token_dictionary_provenance_path(root).exists()
    assert token_dictionary_lexical_rules_path(root).exists()
