from __future__ import annotations

from llm_logparser.core.analyzer_semantic_preview import (
    PreviewMessage,
    WindowPreviewRecord,
)
from llm_logparser.core.semantic_state import (
    DONE_STATE,
    IN_PROGRESS_STATE,
    UNRESOLVED_STATE,
    aggregate_topic_state,
    classify_span_state,
)
from llm_logparser.core.semantic_state_phrases import (
    load_semantic_state_phrases,
    resolve_state_locale,
)


def _message(message_id: str, role: str, text: str, ts: int) -> PreviewMessage:
    return PreviewMessage(
        message_id=message_id,
        role=role,
        text=text,
        ts=ts,
    )


def _record(
    *,
    conversation_id: str = "conv-a",
    window_id: str = "window-0001",
    messages: list[PreviewMessage],
) -> WindowPreviewRecord:
    return WindowPreviewRecord(
        provider_id="openai",
        conversation_id=conversation_id,
        window_id=window_id,
        message_ids=tuple(message.message_id for message in messages),
        char_count=sum(len(message.text) for message in messages),
        ts_start=messages[0].ts if messages else None,
        ts_end=messages[-1].ts if messages else None,
        messages=tuple(messages),
    )


def test_classify_span_state_explicit_confirmation_is_done():
    record = _record(
        messages=[
            _message("m1", "assistant", "Please rerun the migration.", 1_710_000_000_000),
            _message("m2", "user", "Perfect, thanks. That solved it.", 1_710_000_060_000),
        ]
    )

    result = classify_span_state(record, dataset_max_ts=record.ts_end)

    assert result.state == DONE_STATE
    assert result.state_confidence == 0.92
    assert "A1:explicit_confirmation" in result.state_signals


def test_classify_span_state_trailing_user_question_is_unresolved():
    record = _record(
        messages=[
            _message("m1", "assistant", "I updated the script output.", 1_710_000_000_000),
            _message("m2", "user", "Can you also update the docs?", 1_710_000_060_000),
        ]
    )

    result = classify_span_state(
        record,
        dataset_max_ts=1_710_000_060_000 + 10 * 86400 * 1000,
    )

    assert result.state == UNRESOLVED_STATE
    assert result.state_confidence == 0.85
    assert "B1:trailing_question" in result.state_signals
    assert "C1:ends_with_user" in result.state_signals


def test_classify_span_state_next_step_and_revision_is_in_progress():
    record = _record(
        messages=[
            _message("m1", "assistant", "The baseline patch is in place.", 1_710_000_000_000),
            _message(
                "m2",
                "user",
                "One more thing: next step is revise the docs.",
                1_710_000_060_000,
            ),
        ]
    )

    result = classify_span_state(record, dataset_max_ts=record.ts_end)

    assert result.state == IN_PROGRESS_STATE
    assert result.state_confidence == 0.78
    assert "B2:user_revision" in result.state_signals
    assert "B4:explicit_next_step" in result.state_signals


def test_classify_span_state_structural_fallback_done_for_single_turn_assistant_end():
    record = _record(
        messages=[
            _message("m1", "user", "Add the missing type annotation.", 1_710_000_000_000),
            _message("m2", "assistant", "Done.", 1_710_000_060_000),
        ]
    )

    result = classify_span_state(
        record,
        dataset_max_ts=1_710_000_060_000 + 40 * 86400 * 1000,
    )

    assert result.state == DONE_STATE
    assert result.state_confidence == 0.5
    assert "C2:ends_with_assistant" in result.state_signals
    assert "C3:single_turn" in result.state_signals


def test_classify_span_state_last_message_wins_over_earlier_closure():
    record = _record(
        messages=[
            _message("m1", "user", "Perfect, thanks. That solved it.", 1_710_000_000_000),
            _message("m2", "user", "Can you also add regression tests?", 1_710_000_060_000),
        ]
    )

    result = classify_span_state(
        record,
        dataset_max_ts=1_710_000_060_000 + 10 * 86400 * 1000,
    )

    assert result.state == UNRESOLVED_STATE
    assert result.state_confidence < 0.85
    assert "A1:explicit_confirmation" in result.state_signals
    assert "B1:trailing_question" in result.state_signals


def test_classify_span_state_assistant_follow_up_after_solution_is_in_progress():
    record = _record(
        messages=[
            _message("m1", "user", "Please finish the refactor.", 1_710_000_000_000),
            _message(
                "m2",
                "assistant",
                "All done. Would you like me to also update the docs?",
                1_710_000_060_000,
            ),
        ]
    )

    result = classify_span_state(record, dataset_max_ts=record.ts_end)

    assert result.state == IN_PROGRESS_STATE
    assert result.state_confidence < 0.92
    assert "A2:task_completion_statement" in result.state_signals
    assert "B1:trailing_question" in result.state_signals


def test_aggregate_topic_state_uses_conservative_any_rule():
    done = classify_span_state(
        _record(
            conversation_id="conv-a",
            window_id="window-0001",
            messages=[
                _message("m1", "user", "Ship it.", 1_710_000_000_000),
                _message("m2", "assistant", "All done.", 1_710_000_060_000),
            ],
        ),
        dataset_max_ts=1_710_000_060_000 + 40 * 86400 * 1000,
    )
    unresolved = classify_span_state(
        _record(
            conversation_id="conv-b",
            window_id="window-0001",
            messages=[
                _message("m3", "user", "Can you check the rollout logs?", 1_710_000_120_000),
            ],
        ),
        dataset_max_ts=1_710_000_120_000 + 10 * 86400 * 1000,
    )
    in_progress = classify_span_state(
        _record(
            conversation_id="conv-c",
            window_id="window-0001",
            messages=[
                _message(
                    "m4",
                    "user",
                    "One more thing: can you also revise the docs next?",
                    1_710_000_180_000,
                ),
            ],
        ),
        dataset_max_ts=1_710_000_180_000,
    )

    assert aggregate_topic_state([done, unresolved, in_progress])[0] == IN_PROGRESS_STATE
    assert aggregate_topic_state([done, unresolved])[0] == UNRESOLVED_STATE
    assert aggregate_topic_state([done])[0] == DONE_STATE


def test_recency_modifier_demotes_stale_in_progress_to_unresolved():
    record = _record(
        messages=[
            _message("m1", "assistant", "Next step: update the release notes.", 1_710_000_000_000),
        ]
    )

    result = classify_span_state(
        record,
        dataset_max_ts=1_710_000_000_000 + 45 * 86400 * 1000,
    )

    assert result.state == UNRESOLVED_STATE
    assert result.state_confidence == 0.65
    assert "B4:explicit_next_step" in result.state_signals
    assert "D2:stale" in result.state_signals


def test_recency_modifier_promotes_fresh_unresolved_to_in_progress():
    record = _record(
        messages=[
            _message("m1", "user", "What should we do next?", 1_710_000_000_000),
        ]
    )

    result = classify_span_state(
        record,
        dataset_max_ts=1_710_000_000_000 + 1 * 86400 * 1000,
    )

    assert result.state == IN_PROGRESS_STATE
    assert result.state_confidence == 0.65
    assert "B1:trailing_question" in result.state_signals
    assert "D1:recent_activity" in result.state_signals


def test_strong_explicit_completion_is_not_overridden_by_recency():
    record = _record(
        messages=[
            _message(
                "m1",
                "assistant",
                "The implementation is complete and everything is now in place.",
                1_710_000_000_000,
            ),
        ]
    )

    result = classify_span_state(
        record,
        dataset_max_ts=1_710_000_000_000 + 45 * 86400 * 1000,
    )

    assert result.state == DONE_STATE
    assert result.state_confidence == 0.92
    assert "A2:task_completion_statement" in result.state_signals


def test_classify_span_state_uses_structured_messages_not_rendered_text_splitting():
    record = _record(
        messages=[
            _message(
                "m1",
                "user",
                "Need help?\n\nActually this is just context, not the last turn.",
                1_710_000_000_000,
            ),
            _message("m2", "assistant", "All done.", 1_710_000_060_000),
        ]
    )

    result = classify_span_state(
        record,
        dataset_max_ts=1_710_000_060_000 + 40 * 86400 * 1000,
    )

    assert result.state == DONE_STATE
    assert "B1:trailing_question" not in result.state_signals


def test_state_phrase_loader_resolves_supported_locale_and_falls_back():
    assert resolve_state_locale("ja") == "ja-JP"
    assert resolve_state_locale("fr-FR") == "en-US"

    phrases = load_semantic_state_phrases("fr-FR")
    assert phrases.locale == "en-US"
    assert "thanks, that works" in phrases.closure_user


def test_classify_span_state_uses_selected_state_locale_for_phrase_matching():
    record = _record(
        messages=[
            _message(
                "m1",
                "user",
                "ありがとうございます、それで大丈夫です。",
                1_710_000_060_000,
            ),
        ]
    )

    default_result = classify_span_state(record, dataset_max_ts=record.ts_end)
    japanese_result = classify_span_state(
        record,
        dataset_max_ts=record.ts_end,
        state_locale="ja-JP",
    )

    assert default_result.state == IN_PROGRESS_STATE
    assert japanese_result.state == DONE_STATE
    assert "A1:explicit_confirmation" in japanese_result.state_signals


def test_unknown_state_locale_falls_back_to_english_phrase_table():
    record = _record(
        messages=[
            _message("m1", "assistant", "The implementation is complete.", 1_710_000_060_000),
        ]
    )

    result = classify_span_state(
        record,
        dataset_max_ts=record.ts_end,
        state_locale="fr-FR",
    )

    assert result.state == DONE_STATE
    assert "A2:task_completion_statement" in result.state_signals
