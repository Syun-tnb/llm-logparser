from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_logparser.cli.cli import main
from llm_logparser.core import analyzer_cross_thread_intent_eval as intent_eval_module
from llm_logparser.core.analyzer_cross_thread_intent_eval import (
    CrossThreadIntentEvalError,
    build_cross_thread_intent_evaluation_rows,
    cross_thread_intent_evaluations_path,
    write_cross_thread_intent_evaluation_artifact,
)
from llm_logparser.core.schema_validation import (
    load_cross_thread_candidate_validator,
    load_cross_thread_intent_evaluation_validator,
)


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _candidate_row(
    *,
    source_conversation_id: str,
    source_topic_id: str,
    source_span_id: str,
    target_conversation_id: str,
    target_topic_id: str,
    target_span_id: str,
    source_excerpt: str,
    target_excerpt: str,
    rank: int,
) -> dict:
    row = {
        "record_type": "cross_thread_candidate",
        "schema_version": "0.1",
        "provider_id": "openai",
        "source_conversation_id": source_conversation_id,
        "target_conversation_id": target_conversation_id,
        "source_topic_id": source_topic_id,
        "target_topic_id": target_topic_id,
        "source_span_id": source_span_id,
        "target_span_id": target_span_id,
        "source_message_ids": [f"{source_span_id}-m1"],
        "target_message_ids": [f"{target_span_id}-m1"],
        "source_excerpt": source_excerpt,
        "target_excerpt": target_excerpt,
        "source_topic_label": None,
        "target_topic_label": None,
        "source_normalized_label": None,
        "target_normalized_label": None,
        "source_raw_label": None,
        "target_raw_label": None,
        "score": 0.5,
        "rank": rank,
        "evidence": {
            "reason_codes": [
                "topic_label_similarity_high",
                "excerpt_similarity_high",
            ],
            "excerpt_similarity": 0.88,
            "topic_label_similarity": 1.0,
            "keyword_overlap_count": 0,
            "shared_keywords": [],
            "normalized_label_match": False,
            "raw_label_match": False,
        },
    }
    errors = list(load_cross_thread_candidate_validator().iter_errors(row))
    assert not errors, errors[0].message if errors else ""
    return row


def _write_candidate_fixture(root: Path) -> Path:
    rows = [
        _candidate_row(
            source_conversation_id="conv-a",
            source_topic_id="topic-a",
            source_span_id="span-a",
            target_conversation_id="conv-b",
            target_topic_id="topic-b",
            target_span_id="span-b",
            source_excerpt="公開完了！おつかれさん！",
            target_excerpt="OK！公開完了！おつかれさん！",
            rank=1,
        ),
        _candidate_row(
            source_conversation_id="conv-a",
            source_topic_id="topic-a",
            source_span_id="span-a",
            target_conversation_id="conv-c",
            target_topic_id="topic-c",
            target_span_id="span-c",
            source_excerpt="公開完了！おつかれさん！",
            target_excerpt="おはよう。レイナ。2025/12/29",
            rank=2,
        ),
        _candidate_row(
            source_conversation_id="conv-d",
            source_topic_id="topic-d",
            source_span_id="span-d",
            target_conversation_id="conv-e",
            target_topic_id="topic-e",
            target_span_id="span-e",
            source_excerpt="現状サマリ: migration checklist と rollback gate の確認が完了。",
            target_excerpt="migration checklist と rollback gate の確認メモを更新した。",
            rank=1,
        ),
    ]
    path = root / "l3" / "cross-thread-candidates" / "candidates.jsonl"
    _write_jsonl(path, rows)
    return path


def _write_single_candidate_fixture(root: Path) -> Path:
    path = root / "l3" / "cross-thread-candidates" / "candidates.jsonl"
    _write_jsonl(
        path,
        [
            _candidate_row(
                source_conversation_id="conv-a",
                source_topic_id="topic-a",
                source_span_id="span-a",
                target_conversation_id="conv-b",
                target_topic_id="topic-b",
                target_span_id="span-b",
                source_excerpt="公開完了！おつかれさん！",
                target_excerpt="OK！公開完了！おつかれさん！",
                rank=1,
            )
        ],
    )
    return path


class _FakeOllamaClient:
    def __init__(self, responses: list[str], calls: list[dict]) -> None:
        self._responses = responses
        self._calls = calls

    def generate_text(
        self,
        model: str,
        prompt: str,
        *,
        response_format: str | None = None,
        options: dict[str, object] | None = None,
    ) -> str:
        self._calls.append(
            {
                "model": model,
                "prompt": prompt,
                "response_format": response_format,
                "options": options,
            }
        )
        if not self._responses:
            raise AssertionError("unexpected extra generate_text call")
        return self._responses.pop(0)


def test_build_cross_thread_intent_evaluation_rows_emits_one_row_per_candidate(
    tmp_path: Path,
    monkeypatch,
):
    root = tmp_path / "artifacts" / "openai"
    _write_candidate_fixture(root)

    calls: list[dict] = []
    responses = [
        json.dumps(
            [
                {
                    "target_index": 1,
                    "same_intent": "yes",
                    "confidence": "high",
                    "reason": "Same release completion event phrased slightly differently.",
                },
                {
                    "target_index": 2,
                    "same_intent": "no",
                    "confidence": "high",
                    "reason": "Greeting text does not continue the release completion event.",
                },
            ]
        ),
        json.dumps(
            [
                {
                    "target_index": 1,
                    "same_intent": "yes",
                    "confidence": "medium",
                    "reason": "Both snippets describe the same migration checklist status update.",
                }
            ]
        ),
    ]
    monkeypatch.setattr(
        intent_eval_module,
        "OllamaClient",
        lambda **_: _FakeOllamaClient(responses, calls),
    )

    rows = build_cross_thread_intent_evaluation_rows(root, model="fake-model")

    assert len(rows) == 3
    assert len(calls) == 2
    first = rows[0]
    assert first["same_intent"] == "yes"
    assert first["confidence"] == "high"
    assert first["candidate_rank"] == 1
    second = rows[1]
    assert second["same_intent"] == "no"
    assert second["target_excerpt"] == "おはよう。レイナ。2025/12/29"
    assert second["candidate_rank"] == 2
    assert calls[0]["response_format"] == "json"
    assert calls[0]["options"]["temperature"] == 0.0


def test_build_cross_thread_intent_evaluation_rows_rejects_malformed_model_output(
    tmp_path: Path,
    monkeypatch,
):
    root = tmp_path / "artifacts" / "openai"
    _write_candidate_fixture(root)

    monkeypatch.setattr(
        intent_eval_module,
        "OllamaClient",
        lambda **_: _FakeOllamaClient(
            ['[{"target_index": 1, "same_intent": "yes", "confidence": "high", "reason": "x"}]'],
            [],
        ),
    )

    with pytest.raises(CrossThreadIntentEvalError):
        build_cross_thread_intent_evaluation_rows(root, model="fake-model")

    debug_path = root / "l4" / "cross-thread-intent-eval" / "debug_raw_responses.log"
    assert debug_path.exists()
    debug_rows = [
        json.loads(line)
        for line in debug_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert debug_rows
    assert debug_rows[0]["source_span_id"] == "span-a"
    assert debug_rows[0]["model"] == "fake-model"
    assert debug_rows[0]["raw_response"].startswith("[")


def test_build_cross_thread_intent_evaluation_rows_accepts_single_object_for_one_target(
    tmp_path: Path,
    monkeypatch,
):
    root = tmp_path / "artifacts" / "openai"
    _write_single_candidate_fixture(root)

    monkeypatch.setattr(
        intent_eval_module,
        "OllamaClient",
        lambda **_: _FakeOllamaClient(
            [
                json.dumps(
                    {
                        "target_index": 1,
                        "same_intent": "yes",
                        "confidence": "high",
                        "reason": "Same release completion event phrased slightly differently.",
                    }
                )
            ],
            [],
        ),
    )

    rows = build_cross_thread_intent_evaluation_rows(root, model="fake-model")

    assert len(rows) == 1
    assert rows[0]["same_intent"] == "yes"
    assert rows[0]["candidate_rank"] == 1


def test_build_cross_thread_intent_evaluation_rows_normalizes_zero_based_indices(
    tmp_path: Path,
    monkeypatch,
):
    root = tmp_path / "artifacts" / "openai"
    _write_candidate_fixture(root)

    monkeypatch.setattr(
        intent_eval_module,
        "OllamaClient",
        lambda **_: _FakeOllamaClient(
            [
                json.dumps(
                    [
                        {
                            "target_index": 0,
                            "same_intent": "yes",
                            "confidence": "high",
                            "reason": "Same release completion event phrased slightly differently.",
                        },
                        {
                            "target_index": 1,
                            "same_intent": "no",
                            "confidence": "high",
                            "reason": "Greeting text does not continue the release completion event.",
                        },
                    ]
                ),
                json.dumps(
                    [
                        {
                            "target_index": 0,
                            "same_intent": "yes",
                            "confidence": "medium",
                            "reason": "Both snippets describe the same migration checklist status update.",
                        }
                    ]
                ),
            ],
            [],
        ),
    )

    rows = build_cross_thread_intent_evaluation_rows(root, model="fake-model")

    assert len(rows) == 3
    assert [row["candidate_rank"] for row in rows] == [1, 2, 1]
    assert [row["same_intent"] for row in rows] == ["yes", "no", "yes"]


def test_build_cross_thread_intent_evaluation_rows_still_rejects_mixed_invalid_indices(
    tmp_path: Path,
    monkeypatch,
):
    root = tmp_path / "artifacts" / "openai"
    _write_candidate_fixture(root)

    monkeypatch.setattr(
        intent_eval_module,
        "OllamaClient",
        lambda **_: _FakeOllamaClient(
            [
                json.dumps(
                    [
                        {
                            "target_index": 0,
                            "same_intent": "yes",
                            "confidence": "high",
                            "reason": "Same release completion event phrased slightly differently.",
                        },
                        {
                            "target_index": 2,
                            "same_intent": "no",
                            "confidence": "high",
                            "reason": "Greeting text does not continue the release completion event.",
                        },
                    ]
                )
            ],
            [],
        ),
    )

    with pytest.raises(CrossThreadIntentEvalError):
        build_cross_thread_intent_evaluation_rows(root, model="fake-model")


def test_cross_thread_intent_evaluation_rows_are_schema_valid(
    tmp_path: Path,
    monkeypatch,
):
    root = tmp_path / "artifacts" / "openai"
    _write_candidate_fixture(root)

    responses = [
        json.dumps(
            [
                {
                    "target_index": 1,
                    "same_intent": "yes",
                    "confidence": "high",
                    "reason": "Same release completion event phrased slightly differently.",
                },
                {
                    "target_index": 2,
                    "same_intent": "no",
                    "confidence": "high",
                    "reason": "Greeting text does not continue the release completion event.",
                },
            ]
        ),
        json.dumps(
            [
                {
                    "target_index": 1,
                    "same_intent": "yes",
                    "confidence": "medium",
                    "reason": "Both snippets describe the same migration checklist status update.",
                }
            ]
        ),
    ]
    monkeypatch.setattr(
        intent_eval_module,
        "OllamaClient",
        lambda **_: _FakeOllamaClient(responses, []),
    )

    result = write_cross_thread_intent_evaluation_artifact(root, model="fake-model")
    rows = [
        json.loads(line)
        for line in result["evaluations_path"].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    validator = load_cross_thread_intent_evaluation_validator()

    assert rows
    for row in rows:
        errors = list(validator.iter_errors(row))
        assert not errors, errors[0].message if errors else ""

    summary = json.loads(result["summary_path"].read_text(encoding="utf-8"))
    assert summary["same_intent_counts"] == {"yes": 2, "no": 1}
    assert summary["confidence_counts"] == {"high": 2, "medium": 1, "low": 0}


def test_cli_analyze_cross_thread_intent_eval_writes_artifact_without_modifying_l3(
    tmp_path: Path,
    monkeypatch,
):
    root = tmp_path / "artifacts" / "openai"
    candidates_path = _write_candidate_fixture(root)
    before = candidates_path.read_text(encoding="utf-8")

    responses = [
        json.dumps(
            [
                {
                    "target_index": 1,
                    "same_intent": "yes",
                    "confidence": "high",
                    "reason": "Same release completion event phrased slightly differently.",
                },
                {
                    "target_index": 2,
                    "same_intent": "no",
                    "confidence": "high",
                    "reason": "Greeting text does not continue the release completion event.",
                },
            ]
        ),
        json.dumps(
            [
                {
                    "target_index": 1,
                    "same_intent": "yes",
                    "confidence": "medium",
                    "reason": "Both snippets describe the same migration checklist status update.",
                }
            ]
        ),
    ]
    monkeypatch.setattr(
        intent_eval_module,
        "OllamaClient",
        lambda **_: _FakeOllamaClient(responses, []),
    )

    main(
        [
            "analyze",
            "cross-thread-intent-eval",
            "--input",
            str(root),
            "--model",
            "fake-model",
        ]
    )

    assert cross_thread_intent_evaluations_path(root).exists()
    assert (root / "l4" / "cross-thread-intent-eval" / "summary.json").exists()
    assert candidates_path.read_text(encoding="utf-8") == before
