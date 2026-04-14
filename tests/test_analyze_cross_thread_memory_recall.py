from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_logparser.cli.cli import main
from llm_logparser.core.analyzer_cross_thread_memory_recall import (
    CrossThreadMemoryRecallError,
    render_cross_thread_memory_recall,
)
from llm_logparser.core.i18n import set_locale
from llm_logparser.core.schema_validation import (
    load_cross_thread_intent_evaluation_validator,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _message_row(
    *,
    conversation_id: str,
    message_id: str,
    text: str,
    ts: int,
) -> dict:
    return {
        "record_type": "message",
        "provider_id": "openai",
        "conversation_id": conversation_id,
        "message_id": message_id,
        "role": "user",
        "ts": ts,
        "text": text,
    }


def _thread_rows(
    *,
    conversation_id: str,
    messages: list[dict],
) -> list[dict]:
    return [
        {
            "record_type": "thread",
            "provider_id": "openai",
            "conversation_id": conversation_id,
            "message_count": len(messages),
        },
        *messages,
    ]


def _evaluation_row(
    *,
    source_conversation_id: str,
    source_topic_id: str,
    source_span_id: str,
    source_message_ids: list[str],
    source_excerpt: str,
    target_conversation_id: str,
    target_topic_id: str,
    target_span_id: str,
    target_message_ids: list[str],
    target_excerpt: str,
    same_intent: str,
    confidence: str,
    reason: str,
    candidate_rank: int,
    recall_type: str = "continuity",
) -> dict:
    row = {
        "record_type": "cross_thread_intent_evaluation",
        "schema_version": "0.3",
        "provider_id": "openai",
        "source_conversation_id": source_conversation_id,
        "target_conversation_id": target_conversation_id,
        "source_topic_id": source_topic_id,
        "target_topic_id": target_topic_id,
        "source_span_id": source_span_id,
        "target_span_id": target_span_id,
        "source_message_ids": source_message_ids,
        "target_message_ids": target_message_ids,
        "source_excerpt": source_excerpt,
        "target_excerpt": target_excerpt,
        "source_topic_label": None,
        "target_topic_label": None,
        "candidate_score": 0.5,
        "candidate_rank": candidate_rank,
        "candidate_reason_codes": ["excerpt_similarity_high"],
        "candidate_timestamp_delta_ms": None,
        "candidate_volume_gap": None,
        "candidate_temporal_gap_seconds": None,
        "candidate_continuity_mask": False,
        "candidate_dormancy_score": 0.0,
        "candidate_specificity_score": 0.45,
        "candidate_local_context_delta": None,
        "same_intent": same_intent,
        "recall_type": recall_type,
        "confidence": confidence,
        "reason": reason,
    }
    errors = list(load_cross_thread_intent_evaluation_validator().iter_errors(row))
    assert not errors, errors[0].message if errors else ""
    return row


def _write_memory_recall_fixture(root: Path) -> None:
    _write_jsonl(
        root / "thread-conv-a" / "parsed.jsonl",
        _thread_rows(
            conversation_id="conv-a",
            messages=[
                _message_row(
                    conversation_id="conv-a",
                    message_id="a-1",
                    text="公開完了！おつかれさん！",
                    ts=1763876721639,
                ),
            ],
        ),
    )
    _write_jsonl(
        root / "thread-conv-b" / "parsed.jsonl",
        _thread_rows(
            conversation_id="conv-b",
            messages=[
                _message_row(
                    conversation_id="conv-b",
                    message_id="b-1",
                    text="OK！公開完了！おつかれさん！",
                    ts=1769946959884,
                ),
            ],
        ),
    )
    _write_jsonl(
        root / "thread-conv-c" / "parsed.jsonl",
        _thread_rows(
            conversation_id="conv-c",
            messages=[
                _message_row(
                    conversation_id="conv-c",
                    message_id="c-1",
                    text="おはよう。レイナ。2025/12/29",
                    ts=1766996400000,
                ),
            ],
        ),
    )
    _write_jsonl(
        root / "l4" / "cross-thread-intent-eval" / "evaluations.jsonl",
        [
            _evaluation_row(
                source_conversation_id="conv-a",
                source_topic_id="topic-a",
                source_span_id="span-a",
                source_message_ids=["a-1"],
                source_excerpt="公開完了！おつかれさん！",
                target_conversation_id="conv-b",
                target_topic_id="topic-b",
                target_span_id="span-b",
                target_message_ids=["b-1"],
                target_excerpt="OK！公開完了！おつかれさん！",
                same_intent="yes",
                confidence="high",
                reason="Same release completion event phrased slightly differently.",
                candidate_rank=1,
            ),
            _evaluation_row(
                source_conversation_id="conv-a",
                source_topic_id="topic-a",
                source_span_id="span-a",
                source_message_ids=["a-1"],
                source_excerpt="公開完了！おつかれさん！",
                target_conversation_id="conv-c",
                target_topic_id="topic-c",
                target_span_id="span-c",
                target_message_ids=["c-1"],
                target_excerpt="おはよう。レイナ。2025/12/29",
                same_intent="no",
                confidence="high",
                reason="Greeting text does not continue the release completion event.",
                candidate_rank=2,
            ),
        ],
    )


def test_render_cross_thread_memory_recall_filters_and_groups_matches(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_memory_recall_fixture(root)
    set_locale("ja-JP")

    rendered = render_cross_thread_memory_recall(root)

    assert "前にも似た話をしています" in rendered
    assert "### 元の話:" in rendered
    assert "公開完了！おつかれさん！" in rendered
    assert "### 過去の一致候補:" in rendered
    assert "2026/02/01" in rendered
    assert "過去にも公開完了に関するやり取りがあります。" in rendered
    assert "理由: Same release completion event phrased slightly differently." in rendered
    assert "OK！公開完了！おつかれさん！" in rendered
    assert "おはよう。レイナ。2025/12/29" not in rendered


def test_render_cross_thread_memory_recall_handles_no_yes_matches(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    set_locale("ja-JP")
    _write_jsonl(
        root / "thread-conv-a" / "parsed.jsonl",
        _thread_rows(
            conversation_id="conv-a",
            messages=[
                _message_row(
                    conversation_id="conv-a",
                    message_id="a-1",
                    text="公開完了！おつかれさん！",
                    ts=1763876721639,
                ),
            ],
        ),
    )
    _write_jsonl(
        root / "l4" / "cross-thread-intent-eval" / "evaluations.jsonl",
        [
            _evaluation_row(
                source_conversation_id="conv-a",
                source_topic_id="topic-a",
                source_span_id="span-a",
                source_message_ids=["a-1"],
                source_excerpt="公開完了！おつかれさん！",
                target_conversation_id="conv-c",
                target_topic_id="topic-c",
                target_span_id="span-c",
                target_message_ids=["c-1"],
                target_excerpt="おはよう。レイナ。2025/12/29",
                same_intent="no",
                confidence="high",
                reason="Greeting text does not continue the release completion event.",
                candidate_rank=1,
            ),
        ],
    )

    rendered = render_cross_thread_memory_recall(root)

    assert rendered == "過去の類似した話は見つかりませんでした。"


def test_render_cross_thread_memory_recall_uses_locale_strings(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"
    _write_memory_recall_fixture(root)
    set_locale("en-US")

    rendered = render_cross_thread_memory_recall(root)

    assert "You have talked about something similar before" in rendered
    assert "### Source:" in rendered
    assert "### Matches:" in rendered
    assert "Approximate date: 2026/02/01" in rendered
    assert "A similar release-completion exchange exists in the past." in rendered
    assert "Reason: Same release completion event phrased slightly differently." in rendered


def test_render_cross_thread_memory_recall_prefers_canonical_reconstruction(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    set_locale("en-US")
    _write_jsonl(
        root / "thread-conv-a" / "parsed.jsonl",
        _thread_rows(
            conversation_id="conv-a",
            messages=[
                _message_row(
                    conversation_id="conv-a",
                    message_id="a-1",
                    text=json.dumps(
                        {
                            "updates": [
                                {
                                    "pattern": ".*",
                                    "replacement": "Migration checklist rollout gates for production.",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    ),
                    ts=1763876721639,
                ),
            ],
        ),
    )
    _write_jsonl(
        root / "thread-conv-b" / "parsed.jsonl",
        _thread_rows(
            conversation_id="conv-b",
            messages=[
                _message_row(
                    conversation_id="conv-b",
                    message_id="b-1",
                    text=json.dumps(
                        {
                            "summary": "Rollback gate confirmation for the migration checklist.",
                        },
                        ensure_ascii=False,
                    ),
                    ts=1769946959884,
                ),
            ],
        ),
    )
    _write_jsonl(
        root / "l4" / "cross-thread-intent-eval" / "evaluations.jsonl",
        [
            _evaluation_row(
                source_conversation_id="conv-a",
                source_topic_id="topic-a",
                source_span_id="span-a",
                source_message_ids=["a-1"],
                source_excerpt='{"stored":"source preview"}',
                target_conversation_id="conv-b",
                target_topic_id="topic-b",
                target_span_id="span-b",
                target_message_ids=["b-1"],
                target_excerpt='{"stored":"target preview"}',
                same_intent="yes",
                confidence="high",
                reason="Both spans describe the same migration checklist work.",
                candidate_rank=1,
            ),
        ],
    )

    rendered = render_cross_thread_memory_recall(root)

    assert "Migration checklist rollout gates for production." in rendered
    assert "Rollback gate confirmation for the migration checklist." in rendered
    assert '{"stored":"source preview"}' not in rendered
    assert '{"stored":"target preview"}' not in rendered


def test_render_cross_thread_memory_recall_uses_ordered_message_ids_for_reconstruction(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    set_locale("en-US")
    _write_jsonl(
        root / "thread-conv-a" / "parsed.jsonl",
        _thread_rows(
            conversation_id="conv-a",
            messages=[
                _message_row(
                    conversation_id="conv-a",
                    message_id="a-1",
                    text="First note about migration.",
                    ts=1763876721639,
                ),
                _message_row(
                    conversation_id="conv-a",
                    message_id="a-2",
                    text="Second message about rollout.",
                    ts=1763876722640,
                ),
            ],
        ),
    )
    _write_jsonl(
        root / "thread-conv-b" / "parsed.jsonl",
        _thread_rows(
            conversation_id="conv-b",
            messages=[
                _message_row(
                    conversation_id="conv-b",
                    message_id="b-1",
                    text="Target note about rollout continuity.",
                    ts=1769946959884,
                ),
            ],
        ),
    )
    _write_jsonl(
        root / "l4" / "cross-thread-intent-eval" / "evaluations.jsonl",
        [
            _evaluation_row(
                source_conversation_id="conv-a",
                source_topic_id="topic-a",
                source_span_id="span-a",
                source_message_ids=["a-2", "a-1"],
                source_excerpt="stored source excerpt",
                target_conversation_id="conv-b",
                target_topic_id="topic-b",
                target_span_id="span-b",
                target_message_ids=["b-1"],
                target_excerpt="stored target excerpt",
                same_intent="yes",
                confidence="high",
                reason="The rollout discussion continues in the target span.",
                candidate_rank=1,
            ),
        ],
    )

    rendered = render_cross_thread_memory_recall(root)

    assert "Second message about rollout. First note about migration." in rendered


def test_render_cross_thread_memory_recall_falls_back_to_stored_excerpt_when_canonical_unavailable(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    set_locale("en-US")
    _write_jsonl(
        root / "l4" / "cross-thread-intent-eval" / "evaluations.jsonl",
        [
            _evaluation_row(
                source_conversation_id="conv-a",
                source_topic_id="topic-a",
                source_span_id="span-a",
                source_message_ids=["a-1"],
                source_excerpt="Stored source fallback excerpt",
                target_conversation_id="conv-b",
                target_topic_id="topic-b",
                target_span_id="span-b",
                target_message_ids=["b-1"],
                target_excerpt="Stored target fallback excerpt",
                same_intent="yes",
                confidence="high",
                reason="The target continues the same work.",
                candidate_rank=1,
            ),
        ],
    )

    rendered = render_cross_thread_memory_recall(root)

    assert "Stored source fallback excerpt" in rendered
    assert "Stored target fallback excerpt" in rendered


def test_render_cross_thread_memory_recall_omits_reason_when_missing(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "artifacts" / "openai"
    set_locale("en-US")
    _write_jsonl(
        root / "thread-conv-a" / "parsed.jsonl",
        _thread_rows(
            conversation_id="conv-a",
            messages=[
                _message_row(
                    conversation_id="conv-a",
                    message_id="a-1",
                    text="公開完了！おつかれさん！",
                    ts=1763876721639,
                ),
            ],
        ),
    )
    _write_jsonl(
        root / "thread-conv-b" / "parsed.jsonl",
        _thread_rows(
            conversation_id="conv-b",
            messages=[
                _message_row(
                    conversation_id="conv-b",
                    message_id="b-1",
                    text="OK！公開完了！おつかれさん！",
                    ts=1769946959884,
                ),
            ],
        ),
    )
    row = _evaluation_row(
        source_conversation_id="conv-a",
        source_topic_id="topic-a",
        source_span_id="span-a",
        source_message_ids=["a-1"],
        source_excerpt="公開完了！おつかれさん！",
        target_conversation_id="conv-b",
        target_topic_id="topic-b",
        target_span_id="span-b",
        target_message_ids=["b-1"],
        target_excerpt="OK！公開完了！おつかれさん！",
        same_intent="yes",
        confidence="high",
        reason="Placeholder reason.",
        candidate_rank=1,
    )
    row.pop("reason")
    monkeypatch.setattr(
        "llm_logparser.core.analyzer_cross_thread_memory_recall._load_evaluation_rows",
        lambda _input_root: [row],
    )

    rendered = render_cross_thread_memory_recall(root)

    assert "Reason:" not in rendered
    assert "OK！公開完了！おつかれさん！" in rendered


def test_render_cross_thread_memory_recall_deduplicates_symmetric_pairs(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    set_locale("en-US")
    _write_jsonl(
        root / "thread-conv-a" / "parsed.jsonl",
        _thread_rows(
            conversation_id="conv-a",
            messages=[
                _message_row(
                    conversation_id="conv-a",
                    message_id="a-1",
                    text="Older release completion note.",
                    ts=1763876721639,
                ),
            ],
        ),
    )
    _write_jsonl(
        root / "thread-conv-b" / "parsed.jsonl",
        _thread_rows(
            conversation_id="conv-b",
            messages=[
                _message_row(
                    conversation_id="conv-b",
                    message_id="b-1",
                    text="Newer release completion note.",
                    ts=1769946959884,
                ),
            ],
        ),
    )
    _write_jsonl(
        root / "l4" / "cross-thread-intent-eval" / "evaluations.jsonl",
        [
            _evaluation_row(
                source_conversation_id="conv-a",
                source_topic_id="topic-a",
                source_span_id="span-a",
                source_message_ids=["a-1"],
                source_excerpt="older preview",
                target_conversation_id="conv-b",
                target_topic_id="topic-b",
                target_span_id="span-b",
                target_message_ids=["b-1"],
                target_excerpt="newer preview",
                same_intent="yes",
                confidence="high",
                reason="Same release-completion event.",
                candidate_rank=2,
            ),
            _evaluation_row(
                source_conversation_id="conv-b",
                source_topic_id="topic-b",
                source_span_id="span-b",
                source_message_ids=["b-1"],
                source_excerpt="newer preview",
                target_conversation_id="conv-a",
                target_topic_id="topic-a",
                target_span_id="span-a",
                target_message_ids=["a-1"],
                target_excerpt="older preview",
                same_intent="yes",
                confidence="medium",
                reason="Same release-completion event in reverse.",
                candidate_rank=1,
            ),
        ],
    )

    rendered = render_cross_thread_memory_recall(root)

    assert rendered.count("You have talked about something similar before") == 1
    assert rendered.count("* 2025/11/23") == 1


def test_render_cross_thread_memory_recall_orients_newer_to_older(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    set_locale("en-US")
    _write_jsonl(
        root / "thread-conv-a" / "parsed.jsonl",
        _thread_rows(
            conversation_id="conv-a",
            messages=[
                _message_row(
                    conversation_id="conv-a",
                    message_id="a-1",
                    text="Older task note.",
                    ts=1763876721639,
                ),
            ],
        ),
    )
    _write_jsonl(
        root / "thread-conv-b" / "parsed.jsonl",
        _thread_rows(
            conversation_id="conv-b",
            messages=[
                _message_row(
                    conversation_id="conv-b",
                    message_id="b-1",
                    text="Newer task note.",
                    ts=1769946959884,
                ),
            ],
        ),
    )
    _write_jsonl(
        root / "l4" / "cross-thread-intent-eval" / "evaluations.jsonl",
        [
            _evaluation_row(
                source_conversation_id="conv-a",
                source_topic_id="topic-a",
                source_span_id="span-a",
                source_message_ids=["a-1"],
                source_excerpt="older preview",
                target_conversation_id="conv-b",
                target_topic_id="topic-b",
                target_span_id="span-b",
                target_message_ids=["b-1"],
                target_excerpt="newer preview",
                same_intent="yes",
                confidence="high",
                reason="Same task across threads.",
                candidate_rank=1,
            ),
        ],
    )

    rendered = render_cross_thread_memory_recall(root)

    assert "### Source:\nNewer task note." in rendered
    assert "* 2025/11/23" in rendered
    assert "「Older task note.」" in rendered


def test_render_cross_thread_memory_recall_uses_deterministic_tiebreak_for_symmetric_pairs(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    set_locale("en-US")
    _write_jsonl(
        root / "l4" / "cross-thread-intent-eval" / "evaluations.jsonl",
        [
            _evaluation_row(
                source_conversation_id="conv-b",
                source_topic_id="topic-b",
                source_span_id="span-b",
                source_message_ids=["b-1"],
                source_excerpt="Source B",
                target_conversation_id="conv-a",
                target_topic_id="topic-a",
                target_span_id="span-a",
                target_message_ids=["a-1"],
                target_excerpt="Target A",
                same_intent="yes",
                confidence="high",
                reason="Chosen by better candidate score.",
                candidate_rank=2,
            )
            | {"candidate_score": 0.8},
            _evaluation_row(
                source_conversation_id="conv-a",
                source_topic_id="topic-a",
                source_span_id="span-a",
                source_message_ids=["a-1"],
                source_excerpt="Source A",
                target_conversation_id="conv-b",
                target_topic_id="topic-b",
                target_span_id="span-b",
                target_message_ids=["b-1"],
                target_excerpt="Target B",
                same_intent="yes",
                confidence="high",
                reason="Lower-priority duplicate.",
                candidate_rank=1,
            )
            | {"candidate_score": 0.6},
        ],
    )

    rendered = render_cross_thread_memory_recall(root)

    assert "Chosen by better candidate score." in rendered
    assert "Lower-priority duplicate." not in rendered


def test_render_cross_thread_memory_recall_prefers_recurrence_before_continuity(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    set_locale("en-US")
    _write_jsonl(
        root / "thread-conv-a" / "parsed.jsonl",
        _thread_rows(
            conversation_id="conv-a",
            messages=[
                _message_row(
                    conversation_id="conv-a",
                    message_id="a-1",
                    text="Release planning checklist updated.",
                    ts=1769946959884,
                ),
            ],
        ),
    )
    _write_jsonl(
        root / "thread-conv-b" / "parsed.jsonl",
        _thread_rows(
            conversation_id="conv-b",
            messages=[
                _message_row(
                    conversation_id="conv-b",
                    message_id="b-1",
                    text="Older release planning checklist note.",
                    ts=1763876721639,
                ),
            ],
        ),
    )
    _write_jsonl(
        root / "thread-conv-c" / "parsed.jsonl",
        _thread_rows(
            conversation_id="conv-c",
            messages=[
                _message_row(
                    conversation_id="conv-c",
                    message_id="c-1",
                    text="Same-session rollout status summary.",
                    ts=1769850000000,
                ),
            ],
        ),
    )
    _write_jsonl(
        root / "l4" / "cross-thread-intent-eval" / "evaluations.jsonl",
        [
            _evaluation_row(
                source_conversation_id="conv-a",
                source_topic_id="topic-a",
                source_span_id="span-a",
                source_message_ids=["a-1"],
                source_excerpt="source preview",
                target_conversation_id="conv-c",
                target_topic_id="topic-c",
                target_span_id="span-c",
                target_message_ids=["c-1"],
                target_excerpt="continuity preview",
                same_intent="yes",
                confidence="high",
                reason="Near-continuation of the same rollout work.",
                candidate_rank=1,
                recall_type="continuity",
            ),
            _evaluation_row(
                source_conversation_id="conv-a",
                source_topic_id="topic-a",
                source_span_id="span-a",
                source_message_ids=["a-1"],
                source_excerpt="source preview",
                target_conversation_id="conv-b",
                target_topic_id="topic-b",
                target_span_id="span-b",
                target_message_ids=["b-1"],
                target_excerpt="recurrence preview",
                same_intent="yes",
                confidence="high",
                reason="Meaningful return to the same release checklist work.",
                candidate_rank=2,
                recall_type="recurrence",
            ),
        ],
    )

    rendered = render_cross_thread_memory_recall(root)

    assert rendered.index("Meaningful return to the same release checklist work.") < rendered.index(
        "Near-continuation of the same rollout work."
    )


def test_render_cross_thread_memory_recall_preserves_reconstructed_excerpts_after_deduplication(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    set_locale("en-US")
    _write_jsonl(
        root / "thread-conv-a" / "parsed.jsonl",
        _thread_rows(
            conversation_id="conv-a",
            messages=[
                _message_row(
                    conversation_id="conv-a",
                    message_id="a-1",
                    text=json.dumps(
                        {"summary": "Older migration checklist entry."},
                        ensure_ascii=False,
                    ),
                    ts=1763876721639,
                ),
            ],
        ),
    )
    _write_jsonl(
        root / "thread-conv-b" / "parsed.jsonl",
        _thread_rows(
            conversation_id="conv-b",
            messages=[
                _message_row(
                    conversation_id="conv-b",
                    message_id="b-1",
                    text=json.dumps(
                        {"summary": "Newer migration checklist entry."},
                        ensure_ascii=False,
                    ),
                    ts=1769946959884,
                ),
            ],
        ),
    )
    _write_jsonl(
        root / "l4" / "cross-thread-intent-eval" / "evaluations.jsonl",
        [
            _evaluation_row(
                source_conversation_id="conv-a",
                source_topic_id="topic-a",
                source_span_id="span-a",
                source_message_ids=["a-1"],
                source_excerpt='{"stored":"older preview"}',
                target_conversation_id="conv-b",
                target_topic_id="topic-b",
                target_span_id="span-b",
                target_message_ids=["b-1"],
                target_excerpt='{"stored":"newer preview"}',
                same_intent="yes",
                confidence="high",
                reason="Same migration checklist topic.",
                candidate_rank=2,
            ),
            _evaluation_row(
                source_conversation_id="conv-b",
                source_topic_id="topic-b",
                source_span_id="span-b",
                source_message_ids=["b-1"],
                source_excerpt='{"stored":"newer preview"}',
                target_conversation_id="conv-a",
                target_topic_id="topic-a",
                target_span_id="span-a",
                target_message_ids=["a-1"],
                target_excerpt='{"stored":"older preview"}',
                same_intent="yes",
                confidence="medium",
                reason="Same migration checklist topic in reverse.",
                candidate_rank=1,
            ),
        ],
    )

    rendered = render_cross_thread_memory_recall(root)

    assert "Newer migration checklist entry." in rendered
    assert "Older migration checklist entry." in rendered
    assert '{"stored":"newer preview"}' not in rendered
    assert '{"stored":"older preview"}' not in rendered


def test_render_cross_thread_memory_recall_keeps_non_duplicate_rows(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    set_locale("en-US")
    _write_jsonl(
        root / "thread-conv-a" / "parsed.jsonl",
        _thread_rows(
            conversation_id="conv-a",
            messages=[
                _message_row(
                    conversation_id="conv-a",
                    message_id="a-1",
                    text="Newest source note.",
                    ts=1769946959884,
                ),
            ],
        ),
    )
    _write_jsonl(
        root / "thread-conv-b" / "parsed.jsonl",
        _thread_rows(
            conversation_id="conv-b",
            messages=[
                _message_row(
                    conversation_id="conv-b",
                    message_id="b-1",
                    text="Older match one.",
                    ts=1763876721639,
                ),
            ],
        ),
    )
    _write_jsonl(
        root / "thread-conv-c" / "parsed.jsonl",
        _thread_rows(
            conversation_id="conv-c",
            messages=[
                _message_row(
                    conversation_id="conv-c",
                    message_id="c-1",
                    text="Older match two.",
                    ts=1763876722640,
                ),
            ],
        ),
    )
    _write_jsonl(
        root / "l4" / "cross-thread-intent-eval" / "evaluations.jsonl",
        [
            _evaluation_row(
                source_conversation_id="conv-a",
                source_topic_id="topic-a",
                source_span_id="span-a",
                source_message_ids=["a-1"],
                source_excerpt="source preview",
                target_conversation_id="conv-b",
                target_topic_id="topic-b",
                target_span_id="span-b",
                target_message_ids=["b-1"],
                target_excerpt="target b preview",
                same_intent="yes",
                confidence="high",
                reason="First recall pair.",
                candidate_rank=1,
            ),
            _evaluation_row(
                source_conversation_id="conv-a",
                source_topic_id="topic-a",
                source_span_id="span-a",
                source_message_ids=["a-1"],
                source_excerpt="source preview",
                target_conversation_id="conv-c",
                target_topic_id="topic-c",
                target_span_id="span-c",
                target_message_ids=["c-1"],
                target_excerpt="target c preview",
                same_intent="yes",
                confidence="high",
                reason="Second recall pair.",
                candidate_rank=2,
            ),
        ],
    )

    rendered = render_cross_thread_memory_recall(root)

    assert "「Older match one.」" in rendered
    assert "「Older match two.」" in rendered
    assert "First recall pair." in rendered
    assert "Second recall pair." in rendered


def test_render_cross_thread_memory_recall_suppresses_greeting_style_pairs(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    set_locale("en-US")
    _write_jsonl(
        root / "thread-conv-a" / "parsed.jsonl",
        _thread_rows(
            conversation_id="conv-a",
            messages=[
                _message_row(
                    conversation_id="conv-a",
                    message_id="a-1",
                    text="おはよう。レイナ。2025/12/29",
                    ts=1763876721639,
                ),
            ],
        ),
    )
    _write_jsonl(
        root / "thread-conv-b" / "parsed.jsonl",
        _thread_rows(
            conversation_id="conv-b",
            messages=[
                _message_row(
                    conversation_id="conv-b",
                    message_id="b-1",
                    text="おはよう。レイナ。2025/12/30",
                    ts=1769946959884,
                ),
            ],
        ),
    )
    _write_jsonl(
        root / "l4" / "cross-thread-intent-eval" / "evaluations.jsonl",
        [
            _evaluation_row(
                source_conversation_id="conv-a",
                source_topic_id="topic-a",
                source_span_id="span-a",
                source_message_ids=["a-1"],
                source_excerpt="stored source preview",
                target_conversation_id="conv-b",
                target_topic_id="topic-b",
                target_span_id="span-b",
                target_message_ids=["b-1"],
                target_excerpt="stored target preview",
                same_intent="yes",
                confidence="high",
                reason="The target continues the same greeting exchange.",
                candidate_rank=1,
            ),
        ],
    )

    rendered = render_cross_thread_memory_recall(root)

    assert rendered == "No similar past conversation was found."


def test_render_cross_thread_memory_recall_keeps_meaningful_short_pairs(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    set_locale("en-US")
    _write_memory_recall_fixture(root)

    rendered = render_cross_thread_memory_recall(root)

    assert "OK！公開完了！おつかれさん！" in rendered
    assert "公開完了！おつかれさん！" in rendered


def test_render_cross_thread_memory_recall_suppression_uses_reconstructed_excerpts(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    set_locale("en-US")
    _write_jsonl(
        root / "thread-conv-a" / "parsed.jsonl",
        _thread_rows(
            conversation_id="conv-a",
            messages=[
                _message_row(
                    conversation_id="conv-a",
                    message_id="a-1",
                    text="hello",
                    ts=1763876721639,
                ),
            ],
        ),
    )
    _write_jsonl(
        root / "thread-conv-b" / "parsed.jsonl",
        _thread_rows(
            conversation_id="conv-b",
            messages=[
                _message_row(
                    conversation_id="conv-b",
                    message_id="b-1",
                    text="good morning",
                    ts=1769946959884,
                ),
            ],
        ),
    )
    _write_jsonl(
        root / "l4" / "cross-thread-intent-eval" / "evaluations.jsonl",
        [
            _evaluation_row(
                source_conversation_id="conv-a",
                source_topic_id="topic-a",
                source_span_id="span-a",
                source_message_ids=["a-1"],
                source_excerpt="Migration checklist rollout gates",
                target_conversation_id="conv-b",
                target_topic_id="topic-b",
                target_span_id="span-b",
                target_message_ids=["b-1"],
                target_excerpt="Rollback gate confirmation",
                same_intent="yes",
                confidence="high",
                reason="The target repeats the same greeting pattern.",
                candidate_rank=1,
            ),
        ],
    )

    rendered = render_cross_thread_memory_recall(root)

    assert rendered == "No similar past conversation was found."


def test_render_cross_thread_memory_recall_is_deterministic_after_suppression(
    tmp_path: Path,
):
    root = tmp_path / "artifacts" / "openai"
    set_locale("en-US")
    _write_jsonl(
        root / "thread-conv-a" / "parsed.jsonl",
        _thread_rows(
            conversation_id="conv-a",
            messages=[
                _message_row(
                    conversation_id="conv-a",
                    message_id="a-1",
                    text="Thanks",
                    ts=1763876721639,
                ),
            ],
        ),
    )
    _write_jsonl(
        root / "thread-conv-b" / "parsed.jsonl",
        _thread_rows(
            conversation_id="conv-b",
            messages=[
                _message_row(
                    conversation_id="conv-b",
                    message_id="b-1",
                    text="Thank you",
                    ts=1769946959884,
                ),
            ],
        ),
    )
    _write_jsonl(
        root / "l4" / "cross-thread-intent-eval" / "evaluations.jsonl",
        [
            _evaluation_row(
                source_conversation_id="conv-a",
                source_topic_id="topic-a",
                source_span_id="span-a",
                source_message_ids=["a-1"],
                source_excerpt="stored source",
                target_conversation_id="conv-b",
                target_topic_id="topic-b",
                target_span_id="span-b",
                target_message_ids=["b-1"],
                target_excerpt="stored target",
                same_intent="yes",
                confidence="high",
                reason="The target repeats the same acknowledgement.",
                candidate_rank=1,
            ),
        ],
    )

    first = render_cross_thread_memory_recall(root)
    second = render_cross_thread_memory_recall(root)

    assert first == second == "No similar past conversation was found."


def test_render_cross_thread_memory_recall_requires_evaluations_artifact(tmp_path: Path):
    root = tmp_path / "artifacts" / "openai"

    with pytest.raises(CrossThreadMemoryRecallError):
        render_cross_thread_memory_recall(root)


def test_cli_analyze_cross_thread_memory_recall_renders_text(tmp_path: Path, capsys):
    root = tmp_path / "artifacts" / "openai"
    _write_memory_recall_fixture(root)
    set_locale("ja-JP")

    main(["analyze", "cross-thread-memory-recall", "--input", str(root)])

    output = capsys.readouterr().out
    assert "前にも似た話をしています" in output
    assert "### 過去の一致候補:" in output
    assert "2026/02/01" in output
