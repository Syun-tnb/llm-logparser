from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .l1_derivation import iter_input_message_records, ts_to_seconds
from .schema_validation import load_cross_thread_intent_evaluation_validator

ALLOWED_CONFIDENCE = frozenset({"high", "medium"})


class CrossThreadMemoryRecallError(RuntimeError):
    pass


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


def _message_timestamp_index(input_root: Path) -> dict[tuple[str, str], int]:
    index: dict[tuple[str, str], int] = {}
    try:
        for row in iter_input_message_records(input_root):
            conversation_id = row.get("conversation_id")
            message_id = row.get("message_id")
            ts = row.get("ts")
            if (
                isinstance(conversation_id, str)
                and isinstance(message_id, str)
                and isinstance(ts, int)
            ):
                index[(conversation_id, message_id)] = ts
    except (FileNotFoundError, ValueError) as exc:
        raise CrossThreadMemoryRecallError(str(exc)) from exc
    return index


def _first_timestamp(
    message_index: dict[tuple[str, str], int],
    conversation_id: str,
    message_ids: list[str],
) -> int | None:
    timestamps = [
        message_index[(conversation_id, message_id)]
        for message_id in message_ids
        if (conversation_id, message_id) in message_index
    ]
    if not timestamps:
        return None
    return min(timestamps)


def _format_date(ts: int | None) -> str:
    seconds = ts_to_seconds(ts)
    if seconds is None:
        return "時期不明"
    return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y/%m/%d")


def _truncate(text: str, *, max_chars: int = 72) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3] + "..."


def _source_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row["source_conversation_id"]),
        str(row["source_topic_id"]),
        str(row["source_span_id"]),
    )


def _summary_line(excerpt: str) -> str:
    text = excerpt.strip()
    if any(token in text for token in ("公開完了", "リリース", "公開")):
        return "これは過去の公開完了の流れと同じ話やで"
    if any(token in text for token in ("現状サマリ", "進捗", "状況", "まとめ")):
        return "これは前の進捗共有とつながる話やで"
    if any(token in text for token in ("テンプレ", "updates", "pattern")):
        return "これは前にも似た更新の話をしてるで"
    return "これは前にも似た流れの話をしてるで"


def _match_line(row: dict[str, Any]) -> str:
    excerpt = str(row["target_excerpt"])
    if any(token in excerpt for token in ("公開完了", "リリース", "公開")):
        return "前にも公開完了の話してるで"
    if any(token in excerpt for token in ("現状サマリ", "進捗", "状況", "まとめ")):
        return "前にも似た進み具合の話をしてるで"
    if any(token in excerpt for token in ("テンプレ", "updates", "pattern")):
        return "前にも似た更新のやり取りやな"
    return "前にも似た話してるで"


def render_cross_thread_memory_recall(input_root: Path) -> str:
    rows = _load_evaluation_rows(input_root)
    filtered = [
        row
        for row in rows
        if row["same_intent"] == "yes" and row["confidence"] in ALLOWED_CONFIDENCE
    ]
    if not filtered:
        return "前に似た話は見つからへんかった。"

    message_index = _message_timestamp_index(input_root)
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
        sections.append("前にも似た話してるで")
        sections.append("")
        sections.append(f"### Source:")
        sections.append(_truncate(str(source_row["source_excerpt"])))
        sections.append(_summary_line(str(source_row["source_excerpt"])))
        if source_ts is not None:
            sections.append(f"だいたいの時期: {_format_date(source_ts)}")
        sections.append("")
        sections.append("### Matches:")
        for row in matches:
            target_ts = _first_timestamp(
                message_index,
                row["target_conversation_id"],
                row["target_message_ids"],
            )
            sections.append(f"* {_format_date(target_ts)}")
            sections.append(f"  → {_match_line(row)}")
            sections.append(f"  「{_truncate(str(row['target_excerpt']))}」")
    return "\n".join(sections)
