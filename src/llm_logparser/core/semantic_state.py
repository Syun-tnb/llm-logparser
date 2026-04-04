from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .analyzer_common import normalize_analysis_text
from .analyzer_semantic_preview import PreviewMessage, WindowPreviewRecord
from .l1_derivation import ts_to_seconds

DONE_STATE = "done"
IN_PROGRESS_STATE = "in_progress"
UNRESOLVED_STATE = "unresolved"
CANONICAL_SPAN_STATES = frozenset(
    {DONE_STATE, IN_PROGRESS_STATE, UNRESOLVED_STATE}
)

STALE_DEMOTION_DAYS = 30.0
FRESH_PROMOTION_DAYS = 3.0
MIN_NORMALIZED_TAIL_CHARS = 10
SHORT_TEXT_CONFIDENCE_CAP = 0.40
RECENCY_MODIFIER_CONFIDENCE_CAP = 0.65
PLAUSIBLE_EPOCH_SECONDS = 946684800.0
TOOL_HEAVY_CONFIDENCE_CAP = 0.35


def _normalized_phrases(*phrases: str) -> tuple[str, ...]:
    normalized: list[str] = []
    for phrase in phrases:
        folded = normalize_analysis_text(phrase)
        if folded:
            normalized.append(folded)
    return tuple(normalized)


SPAN_CLOSURE_USER_PHRASES = _normalized_phrases(
    "thanks, that works",
    "perfect, thanks",
    "that's exactly what i needed",
    "got it, thanks",
    "looks good",
    "that's correct",
    "yes, that's right",
    "great, thank you",
    "problem solved",
    "resolved now",
    "all set now",
    "that solved it",
    "that fixes it",
    "we're good now",
    "ありがとう、それでいい",
    "問題解決",
)
SPAN_COMPLETION_ASSISTANT_PHRASES = _normalized_phrases(
    "here's the complete",
    "here is the final",
    "the implementation is complete",
    "this should resolve",
    "that completes",
    "all done",
    "i've finished",
    "the changes have been applied",
    "everything is now in place",
    "that should do it",
    "this is ready",
)
SPAN_DECISION_PHRASES = _normalized_phrases(
    "let's go with",
    "the decision is",
    "we'll use",
    "i'll proceed with",
    "agreed",
    "confirmed",
    "approved",
)
SPAN_QUESTION_INDICATORS = _normalized_phrases(
    "what do you think",
    "how should we",
    "can you",
    "could you",
    "would you",
    "what about",
    "any suggestions",
    "is there a way to",
    "what's the best",
)
SPAN_USER_REVISION_PHRASES = _normalized_phrases(
    "let me rephrase",
    "to clarify",
    "i mean",
    "in other words",
    "what i mean is",
    "actually",
    "instead",
    "can you also",
    "one more thing",
    "change that",
    "update that",
    "revise that",
    "rewrite that",
    "fix this",
    "adjust this",
)
SPAN_UNCERTAINTY_PHRASES = _normalized_phrases(
    "i'm not sure",
    "it's unclear",
    "this might not work",
    "there could be issues",
    "i'd need to investigate",
    "this is speculative",
    "i don't have enough context",
)
SPAN_NEXT_STEP_PHRASES = _normalized_phrases(
    "next step",
    "next, we should",
    "after that",
    "the remaining task",
    "still need to",
    "todo",
    "we still need",
    "the next thing to do",
)

SIGNAL_NAMES = {
    "A1": "explicit_confirmation",
    "A2": "task_completion_statement",
    "A3": "decision_statement",
    "B1": "trailing_question",
    "B2": "user_revision",
    "B3": "assistant_hedge",
    "B4": "explicit_next_step",
    "C1": "ends_with_user",
    "C2": "ends_with_assistant",
    "C3": "single_turn",
    "C4": "high_turn_count",
    "D1": "recent_activity",
    "D2": "stale",
}


@dataclass(frozen=True)
class SpanStateResult:
    conversation_id: str
    span_id: str
    window_id: str
    message_ids: tuple[str, ...]
    state: str
    state_confidence: float
    state_signals: tuple[str, ...]


def _matches_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    if not text or not phrases:
        return False
    normalized_text = normalize_analysis_text(text)
    return any(phrase in normalized_text for phrase in phrases)


def _tail_messages(record: WindowPreviewRecord, *, size: int = 3) -> list[PreviewMessage]:
    return list(record.messages[-size:])


def _message_offset(messages: list[PreviewMessage], index: int) -> int:
    return len(messages) - 1 - index


def _latest_role_message_offset(
    messages: list[PreviewMessage],
    *,
    role: str,
    phrases: tuple[str, ...],
) -> int | None:
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.role != role:
            continue
        if _matches_phrase(message.text, phrases):
            return _message_offset(messages, index)
    return None


def _latest_any_message_offset(
    messages: list[PreviewMessage],
    *,
    phrases: tuple[str, ...],
    limit: int | None = None,
) -> int | None:
    search = messages if limit is None else messages[-limit:]
    for index in range(len(search) - 1, -1, -1):
        if _matches_phrase(search[index].text, phrases):
            return _message_offset(search, index)
    return None


def _is_trailing_question(message: PreviewMessage | None) -> bool:
    if message is None:
        return False
    stripped = message.text.strip()
    if not stripped:
        return False
    if stripped.endswith("?"):
        return True
    return _matches_phrase(stripped, SPAN_QUESTION_INDICATORS)


def _record_last_activity_ts(record: WindowPreviewRecord) -> int | None:
    timestamps = [message.ts for message in record.messages if isinstance(message.ts, int)]
    if timestamps:
        return max(timestamps)
    if isinstance(record.ts_end, int):
        return record.ts_end
    if isinstance(record.ts_start, int):
        return record.ts_start
    return None


def semantic_state_dataset_max_timestamp(
    records: Iterable[WindowPreviewRecord],
) -> int | None:
    values = [
        timestamp
        for record in records
        if (timestamp := _record_last_activity_ts(record)) is not None
    ]
    if not values:
        return None
    return max(values)


def _days_since_last_activity(
    *,
    record: WindowPreviewRecord,
    dataset_max_ts: int | None,
) -> float | None:
    last_activity_ts = _record_last_activity_ts(record)
    max_seconds = ts_to_seconds(dataset_max_ts)
    last_seconds = ts_to_seconds(last_activity_ts)
    if max_seconds is None or last_seconds is None:
        return None
    if max_seconds < PLAUSIBLE_EPOCH_SECONDS or last_seconds < PLAUSIBLE_EPOCH_SECONDS:
        return None
    return max(0.0, max_seconds - last_seconds) / 86400.0


def _tool_heavy(record: WindowPreviewRecord) -> bool:
    if not record.messages:
        return False
    non_dialog_roles = sum(
        1 for message in record.messages if message.role not in {"user", "assistant"}
    )
    return non_dialog_roles > (len(record.messages) / 2)


def _short_text(record: WindowPreviewRecord) -> bool:
    tail_text = " ".join(message.text for message in _tail_messages(record))
    return len(normalize_analysis_text(tail_text)) < MIN_NORMALIZED_TAIL_CHARS


def _signal_offsets(
    record: WindowPreviewRecord,
    *,
    dataset_max_ts: int | None,
) -> dict[str, int | None]:
    tail = _tail_messages(record)
    last_message = tail[-1] if tail else None
    last_role = last_message.role if last_message is not None else ""
    days_since = _days_since_last_activity(record=record, dataset_max_ts=dataset_max_ts)
    offsets = {
        "A1": _latest_role_message_offset(
            tail,
            role="user",
            phrases=SPAN_CLOSURE_USER_PHRASES,
        ),
        "A2": _latest_role_message_offset(
            tail,
            role="assistant",
            phrases=SPAN_COMPLETION_ASSISTANT_PHRASES,
        ),
        "A3": _latest_any_message_offset(
            tail,
            phrases=SPAN_DECISION_PHRASES,
        ),
        "B1": 0 if _is_trailing_question(last_message) else None,
        "B2": _latest_role_message_offset(
            tail,
            role="user",
            phrases=SPAN_USER_REVISION_PHRASES,
        ),
        "B3": _latest_role_message_offset(
            tail,
            role="assistant",
            phrases=SPAN_UNCERTAINTY_PHRASES,
        ),
        "B4": _latest_any_message_offset(
            tail,
            phrases=SPAN_NEXT_STEP_PHRASES,
            limit=2,
        ),
        "C1": 0 if last_role == "user" else None,
        "C2": 0 if last_role == "assistant" else None,
        "C3": 0 if record.message_count <= 2 else None,
        "C4": 0 if record.message_count >= 8 else None,
        "D1": 0 if days_since is not None and days_since < FRESH_PROMOTION_DAYS else None,
        "D2": 0 if days_since is not None and days_since > STALE_DEMOTION_DAYS else None,
    }
    return offsets


def _continuation_outranks_closure(offsets: dict[str, int | None]) -> bool:
    closure_offsets = [offsets[key] for key in ("A1", "A2", "A3") if offsets[key] is not None]
    continuation_offsets = [
        offsets[key]
        for key in ("B1", "B2", "B3", "B4")
        if offsets[key] is not None
    ]
    if not continuation_offsets:
        return False
    if not closure_offsets:
        return True
    return min(continuation_offsets) <= min(closure_offsets)


def _matched_signal_labels(offsets: dict[str, int | None]) -> tuple[str, ...]:
    labels = [
        (offset if offset is not None else 99, signal_id, f"{signal_id}:{SIGNAL_NAMES[signal_id]}")
        for signal_id, offset in offsets.items()
        if offset is not None
    ]
    labels.sort()
    return tuple(label for _offset, _signal_id, label in labels)


def _compute_confidence(
    *,
    offsets: dict[str, int | None],
    record: WindowPreviewRecord,
    recency_modifier_applied: bool,
) -> float:
    base = 0.50

    if offsets["A1"] is not None or offsets["A2"] is not None:
        base = 0.92
    elif offsets["B1"] is not None and offsets["C1"] is not None:
        base = 0.85
    elif offsets["B2"] is not None or offsets["B4"] is not None:
        base = 0.78
    elif offsets["A3"] is not None:
        base = 0.72
    elif offsets["B3"] is not None:
        base = 0.65

    closure_count = sum(
        1 for key in ("A1", "A2", "A3") if offsets[key] is not None
    )
    continuation_count = sum(
        1 for key in ("B1", "B2", "B3", "B4") if offsets[key] is not None
    )
    if closure_count > 0 and continuation_count > 0:
        base *= 0.75

    if recency_modifier_applied:
        base = min(base, RECENCY_MODIFIER_CONFIDENCE_CAP)
    if _tool_heavy(record) and continuation_count == 0 and closure_count == 0:
        base = min(base, TOOL_HEAVY_CONFIDENCE_CAP)
    if _short_text(record):
        base = min(base, SHORT_TEXT_CONFIDENCE_CAP)

    return round(min(base, 1.0), 2)


def classify_span_state(
    record: WindowPreviewRecord,
    *,
    dataset_max_ts: int | None = None,
) -> SpanStateResult:
    offsets = _signal_offsets(record, dataset_max_ts=dataset_max_ts)
    continuation_wins = _continuation_outranks_closure(offsets)

    if not continuation_wins and (offsets["A1"] is not None or offsets["A2"] is not None):
        state = DONE_STATE
    elif offsets["B1"] is not None and offsets["C1"] is not None:
        state = UNRESOLVED_STATE
    elif offsets["B2"] is not None or offsets["B4"] is not None:
        state = IN_PROGRESS_STATE
    elif not continuation_wins and offsets["A3"] is not None and offsets["B1"] is None:
        state = DONE_STATE
    elif offsets["B3"] is not None and offsets["C2"] is not None:
        state = IN_PROGRESS_STATE
    elif offsets["B1"] is not None and offsets["C2"] is not None:
        state = IN_PROGRESS_STATE
    elif offsets["C1"] is not None and not any(
        offsets[key] is not None for key in ("A1", "A2", "A3")
    ):
        state = UNRESOLVED_STATE
    elif offsets["C3"] is not None and offsets["C2"] is not None and offsets["B1"] is None:
        state = DONE_STATE
    else:
        state = IN_PROGRESS_STATE

    recency_modifier_applied = False
    if state == IN_PROGRESS_STATE and offsets["D2"] is not None:
        state = UNRESOLVED_STATE
        recency_modifier_applied = True
    elif state == UNRESOLVED_STATE and offsets["D1"] is not None:
        state = IN_PROGRESS_STATE
        recency_modifier_applied = True

    return SpanStateResult(
        conversation_id=record.conversation_id,
        span_id=record.span_id,
        window_id=record.window_id,
        message_ids=record.message_ids,
        state=state,
        state_confidence=_compute_confidence(
            offsets=offsets,
            record=record,
            recency_modifier_applied=recency_modifier_applied,
        ),
        state_signals=_matched_signal_labels(offsets),
    )


def aggregate_topic_state(
    span_results: Iterable[SpanStateResult],
) -> tuple[str | None, float | None]:
    rows = list(span_results)
    if not rows:
        return None, None

    if any(row.state == IN_PROGRESS_STATE for row in rows):
        state = IN_PROGRESS_STATE
    elif any(row.state == UNRESOLVED_STATE for row in rows):
        state = UNRESOLVED_STATE
    else:
        state = DONE_STATE

    confidences = [row.state_confidence for row in rows if row.state == state]
    confidence = round(max(confidences), 2) if confidences else None
    return state, confidence
