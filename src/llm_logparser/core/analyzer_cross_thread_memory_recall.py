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


def _match_line(row: dict[str, Any]) -> str:
    excerpt = str(row["target_excerpt"])
    if any(token in excerpt for token in ("公開完了", "リリース", "公開")):
        return _("memory_recall.match_line.release")
    if any(token in excerpt for token in ("現状サマリ", "進捗", "状況", "まとめ")):
        return _("memory_recall.match_line.status")
    if any(token in excerpt for token in ("テンプレ", "updates", "pattern")):
        return _("memory_recall.match_line.template")
    return _("memory_recall.match_line.generic")


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
            sections.append(f"* {_format_date(target_ts)}")
            sections.append(f"  → {_match_line(row)}")
            reason = row.get("reason")
            if isinstance(reason, str) and reason.strip():
                sections.append(
                    _("memory_recall.reason_line", reason=" ".join(reason.split()))
                )
            target_excerpt = _rendered_excerpt(
                message_index=message_index,
                conversation_id=str(row["target_conversation_id"]),
                message_ids=list(row["target_message_ids"]),
                fallback_excerpt=str(row["target_excerpt"]),
            )
            sections.append(f"  「{target_excerpt}」")
    return "\n".join(sections)
