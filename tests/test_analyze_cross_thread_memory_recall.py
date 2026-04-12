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
) -> dict:
    row = {
        "record_type": "cross_thread_intent_evaluation",
        "schema_version": "0.1",
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
        "same_intent": same_intent,
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
    assert "Approximate date: 2025/11/23" in rendered
    assert "A similar release-completion exchange exists in the past." in rendered


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
