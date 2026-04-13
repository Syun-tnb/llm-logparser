from __future__ import annotations

import json
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


class CrossThreadMemoryRecallError(RuntimeError):
    pass


@dataclass(frozen=True)
class _CanonicalMessageRecord:
    text: str
    ts: int | None


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
) -> tuple[int, float, int, tuple[str, str], tuple[str, str]]:
    confidence = str(row.get("confidence", "low"))
    candidate_score = row.get("candidate_score")
    score = float(candidate_score) if isinstance(candidate_score, (int, float)) else -1.0
    candidate_rank = row.get("candidate_rank")
    rank = int(candidate_rank) if isinstance(candidate_rank, int) else 9999
    return (
        _CONFIDENCE_RANK.get(confidence, -1),
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
    filtered = [
        _oriented_row(row, message_index=message_index)
        for row in _deduplicate_rows(filtered)
    ]
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in filtered:
        grouped[_source_key(row)].append(row)

    ordered_groups = sorted(
        grouped.items(),
        key=lambda item: (
            _first_timestamp(
                message_index,
                item[0][0],
                item[1][0]["source_message_ids"],
            )
            or 0,
            item[0],
        ),
    )

    sections: list[str] = []
    for group_index, (_key, matches) in enumerate(ordered_groups):
        matches.sort(
            key=lambda row: (
                -1 if row["confidence"] == "high" else 0,
                row.get("candidate_rank", 9999),
                row["target_conversation_id"],
                row["target_span_id"],
            )
        )
        source_row = matches[0]
        source_ts = _first_timestamp(
            message_index,
            source_row["source_conversation_id"],
            source_row["source_message_ids"],
        )
        if group_index:
            sections.append("")
        sections.append(_("memory_recall.header"))
        sections.append("")
        sections.append(_("memory_recall.source_section"))
        source_excerpt = _rendered_excerpt(
            message_index=message_index,
            conversation_id=str(source_row["source_conversation_id"]),
            message_ids=list(source_row["source_message_ids"]),
            fallback_excerpt=str(source_row["source_excerpt"]),
        )
        sections.append(source_excerpt)
        sections.append(_summary_line(source_excerpt))
        if source_ts is not None:
            sections.append(
                _("memory_recall.approximate_date", date=_format_date(source_ts))
            )
        sections.append("")
        sections.append(_("memory_recall.matches_section"))
        for row in matches:
            target_ts = _first_timestamp(
                message_index,
                row["target_conversation_id"],
                row["target_message_ids"],
            )
            target_excerpt = _rendered_excerpt(
                message_index=message_index,
                conversation_id=str(row["target_conversation_id"]),
                message_ids=list(row["target_message_ids"]),
                fallback_excerpt=str(row["target_excerpt"]),
            )
            sections.append(f"* {_format_date(target_ts)}")
            sections.append(f"  → {_match_line(target_excerpt)}")
            reason = row.get("reason")
            if isinstance(reason, str) and reason.strip():
                sections.append(
                    _("memory_recall.reason_line", reason=" ".join(reason.split()))
                )
            sections.append(f"  「{target_excerpt}」")
    return "\n".join(sections)
