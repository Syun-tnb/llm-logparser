from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .i18n import _
from .l1_derivation import iter_input_message_records, message_text, ts_to_seconds
from .schema_validation import load_cross_thread_intent_evaluation_validator

ALLOWED_CONFIDENCE = frozenset({"high", "medium"})
_STRUCTURAL_CHARS = frozenset({'{', '}', '[', ']', ':', ',', '"', "\\"})
_CONFIDENCE_RANK = {
    "low": 0,
    "medium": 1,
    "high": 2,
}
_RECALL_TYPE_RANK = {
    "continuity": 0,
    "recurrence": 1,
}
_GREETING_PREFIXES = (
    "goodmorning",
    "goodafternoon",
    "goodevening",
    "hello",
    "hi",
    "hey",
    "おはよう",
    "こんにちは",
    "こんばんは",
    "はじめまして",
)
_ACK_EXACT = frozenset(
    {
        "ok",
        "okay",
        "thanks",
        "thankyou",
        "gotit",
        "noted",
        "sure",
        "了解",
        "承知",
        "はい",
        "うん",
        "ありがとう",
        "ありがとうございます",
    }
)
_STRONG_RECALL_MARKERS = (
    "公開",
    "リリース",
    "現状サマリ",
    "進捗",
    "状況",
    "まとめ",
    "migration",
    "rollback",
    "checklist",
    "release",
    "deploy",
    "deployment",
    "status",
    "summary",
    "task",
    "project",
    "issue",
    "bug",
    "fix",
    "review",
)
_LOW_VALUE_REASON_MARKERS = (
    "greeting",
    "acknowledgement",
    "acknowledgment",
    "hello",
    "good morning",
    "good afternoon",
    "good evening",
    "thanks",
    "thank you",
    "挨拶",
    "ありがとう",
    "了解",
)
_WORD_RE = re.compile(r"[a-z]{4,}|[一-龯ぁ-んァ-ヶー]{2,}", re.IGNORECASE)


class CrossThreadMemoryRecallError(RuntimeError):
    pass


@dataclass(frozen=True)
class _CanonicalMessageRecord:
    text: str
    ts: int | None


@dataclass(frozen=True)
class _PreparedRecallRow:
    row: dict[str, Any]
    source_excerpt: str
    target_excerpt: str
    source_ts: int | None
    target_ts: int | None


def _evaluations_path(input_root: Path) -> Path:
    return input_root / "l4" / "cross-thread-intent-eval" / "evaluations.jsonl"


def _load_evaluation_rows(input_root: Path) -> list[dict[str, Any]]:
    path = _evaluations_path(input_root)
    if not path.exists():
        raise CrossThreadMemoryRecallError(
            f"cross-thread intent evaluation artifact not found: {path}"
        )

    validator = load_cross_thread_intent_evaluation_validator()
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise CrossThreadMemoryRecallError(
                    f"invalid JSON in {path} line {line_number}: {exc.msg}"
                ) from exc
            errors = list(validator.iter_errors(row))
            if errors:
                raise CrossThreadMemoryRecallError(
                    "cross-thread intent evaluation schema validation failed for "
                    f"{path} line {line_number}: {errors[0].message}"
                )
            rows.append(row)
    return rows


def _message_record_index(
    input_root: Path,
) -> dict[tuple[str, str], _CanonicalMessageRecord]:
    index: dict[tuple[str, str], _CanonicalMessageRecord] = {}
    try:
        for row in iter_input_message_records(input_root):
            conversation_id = row.get("conversation_id")
            message_id = row.get("message_id")
            if not isinstance(conversation_id, str) or not isinstance(message_id, str):
                continue
            ts = row.get("ts") if isinstance(row.get("ts"), int) else None
            index[(conversation_id, message_id)] = _CanonicalMessageRecord(
                text=message_text(row),
                ts=ts,
            )
    except (FileNotFoundError, ValueError):
        # Recall rendering remains usable even when canonical files are missing
        # or unreadable; callers fall back to stored excerpts and unknown dates.
        return {}
    return index


def _first_timestamp(
    message_index: dict[tuple[str, str], _CanonicalMessageRecord],
    conversation_id: str,
    message_ids: list[str],
) -> int | None:
    timestamps = [
        message_index[(conversation_id, message_id)].ts
        for message_id in message_ids
        if (
            (conversation_id, message_id) in message_index
            and message_index[(conversation_id, message_id)].ts is not None
        )
    ]
    if not timestamps:
        return None
    return min(timestamps)


def _format_date(ts: int | None) -> str:
    seconds = ts_to_seconds(ts)
    if seconds is None:
        return _("memory_recall.date_unknown")
    return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y/%m/%d")


def _truncate(text: str, *, max_chars: int = 72) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3] + "..."


def _collect_json_string_values(payload: Any) -> list[str]:
    values: list[str] = []
    if isinstance(payload, str):
        values.append(payload)
        return values
    if isinstance(payload, list):
        for item in payload:
            values.extend(_collect_json_string_values(item))
        return values
    if isinstance(payload, dict):
        for value in payload.values():
            values.extend(_collect_json_string_values(value))
    return values


def _looks_structured(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith("{") or stripped.startswith("[")


def _textual_char_count(text: str) -> int:
    return sum(
        1
        for char in text
        if char.isalnum()
        or "\u3040" <= char <= "\u30ff"
        or "\u3400" <= char <= "\u9fff"
    )


def _structural_char_count(text: str) -> int:
    return sum(1 for char in text if char in _STRUCTURAL_CHARS)


def _compact_candidate(text: str) -> str:
    return " ".join(text.split()).strip()


def _excerpt_candidates(text: str) -> list[str]:
    compact = _compact_candidate(text)
    if not compact:
        return []

    candidates: list[str] = []
    if _looks_structured(compact):
        try:
            payload = json.loads(compact)
        except json.JSONDecodeError:
            payload = None
        if payload is not None:
            for value in _collect_json_string_values(payload):
                candidate = _compact_candidate(value)
                if not candidate:
                    continue
                if _textual_char_count(candidate) < 8:
                    continue
                if _textual_char_count(candidate) <= _structural_char_count(candidate):
                    continue
                candidates.append(candidate)

    parts = [part for part in text.split("\n\n") if part.strip()]
    if len(parts) <= 1:
        parts = [part for part in text.splitlines() if part.strip()]
    for part in parts:
        candidate = _compact_candidate(part)
        if candidate:
            candidates.append(candidate)

    candidates.append(compact)
    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        deduped.append(candidate)
    return deduped


def _candidate_score(text: str) -> tuple[int, int, int, int, str]:
    textual = _textual_char_count(text)
    structural_penalty = _structural_char_count(text)
    return (
        textual - structural_penalty,
        textual,
        -structural_penalty,
        -abs(len(text) - 96),
        text,
    )


def _format_recall_excerpt(text: str, *, max_chars: int = 120) -> str:
    candidates = _excerpt_candidates(text)
    if not candidates:
        return ""
    best = max(candidates, key=_candidate_score)
    return _truncate(best, max_chars=max_chars)


def _reconstruct_span_text(
    message_index: dict[tuple[str, str], _CanonicalMessageRecord],
    conversation_id: str,
    message_ids: list[str],
) -> str | None:
    texts: list[str] = []
    for message_id in message_ids:
        record = message_index.get((conversation_id, message_id))
        if record is None:
            return None
        if record.text:
            texts.append(record.text)
    if not texts:
        return None
    return "\n\n".join(texts)


def _rendered_excerpt(
    *,
    message_index: dict[tuple[str, str], _CanonicalMessageRecord],
    conversation_id: str,
    message_ids: list[str],
    fallback_excerpt: str,
) -> str:
    reconstructed = _reconstruct_span_text(
        message_index,
        conversation_id,
        message_ids,
    )
    if reconstructed is not None:
        formatted = _format_recall_excerpt(reconstructed)
        if formatted:
            return formatted
    return _truncate(fallback_excerpt)


def _source_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row["source_conversation_id"]),
        str(row["source_topic_id"]),
        str(row["source_span_id"]),
    )


def _summary_line(excerpt: str) -> str:
    text = excerpt.strip()
    if any(token in text for token in ("公開完了", "リリース", "公開")):
        return _("memory_recall.summary_line.release")
    if any(token in text for token in ("現状サマリ", "進捗", "状況", "まとめ")):
        return _("memory_recall.summary_line.status")
    if any(token in text for token in ("テンプレ", "updates", "pattern")):
        return _("memory_recall.summary_line.template")
    return _("memory_recall.summary_line.generic")


def _match_line(excerpt: str) -> str:
    if any(token in excerpt for token in ("公開完了", "リリース", "公開")):
        return _("memory_recall.match_line.release")
    if any(token in excerpt for token in ("現状サマリ", "進捗", "状況", "まとめ")):
        return _("memory_recall.match_line.status")
    if any(token in excerpt for token in ("テンプレ", "updates", "pattern")):
        return _("memory_recall.match_line.template")
    return _("memory_recall.match_line.generic")


def _endpoint_key(row: dict[str, Any], prefix: str) -> tuple[str, str]:
    return (
        str(row[f"{prefix}_conversation_id"]),
        str(row[f"{prefix}_span_id"]),
    )


def _pair_key(row: dict[str, Any]) -> tuple[tuple[str, str], tuple[str, str]]:
    endpoints = sorted((_endpoint_key(row, "source"), _endpoint_key(row, "target")))
    return endpoints[0], endpoints[1]


def _row_priority(
    row: dict[str, Any],
) -> tuple[int, int, float, int, tuple[str, str], tuple[str, str]]:
    confidence = str(row.get("confidence", "low"))
    recall_type = str(row.get("recall_type", "continuity"))
    candidate_score = row.get("candidate_score")
    score = float(candidate_score) if isinstance(candidate_score, (int, float)) else -1.0
    candidate_rank = row.get("candidate_rank")
    rank = int(candidate_rank) if isinstance(candidate_rank, int) else 9999
    return (
        _CONFIDENCE_RANK.get(confidence, -1),
        _RECALL_TYPE_RANK.get(recall_type, -1),
        score,
        -rank,
        _endpoint_key(row, "source"),
        _endpoint_key(row, "target"),
    )


def _deduplicate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best_by_pair: dict[tuple[tuple[str, str], tuple[str, str]], dict[str, Any]] = {}
    for row in rows:
        pair_key = _pair_key(row)
        current = best_by_pair.get(pair_key)
        if current is None or _row_priority(row) > _row_priority(current):
            best_by_pair[pair_key] = row
    deduped = list(best_by_pair.values())
    deduped.sort(
        key=lambda row: (
            _pair_key(row),
            _endpoint_key(row, "source"),
            _endpoint_key(row, "target"),
        )
    )
    return deduped


def _swap_row_direction(row: dict[str, Any]) -> dict[str, Any]:
    swapped = dict(row)
    directional_fields = (
        "conversation_id",
        "topic_id",
        "span_id",
        "message_ids",
        "excerpt",
        "topic_label",
    )
    for field in directional_fields:
        source_key = f"source_{field}"
        target_key = f"target_{field}"
        swapped[source_key] = row[target_key]
        swapped[target_key] = row[source_key]
    return swapped


def _endpoint_timestamp(
    message_index: dict[tuple[str, str], _CanonicalMessageRecord],
    row: dict[str, Any],
    prefix: str,
) -> int | None:
    return _first_timestamp(
        message_index,
        str(row[f"{prefix}_conversation_id"]),
        list(row[f"{prefix}_message_ids"]),
    )


def _oriented_row(
    row: dict[str, Any],
    *,
    message_index: dict[tuple[str, str], _CanonicalMessageRecord],
) -> dict[str, Any]:
    source_key = _endpoint_key(row, "source")
    target_key = _endpoint_key(row, "target")
    source_ts = _endpoint_timestamp(message_index, row, "source")
    target_ts = _endpoint_timestamp(message_index, row, "target")

    should_swap = False
    if source_ts is not None and target_ts is not None:
        should_swap = target_ts > source_ts
    elif source_ts is None and target_ts is not None:
        should_swap = True
    elif source_ts == target_ts:
        should_swap = target_key > source_key

    return _swap_row_direction(row) if should_swap else row


def _normalize_phrase_text(text: str) -> str:
    chars: list[str] = []
    for char in text.casefold():
        if (
            char.isalpha()
            or "\u3040" <= char <= "\u30ff"
            or "\u3400" <= char <= "\u9fff"
        ):
            chars.append(char)
    return "".join(chars)


def _has_strong_recall_signal(text: str) -> bool:
    compact = _compact_candidate(text).casefold()
    return any(marker.casefold() in compact for marker in _STRONG_RECALL_MARKERS)


def _is_greeting_like(text: str) -> bool:
    normalized = _normalize_phrase_text(text)
    if not normalized:
        return False
    return any(normalized.startswith(prefix) for prefix in _GREETING_PREFIXES)


def _is_ack_like(text: str) -> bool:
    normalized = _normalize_phrase_text(text)
    if not normalized:
        return False
    if normalized in _ACK_EXACT:
        return True
    return normalized.startswith(("thankyou", "thanks", "gotit", "了解", "承知")) and len(
        normalized
    ) <= 18


def _lexical_signal_count(text: str) -> int:
    compact = _compact_candidate(text).casefold()
    return sum(1 for token in _WORD_RE.findall(compact) if token)


def _is_low_value_excerpt(text: str) -> bool:
    compact = _compact_candidate(text)
    if not compact:
        return True
    if _has_strong_recall_signal(compact):
        return False
    if _is_ack_like(compact):
        return True
    if _is_greeting_like(compact) and _textual_char_count(compact) <= 32:
        return True
    return _textual_char_count(compact) <= 12 and _lexical_signal_count(compact) <= 1


def _reason_supports_recall(reason: str) -> bool:
    compact = _compact_candidate(reason)
    if not compact:
        return False
    if _has_strong_recall_signal(compact):
        return True
    lowered = compact.casefold()
    if any(marker in lowered for marker in _LOW_VALUE_REASON_MARKERS):
        return False
    if _is_greeting_like(compact) or _is_ack_like(compact):
        return False
    return _textual_char_count(compact) >= 28 and _lexical_signal_count(compact) >= 2


def _should_suppress_prepared_row(prepared: _PreparedRecallRow) -> bool:
    if _reason_supports_recall(str(prepared.row.get("reason", ""))):
        return False
    return _is_low_value_excerpt(prepared.source_excerpt) and _is_low_value_excerpt(
        prepared.target_excerpt
    )


def _prepare_recall_row(
    row: dict[str, Any],
    *,
    message_index: dict[tuple[str, str], _CanonicalMessageRecord],
) -> _PreparedRecallRow:
    return _PreparedRecallRow(
        row=row,
        source_excerpt=_rendered_excerpt(
            message_index=message_index,
            conversation_id=str(row["source_conversation_id"]),
            message_ids=list(row["source_message_ids"]),
            fallback_excerpt=str(row["source_excerpt"]),
        ),
        target_excerpt=_rendered_excerpt(
            message_index=message_index,
            conversation_id=str(row["target_conversation_id"]),
            message_ids=list(row["target_message_ids"]),
            fallback_excerpt=str(row["target_excerpt"]),
        ),
        source_ts=_endpoint_timestamp(message_index, row, "source"),
        target_ts=_endpoint_timestamp(message_index, row, "target"),
    )


def render_cross_thread_memory_recall(input_root: Path) -> str:
    rows = _load_evaluation_rows(input_root)
    filtered = [
        row
        for row in rows
        if row["same_intent"] == "yes" and row["confidence"] in ALLOWED_CONFIDENCE
    ]
    if not filtered:
        return _("memory_recall.no_matches")

    message_index = _message_record_index(input_root)
    prepared_rows = [
        _prepare_recall_row(
            _oriented_row(row, message_index=message_index),
            message_index=message_index,
        )
        for row in _deduplicate_rows(filtered)
    ]
    prepared_rows = [
        prepared for prepared in prepared_rows if not _should_suppress_prepared_row(prepared)
    ]
    if not prepared_rows:
        return _("memory_recall.no_matches")

    grouped: dict[tuple[str, str, str], list[_PreparedRecallRow]] = defaultdict(list)
    for prepared in prepared_rows:
        grouped[_source_key(prepared.row)].append(prepared)

    ordered_groups = sorted(
        grouped.items(),
        key=lambda item: (
            item[1][0].source_ts or 0,
            item[0],
        ),
    )

    sections: list[str] = []
    for group_index, (_key, matches) in enumerate(ordered_groups):
        matches.sort(
            key=lambda prepared: (
                -1 if prepared.row["confidence"] == "high" else 0,
                -1 if prepared.row.get("recall_type") == "recurrence" else 0,
                prepared.row.get("candidate_rank", 9999),
                prepared.row["target_conversation_id"],
                prepared.row["target_span_id"],
            )
        )
        source_row = matches[0]
        if group_index:
            sections.append("")
        sections.append(_("memory_recall.header"))
        sections.append("")
        sections.append(_("memory_recall.source_section"))
        source_excerpt = source_row.source_excerpt
        sections.append(source_excerpt)
        sections.append(_summary_line(source_excerpt))
        if source_row.source_ts is not None:
            sections.append(
                _("memory_recall.approximate_date", date=_format_date(source_row.source_ts))
            )
        sections.append("")
        sections.append(_("memory_recall.matches_section"))
        for prepared in matches:
            sections.append(f"* {_format_date(prepared.target_ts)}")
            sections.append(f"  → {_match_line(prepared.target_excerpt)}")
            reason = prepared.row.get("reason")
            if isinstance(reason, str) and reason.strip():
                sections.append(
                    _("memory_recall.reason_line", reason=" ".join(reason.split()))
                )
            sections.append(f"  「{prepared.target_excerpt}」")
    return "\n".join(sections)
