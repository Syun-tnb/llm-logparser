from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .analyzer_semantic_preview import (
    WindowPreviewRecord,
    load_window_preview_index,
)
from .l1_derivation import (
    canonical_role_or_unknown,
    iter_input_message_records,
    ts_to_seconds,
)
from .schema_validation import (
    load_topic_membership_validator,
    load_topics_validator,
)

WindowRef = tuple[str, str]
EXPECTED_TOPICS_SCHEMA_VERSION = "2.1"
EXPECTED_TOPIC_MEMBERSHIP_SCHEMA_VERSION = "1.0"
EXPECTED_TOPIC_MEMBERSHIP_MODE = "span-and-message-v2"


class SemanticTopicExploreError(RuntimeError):
    pass


@dataclass(frozen=True)
class TopicMembershipRecord:
    provider_id: str
    topic_id: str
    membership_type: str
    conversation_id: str | None
    cluster_id: str | None
    span_id: str | None
    window_id: str | None
    message_id: str | None


@dataclass(frozen=True)
class CanonicalMessageRecord:
    conversation_id: str
    message_id: str
    role: str
    text: str
    ts: int | None


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise SemanticTopicExploreError(f"invalid JSON object in {path}")
    return payload


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SemanticTopicExploreError(
                    f"invalid JSON in {path}:{line_no}: {exc.msg}"
                ) from exc
            if not isinstance(row, dict):
                raise SemanticTopicExploreError(
                    f"invalid record in {path}:{line_no}: expected object"
                )
            rows.append(row)
    return rows


def _discover_artifacts(root: Path, name: str) -> list[Path]:
    return sorted(root.rglob(name))


def _semantic_regeneration_guidance() -> str:
    return (
        "Regenerate semantic artifacts with the current pipeline from canonical "
        "parsed.jsonl inputs."
    )


def _require_semantic_schema_version(
    *,
    artifact_name: str,
    actual_version: Any,
    expected_version: str,
    path: Path,
    location: str | None = None,
) -> None:
    if actual_version == expected_version:
        return
    suffix = f":{location}" if location is not None else ""
    raise SemanticTopicExploreError(
        f"incompatible {artifact_name} schema_version in {path}{suffix}: "
        f"expected {expected_version}, got {actual_version!r}. "
        f"{_semantic_regeneration_guidance()}"
    )


def _normalize_excerpt(text: str, *, max_chars: int = 120) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3] + "..."


def _format_timestamp(value: int | None) -> str:
    if value is None:
        return "?"
    return str(value)


def _format_score(value: float | None) -> str:
    if not isinstance(value, (int, float)):
        return "?"
    return f"{float(value):.2f}"


def _format_human_timestamp(value: int | None) -> str:
    seconds = ts_to_seconds(value)
    if seconds is None or seconds < 946684800.0:
        return _format_timestamp(value)
    return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _truncate_message_text(text: str, *, max_chars: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3] + "..."


def _is_displayable_message_text(text: str | None) -> bool:
    return isinstance(text, str) and bool(text.strip())


def _load_canonical_message_index(
    input_root: Path,
) -> dict[tuple[str, str], CanonicalMessageRecord]:
    messages: dict[tuple[str, str], CanonicalMessageRecord] = {}
    try:
        for row in iter_input_message_records(input_root):
            conversation_id = row.get("conversation_id")
            message_id = row.get("message_id")
            if not isinstance(conversation_id, str) or not isinstance(message_id, str):
                continue
            messages[(conversation_id, message_id)] = CanonicalMessageRecord(
                conversation_id=conversation_id,
                message_id=message_id,
                role=canonical_role_or_unknown(row.get("role")),
                text=row.get("text") if isinstance(row.get("text"), str) else "",
                ts=row.get("ts") if isinstance(row.get("ts"), int) else None,
            )
    except (FileNotFoundError, ValueError) as exc:
        raise SemanticTopicExploreError(str(exc)) from exc
    return messages


def _topic_conversation_count(topic: dict[str, Any]) -> int:
    quality_signals = topic.get("quality_signals")
    if isinstance(quality_signals, dict):
        value = quality_signals.get("conversation_count")
        if isinstance(value, int):
            return value
    conversation_ids = topic.get("conversation_ids", [])
    if isinstance(conversation_ids, list):
        return len(conversation_ids)
    return 0


def _topic_is_single_window(topic: dict[str, Any]) -> bool:
    quality_signals = topic.get("quality_signals")
    if not isinstance(quality_signals, dict):
        return False
    return quality_signals.get("single_window") is True


def _topic_matches_browse_filters(
    topic: dict[str, Any],
    *,
    hide_single_window: bool,
    min_window_count: int,
    min_conversation_count: int,
) -> bool:
    if hide_single_window and _topic_is_single_window(topic):
        return False

    window_count = topic.get(
        "span_count",
        topic.get("window_count", len(topic.get("span_refs", topic.get("window_refs", [])))),
    )
    if int(window_count or 0) < min_window_count:
        return False

    if _topic_conversation_count(topic) < min_conversation_count:
        return False

    return True


def load_topics_index(input_root: Path) -> dict[str, dict[str, Any]]:
    validator = load_topics_validator()
    topics: dict[str, dict[str, Any]] = {}
    paths = _discover_artifacts(input_root, "topics.json")
    if not paths:
        raise SemanticTopicExploreError(f"no topics.json found under: {input_root}")

    for path in paths:
        payload = _load_json(path)
        _require_semantic_schema_version(
            artifact_name="topics.json",
            actual_version=payload.get("schema_version"),
            expected_version=EXPECTED_TOPICS_SCHEMA_VERSION,
            path=path,
        )
        provenance = payload.get("provenance")
        membership_mode = provenance.get("membership_mode") if isinstance(provenance, dict) else None
        if membership_mode != EXPECTED_TOPIC_MEMBERSHIP_MODE:
            raise SemanticTopicExploreError(
                f"incompatible topics.json membership_mode in {path}: "
                f"expected {EXPECTED_TOPIC_MEMBERSHIP_MODE!r}, got {membership_mode!r}. "
                f"{_semantic_regeneration_guidance()}"
            )
        errors = list(validator.iter_errors(payload))
        if errors:
            raise SemanticTopicExploreError(
                f"topics schema validation failed for {path}: {errors[0].message}"
            )
        for topic in payload.get("topics", []):
            if not isinstance(topic, dict):
                continue
            topic_id = topic["topic_id"]
            topics[topic_id] = topic
    if not topics:
        raise SemanticTopicExploreError(f"no topics found under: {input_root}")
    return topics


def load_topic_membership_rows(input_root: Path) -> list[TopicMembershipRecord]:
    validator = load_topic_membership_validator()
    rows: list[TopicMembershipRecord] = []
    paths = _discover_artifacts(input_root, "topic_membership.jsonl")
    if not paths:
        raise SemanticTopicExploreError(
            f"no topic_membership.jsonl found under: {input_root}"
        )

    for path in paths:
        for line_no, row in enumerate(_load_jsonl(path), start=1):
            _require_semantic_schema_version(
                artifact_name="topic_membership.jsonl",
                actual_version=row.get("schema_version"),
                expected_version=EXPECTED_TOPIC_MEMBERSHIP_SCHEMA_VERSION,
                path=path,
                location=f"line {line_no}",
            )
            errors = list(validator.iter_errors(row))
            if errors:
                raise SemanticTopicExploreError(
                    "topic membership schema validation failed for "
                    f"{path}:{line_no}: {errors[0].message}"
                )
            rows.append(
                TopicMembershipRecord(
                    provider_id=row["provider_id"],
                    topic_id=row["topic_id"],
                    membership_type=row["membership_type"],
                    conversation_id=row.get("conversation_id"),
                    cluster_id=row.get("cluster_id"),
                    span_id=row.get("span_id"),
                    window_id=row.get("window_id"),
                    message_id=row.get("message_id"),
                )
            )
    return rows


@dataclass
class TopicExploreIndex:
    topics_by_id: dict[str, dict[str, Any]]
    memberships_by_topic: dict[str, list[TopicMembershipRecord]]
    topic_ids_by_message: dict[str, list[str]]
    topic_ids_by_conversation: dict[str, list[str]]
    windows_by_ref: dict[WindowRef, WindowPreviewRecord]
    windows_by_span: dict[tuple[str, str], WindowPreviewRecord]


def build_topic_explore_index(input_root: Path) -> TopicExploreIndex:
    topics_by_id = load_topics_index(input_root)
    membership_rows = load_topic_membership_rows(input_root)
    windows_by_ref = load_window_preview_index(input_root)
    windows_by_span = {
        (record.conversation_id, record.span_id): record
        for record in windows_by_ref.values()
    }

    memberships_by_topic: dict[str, list[TopicMembershipRecord]] = defaultdict(list)
    topic_ids_by_message: dict[str, list[str]] = defaultdict(list)
    topic_ids_by_conversation: dict[str, list[str]] = defaultdict(list)

    seen_message_links: set[tuple[str, str]] = set()
    seen_conversation_links: set[tuple[str, str]] = set()

    for row in membership_rows:
        memberships_by_topic[row.topic_id].append(row)
        if row.message_id is not None:
            key = (row.message_id, row.topic_id)
            if key not in seen_message_links:
                topic_ids_by_message[row.message_id].append(row.topic_id)
                seen_message_links.add(key)
        if row.conversation_id is not None:
            key = (row.conversation_id, row.topic_id)
            if key not in seen_conversation_links:
                topic_ids_by_conversation[row.conversation_id].append(row.topic_id)
                seen_conversation_links.add(key)

    for topic_ids in topic_ids_by_message.values():
        topic_ids.sort()
    for topic_ids in topic_ids_by_conversation.values():
        topic_ids.sort()
    for rows in memberships_by_topic.values():
        rows.sort(
            key=lambda row: (
                row.membership_type,
                row.conversation_id or "",
                row.cluster_id or "",
                row.span_id or "",
                row.window_id or "",
                row.message_id or "",
            )
        )

    return TopicExploreIndex(
        topics_by_id=topics_by_id,
        memberships_by_topic=dict(memberships_by_topic),
        topic_ids_by_message=dict(topic_ids_by_message),
        topic_ids_by_conversation=dict(topic_ids_by_conversation),
        windows_by_ref=windows_by_ref,
        windows_by_span=windows_by_span,
    )


def _topic_representative_spans(topic: dict[str, Any]) -> list[dict[str, Any]]:
    spans = topic.get("representative_spans", [])
    if isinstance(spans, list) and spans:
        return [row for row in spans if isinstance(row, dict)]
    windows = topic.get("representative_windows", [])
    if isinstance(windows, list):
        return [row for row in windows if isinstance(row, dict)]
    return []


def _topic_view_spans(topic: dict[str, Any]) -> list[dict[str, Any]]:
    spans = _topic_representative_spans(topic)
    if spans:
        return spans
    refs = topic.get("span_refs", [])
    if isinstance(refs, list):
        return [row for row in refs if isinstance(row, dict)]
    return []


def _render_ref_label(
    *,
    conversation_id: str | None,
    span_id: str | None,
    window_id: str | None,
) -> str:
    conversation = conversation_id or "?"
    if isinstance(span_id, str) and span_id:
        if isinstance(window_id, str) and window_id:
            return f"{conversation} / {span_id} ({window_id})"
        return f"{conversation} / {span_id}"
    if isinstance(window_id, str) and window_id:
        return f"{conversation} / {window_id}"
    return conversation


def _span_messages(
    span_row: dict[str, Any],
    *,
    messages_by_ref: dict[tuple[str, str], CanonicalMessageRecord],
) -> list[CanonicalMessageRecord]:
    conversation_id = span_row.get("conversation_id")
    if not isinstance(conversation_id, str):
        return []
    message_ids = span_row.get("message_ids", [])
    if not isinstance(message_ids, list):
        return []
    messages: list[CanonicalMessageRecord] = []
    for message_id in message_ids:
        if not isinstance(message_id, str):
            continue
        message = messages_by_ref.get((conversation_id, message_id))
        if message is None:
            messages.append(
                CanonicalMessageRecord(
                    conversation_id=conversation_id,
                    message_id=message_id,
                    role="unknown",
                    text="",
                    ts=None,
                )
            )
            continue
        messages.append(message)
    return messages


def _displayable_messages(
    messages: list[CanonicalMessageRecord],
) -> list[CanonicalMessageRecord]:
    return [
        message
        for message in messages
        if _is_displayable_message_text(message.text)
    ]


def _topic_full_messages(
    topic: dict[str, Any],
    *,
    messages_by_ref: dict[tuple[str, str], CanonicalMessageRecord],
) -> list[CanonicalMessageRecord]:
    rows = topic.get("span_refs", [])
    if not isinstance(rows, list):
        rows = []
    collected: list[CanonicalMessageRecord] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        for message in _span_messages(row, messages_by_ref=messages_by_ref):
            key = (message.conversation_id, message.message_id)
            if key in seen:
                continue
            seen.add(key)
            collected.append(message)
    collected.sort(
        key=lambda row: (
            row.ts if isinstance(row.ts, int) else float("inf"),
            row.conversation_id,
            row.message_id,
        )
    )
    return collected


def _render_human_topic_view(
    topic: dict[str, Any],
    *,
    messages_by_ref: dict[tuple[str, str], CanonicalMessageRecord],
    full_messages: bool,
    max_chars: int,
) -> str:
    lines = [
        f"topic_id: {topic['topic_id']}",
        f"state: {topic.get('state') or '?'}",
        "clusters: " + ", ".join(topic.get("cluster_ids", []) or ["(none)"]),
        f"spans: {topic.get('span_count', len(topic.get('span_refs', [])))}",
        f"messages: {topic.get('message_count', len(topic.get('message_refs', [])))}",
        f"first_seen: {_format_human_timestamp(topic.get('first_seen'))}",
        f"last_seen: {_format_human_timestamp(topic.get('last_seen'))}",
    ]

    if full_messages:
        lines.append("")
        lines.append("== full messages ==")
        messages = _displayable_messages(
            _topic_full_messages(topic, messages_by_ref=messages_by_ref)
        )
        if not messages:
            lines.append("(no displayable messages)")
            return "\n".join(lines)
        for message in messages:
            lines.append(
                f"[{message.role.upper()}] "
                f"{_truncate_message_text(message.text, max_chars=max_chars)}"
            )
        return "\n".join(lines)

    spans = _topic_view_spans(topic)
    for index, span_row in enumerate(spans, start=1):
        lines.append("")
        lines.append(
            "== representative span "
            f"{index} / "
            f"{_render_ref_label(conversation_id=span_row.get('conversation_id'), span_id=span_row.get('span_id'), window_id=span_row.get('window_id'))} =="
        )
        messages = _displayable_messages(
            _span_messages(span_row, messages_by_ref=messages_by_ref)
        )
        if not messages:
            lines.append("(no displayable messages)")
            continue
        for message in messages:
            lines.append(
                f"[{message.role.upper()}] "
                f"{_truncate_message_text(message.text, max_chars=max_chars)}"
            )
    return "\n".join(lines)


def _topic_list_rows(
    index: TopicExploreIndex,
    *,
    hide_single_window: bool,
    min_window_count: int,
    min_conversation_count: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for topic in index.topics_by_id.values():
        if not _topic_matches_browse_filters(
            topic,
            hide_single_window=hide_single_window,
            min_window_count=min_window_count,
            min_conversation_count=min_conversation_count,
        ):
            continue
        quality_signals = topic.get("quality_signals")
        representative_spans = _topic_representative_spans(topic)
        representative_span = representative_spans[0] if representative_spans else None
        rows.append(
            {
                "topic_id": topic["topic_id"],
                "label": topic.get("label"),
                "summary": topic.get("summary"),
                "state": topic.get("state"),
                "state_confidence": topic.get("state_confidence"),
                "cluster_count": topic.get("cluster_count", len(topic.get("cluster_ids", []))),
                "span_count": topic.get(
                    "span_count",
                    len(topic.get("span_refs", topic.get("window_refs", []))),
                ),
                "window_count": topic.get(
                    "window_count",
                    topic.get("span_count", len(topic.get("span_refs", topic.get("window_refs", [])))),
                ),
                "message_count": topic.get("message_count", len(topic.get("message_refs", []))),
                "conversation_count": len(topic.get("conversation_ids", [])),
                "quality_signals": quality_signals,
                "representative_span": representative_span,
                "first_seen": topic.get("first_seen"),
                "last_seen": topic.get("last_seen"),
            }
        )
    rows.sort(
        key=lambda row: (
            -int(
                (row["quality_signals"] or {}).get("cluster_size")
                or row["window_count"]
                or 0
            ),
            -int(
                (row["quality_signals"] or {}).get("conversation_count")
                or row["conversation_count"]
                or 0
            ),
            (row["quality_signals"] or {}).get("avg_intra_cluster_score") is None,
            -float((row["quality_signals"] or {}).get("avg_intra_cluster_score") or 0.0),
            row["topic_id"],
        )
    )
    return rows


def _topic_timeline(
    *,
    topic: dict[str, Any],
    windows_by_span: dict[tuple[str, str], WindowPreviewRecord],
    windows_by_ref: dict[WindowRef, WindowPreviewRecord],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ref in topic.get("span_refs", topic.get("window_refs", [])):
        if not isinstance(ref, dict):
            continue
        conversation_id = ref.get("conversation_id")
        span_id = ref.get("span_id")
        window_id = ref.get("window_id")
        if not isinstance(conversation_id, str):
            continue
        record = None
        if isinstance(span_id, str) and span_id:
            record = windows_by_span.get((conversation_id, span_id))
        if record is None and isinstance(window_id, str) and window_id:
            record = windows_by_ref.get((conversation_id, window_id))
        if record is None:
            rows.append(
                {
                    "timestamp": None,
                    "conversation_id": conversation_id,
                    "span_id": span_id,
                    "window_id": window_id,
                    "excerpt": "",
                }
            )
            continue
        timestamp = record.ts_start if isinstance(record.ts_start, int) else record.ts_end
        rows.append(
            {
                "timestamp": timestamp,
                "conversation_id": record.conversation_id,
                "span_id": record.span_id,
                "window_id": record.window_id,
                "excerpt": _normalize_excerpt(record.text),
            }
        )
    rows.sort(
        key=lambda row: (
            row["timestamp"] if isinstance(row["timestamp"], int) else float("inf"),
            row["conversation_id"],
            row.get("window_id") or row.get("span_id") or "",
        )
    )
    return rows


def _topic_detail_payload(index: TopicExploreIndex, topic_id: str) -> dict[str, Any]:
    topic = index.topics_by_id.get(topic_id)
    if topic is None:
        raise SemanticTopicExploreError(f"topic not found: {topic_id}")
    return {
        "view": "topic-detail",
        "topic": {
            "topic_id": topic["topic_id"],
            "label": topic.get("label"),
            "summary": topic.get("summary"),
            "keywords": topic.get("keywords", []),
            "confidence": topic.get("confidence"),
            "state": topic.get("state"),
            "state_confidence": topic.get("state_confidence"),
            "cluster_count": topic.get("cluster_count", len(topic.get("cluster_ids", []))),
            "span_count": topic.get(
                "span_count",
                len(topic.get("span_refs", topic.get("window_refs", []))),
            ),
            "window_count": topic.get(
                "window_count",
                topic.get("span_count", len(topic.get("span_refs", topic.get("window_refs", [])))),
            ),
            "message_count": topic.get("message_count", len(topic.get("message_refs", []))),
            "cluster_ids": topic.get("cluster_ids", []),
            "conversation_ids": topic.get("conversation_ids", []),
            "span_refs": topic.get("span_refs", []),
            "quality_signals": topic.get("quality_signals"),
            "representative_spans": _topic_representative_spans(topic),
            "representative_windows": topic.get("representative_windows", []),
            "first_seen": topic.get("first_seen"),
            "last_seen": topic.get("last_seen"),
            "timeline": _topic_timeline(
                topic=topic,
                windows_by_span=index.windows_by_span,
                windows_by_ref=index.windows_by_ref,
            ),
        },
    }


def _message_lookup_payload(index: TopicExploreIndex, message_id: str) -> dict[str, Any]:
    topic_ids = index.topic_ids_by_message.get(message_id, [])
    if not topic_ids:
        raise SemanticTopicExploreError(f"message not found in topic membership: {message_id}")
    return {
        "view": "message-lookup",
        "message_id": message_id,
        "topics": [
            {
                "topic_id": topic_id,
                "label": index.topics_by_id.get(topic_id, {}).get("label"),
                "summary": index.topics_by_id.get(topic_id, {}).get("summary"),
                "state": index.topics_by_id.get(topic_id, {}).get("state"),
                "state_confidence": index.topics_by_id.get(topic_id, {}).get(
                    "state_confidence"
                ),
            }
            for topic_id in topic_ids
        ],
    }


def _conversation_payload(
    index: TopicExploreIndex,
    conversation_id: str,
    *,
    hide_single_window: bool,
    min_window_count: int,
    min_conversation_count: int,
) -> dict[str, Any]:
    topic_ids = index.topic_ids_by_conversation.get(conversation_id, [])
    if not topic_ids:
        raise SemanticTopicExploreError(
            f"conversation not found in topic membership: {conversation_id}"
        )

    topics: list[dict[str, Any]] = []
    for topic_id in topic_ids:
        topic = index.topics_by_id.get(topic_id)
        if topic is None:
            continue
        if not _topic_matches_browse_filters(
            topic,
            hide_single_window=hide_single_window,
            min_window_count=min_window_count,
            min_conversation_count=min_conversation_count,
        ):
            continue
        rows = [
            row
            for row in index.memberships_by_topic.get(topic_id, [])
            if row.membership_type == "message" and row.conversation_id == conversation_id
        ]
        timestamps: list[int] = []
        for row in rows:
            record = None
            if row.span_id is not None:
                record = index.windows_by_span.get((conversation_id, row.span_id))
            if record is None and row.window_id is not None:
                record = index.windows_by_ref.get((conversation_id, row.window_id))
            if record is None:
                continue
            if isinstance(record.ts_start, int):
                timestamps.append(record.ts_start)
            if isinstance(record.ts_end, int):
                timestamps.append(record.ts_end)
        topics.append(
            {
                "topic_id": topic_id,
                "label": topic.get("label"),
                "summary": topic.get("summary"),
                "state": topic.get("state"),
                "state_confidence": topic.get("state_confidence"),
                "message_count": len(rows),
                "quality_signals": topic.get("quality_signals"),
                "first_seen": min(timestamps) if timestamps else None,
                "last_seen": max(timestamps) if timestamps else None,
            }
        )
    topics.sort(key=lambda row: (-row["message_count"], row["topic_id"]))
    return {
        "view": "conversation",
        "conversation_id": conversation_id,
        "topics": topics,
    }


def build_semantic_topic_explore_payload(
    *,
    input_root: Path,
    topic_id: str | None = None,
    message_id: str | None = None,
    conversation_id: str | None = None,
    hide_single_window: bool = False,
    min_window_count: int = 1,
    min_conversation_count: int = 1,
) -> dict[str, Any]:
    if min_window_count <= 0:
        raise SemanticTopicExploreError("--min-window-count must be > 0")
    if min_conversation_count <= 0:
        raise SemanticTopicExploreError("--min-conversation-count must be > 0")
    selected = [value is not None for value in (topic_id, message_id, conversation_id)]
    if sum(selected) > 1:
        raise SemanticTopicExploreError(
            "choose only one of --topic-id, --message-id, or --conversation-id"
        )

    index = build_topic_explore_index(input_root)
    if topic_id is not None:
        return _topic_detail_payload(index, topic_id)
    if message_id is not None:
        return _message_lookup_payload(index, message_id)
    if conversation_id is not None:
        return _conversation_payload(
            index,
            conversation_id,
            hide_single_window=hide_single_window,
            min_window_count=min_window_count,
            min_conversation_count=min_conversation_count,
        )
    return {
        "view": "topic-list",
        "topics": _topic_list_rows(
            index,
            hide_single_window=hide_single_window,
            min_window_count=min_window_count,
            min_conversation_count=min_conversation_count,
        ),
    }


def _render_topic_list(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    for row in payload["topics"]:
        label = row["label"] or "(unlabeled)"
        summary = row["summary"] or "(none)"
        representative = row.get("representative_span")
        quality_signals = row.get("quality_signals") or {}
        lines.append(f"{row['topic_id']} | {label}")
        lines.append(f"  summary: {summary}")
        lines.append(
            "  state: "
            f"{row['state'] or '?'}"
            + (
                f" ({row['state_confidence']:.2f})"
                if isinstance(row.get("state_confidence"), (int, float))
                else ""
            )
        )
        lines.append(
            "  stats: "
            f"clusters={row['cluster_count']} windows={row['window_count']} "
            f"messages={row['message_count']} conversations={row['conversation_count']} "
            f"avg_intra_cluster_score={_format_score(quality_signals.get('avg_intra_cluster_score'))} "
            f"range={_format_timestamp(row['first_seen'])} -> {_format_timestamp(row['last_seen'])}"
        )
        if isinstance(representative, dict):
            excerpt = representative.get("excerpt")
            preview_text = (
                f"\"{excerpt}\""
                if _is_displayable_message_text(excerpt)
                else "(no displayable preview)"
            )
            lines.append(
                "  preview: "
                f"[{_render_ref_label(conversation_id=representative.get('conversation_id'), span_id=representative.get('span_id'), window_id=representative.get('window_id'))}] "
                f"{preview_text}"
            )
    return "\n".join(lines)


def _render_topic_detail(payload: dict[str, Any]) -> str:
    topic = payload["topic"]
    lines = [
        f"Topic {topic['topic_id']}",
        f"Label: {topic['label'] or '(unlabeled)'}",
        f"Summary: {topic['summary'] or '(none)'}",
        (
            "State: "
            f"{topic['state'] or '?'}"
            + (
                f" ({topic['state_confidence']:.2f})"
                if isinstance(topic.get('state_confidence'), (int, float))
                else ""
            )
        ),
        "Keywords: "
        + (", ".join(topic["keywords"]) if topic["keywords"] else "(none)"),
        (
            "Stats: "
            f"clusters={topic['cluster_count']} windows={topic['window_count']} "
            f"messages={topic['message_count']}"
        ),
        (
            "Quality: "
            f"windows={(topic.get('quality_signals') or {}).get('cluster_size', topic['window_count'])} "
            f"conversations={(topic.get('quality_signals') or {}).get('conversation_count', len(topic['conversation_ids']))} "
            f"avg_intra_cluster_score={_format_score((topic.get('quality_signals') or {}).get('avg_intra_cluster_score'))} "
            f"max_intra_cluster_score={_format_score((topic.get('quality_signals') or {}).get('max_intra_cluster_score'))} "
            f"single_window={'yes' if (topic.get('quality_signals') or {}).get('single_window') else 'no'}"
        ),
        f"Range: {_format_timestamp(topic['first_seen'])} -> {_format_timestamp(topic['last_seen'])}",
        "Conversations: "
        + (", ".join(topic["conversation_ids"]) if topic["conversation_ids"] else "(none)"),
        "Representative:",
    ]
    for row in topic.get("representative_spans", topic.get("representative_windows", [])):
        lines.append(
            f"- [{_render_ref_label(conversation_id=row.get('conversation_id'), span_id=row.get('span_id'), window_id=row.get('window_id'))}] "
            f"\"{row['excerpt']}\""
        )
    lines.extend(
        [
        "Timeline:",
        ]
    )
    for row in topic["timeline"]:
        lines.append(
            f"- {_format_timestamp(row['timestamp'])} | "
            f"{_render_ref_label(conversation_id=row.get('conversation_id'), span_id=row.get('span_id'), window_id=row.get('window_id'))} "
            f"| \"{row['excerpt']}\""
        )
    return "\n".join(lines)


def _render_message_lookup(payload: dict[str, Any]) -> str:
    lines = [f"Message {payload['message_id']}"]
    for row in payload["topics"]:
        state_suffix = (
            f" | state={row['state']} ({row['state_confidence']:.2f})"
            if isinstance(row.get("state_confidence"), (int, float))
            else f" | state={row['state'] or '?'}"
        )
        lines.append(
            f"- {row['topic_id']} | {row['label'] or '(unlabeled)'}{state_suffix}"
        )
        lines.append(f"  {row['summary'] or '(no summary)'}")
    return "\n".join(lines)


def _render_conversation(payload: dict[str, Any]) -> str:
    lines = [f"Conversation {payload['conversation_id']}"]
    for row in payload["topics"]:
        state_suffix = (
            f"state={row['state']} ({row['state_confidence']:.2f}) "
            if isinstance(row.get("state_confidence"), (int, float))
            else f"state={row['state'] or '?'} "
        )
        lines.append(
            f"- {row['topic_id']} | {row['label'] or '(unlabeled)'} | "
            f"{state_suffix}messages={row['message_count']} "
            f"range={_format_timestamp(row['first_seen'])} -> {_format_timestamp(row['last_seen'])}"
        )
    return "\n".join(lines)


def render_semantic_topic_explore(
    *,
    input_root: Path,
    topic_id: str | None = None,
    message_id: str | None = None,
    conversation_id: str | None = None,
    hide_single_window: bool = False,
    min_window_count: int = 1,
    min_conversation_count: int = 1,
    view: bool = False,
    full_messages: bool = False,
    max_chars: int = 400,
    json_output: bool = False,
) -> str:
    if max_chars <= 0:
        raise SemanticTopicExploreError("--max-chars must be > 0")
    if view and json_output:
        raise SemanticTopicExploreError("--view cannot be combined with --json")
    if full_messages and not view:
        raise SemanticTopicExploreError("--full-messages requires --view")
    if view and topic_id is None:
        raise SemanticTopicExploreError("--view currently requires --topic-id")

    if view:
        index = build_topic_explore_index(input_root)
        topic = index.topics_by_id.get(topic_id)
        if topic is None:
            raise SemanticTopicExploreError(f"topic not found: {topic_id}")
        messages_by_ref = _load_canonical_message_index(input_root)
        return _render_human_topic_view(
            topic,
            messages_by_ref=messages_by_ref,
            full_messages=full_messages,
            max_chars=max_chars,
        )

    payload = build_semantic_topic_explore_payload(
        input_root=input_root,
        topic_id=topic_id,
        message_id=message_id,
        conversation_id=conversation_id,
        hide_single_window=hide_single_window,
        min_window_count=min_window_count,
        min_conversation_count=min_conversation_count,
    )
    if json_output:
        return json.dumps(payload, ensure_ascii=False, indent=2)
    if payload["view"] == "topic-detail":
        return _render_topic_detail(payload)
    if payload["view"] == "message-lookup":
        return _render_message_lookup(payload)
    if payload["view"] == "conversation":
        return _render_conversation(payload)
    return _render_topic_list(payload)
