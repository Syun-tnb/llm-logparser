from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .l1_derivation import (
    derive_thread_metrics,
    discover_parsed_jsonl,
    span_seconds,
    to_iso_utc,
)


def sort_threads_detail(
    threads_detail: list[dict[str, Any]],
    sort_field: str,
) -> list[dict[str, Any]]:
    """Return deterministically sorted thread rows."""
    if sort_field == "conversation_id":
        return sorted(
            threads_detail,
            key=lambda row: str(row.get("conversation_id") or ""),
        )

    if sort_field == "messages":
        return sorted(
            threads_detail,
            key=lambda row: (
                -int(row.get("message_count") or 0),
                str(row.get("conversation_id") or ""),
            ),
        )

    if sort_field == "chars":
        return sorted(
            threads_detail,
            key=lambda row: (
                -int(row.get("character_count") or 0),
                str(row.get("conversation_id") or ""),
            ),
        )

    if sort_field == "span":
        return sorted(
            threads_detail,
            key=lambda row: (
                row.get("conversation_span_seconds") is None,
                -int(row["conversation_span_seconds"])
                if row.get("conversation_span_seconds") is not None
                else 0,
                str(row.get("conversation_id") or ""),
            ),
        )

    raise ValueError(f"unsupported sort field: {sort_field}")


def select_threads_detail(
    stats: dict[str, Any],
    *,
    sort_field: str | None = None,
    top: int | None = None,
) -> list[dict[str, Any]]:
    """Apply deterministic sort and optional top-N limiting to thread rows."""
    rows = list(stats.get("threads_detail", []))
    if sort_field:
        rows = sort_threads_detail(rows, sort_field)
    if top is not None:
        rows = rows[:top]
    return rows


def build_stats_output(
    stats: dict[str, Any],
    *,
    sort_field: str | None = None,
    top: int | None = None,
) -> dict[str, Any]:
    """Return a stats payload with presentation-level thread detail selection."""
    out = dict(stats)
    out["threads_detail"] = select_threads_detail(stats, sort_field=sort_field, top=top)
    return out


def analyze_stats(input_path: Path) -> dict[str, Any]:
    """Compute deterministic statistics from canonical parsed JSONL threads."""
    parsed_files = discover_parsed_jsonl(input_path)

    threads_detail: list[dict[str, Any]] = []
    total_messages = 0
    total_user_messages = 0
    total_assistant_messages = 0
    total_other_roles = 0
    total_characters = 0
    total_user_characters = 0
    total_assistant_characters = 0
    other_role_breakdown: dict[str, int] = {}
    global_first_ts: float | None = None
    global_last_ts: float | None = None
    message_counts: list[int] = []

    for parsed_path in parsed_files:
        thread_stats = derive_thread_metrics(parsed_path)
        threads_detail.append(thread_stats.to_detail())

        total_messages += thread_stats.message_count
        total_user_messages += thread_stats.user_messages
        total_assistant_messages += thread_stats.assistant_messages
        total_other_roles += thread_stats.other_roles
        total_characters += thread_stats.character_count
        total_user_characters += thread_stats.characters_user
        total_assistant_characters += thread_stats.characters_assistant
        for role, count in (thread_stats.other_role_breakdown or {}).items():
            other_role_breakdown[role] = other_role_breakdown.get(role, 0) + count
        message_counts.append(thread_stats.message_count)

        if thread_stats.first_ts is not None:
            global_first_ts = (
                thread_stats.first_ts
                if global_first_ts is None
                else min(global_first_ts, thread_stats.first_ts)
            )
        if thread_stats.last_ts is not None:
            global_last_ts = (
                thread_stats.last_ts
                if global_last_ts is None
                else max(global_last_ts, thread_stats.last_ts)
            )

    threads_count = len(threads_detail)
    avg_chars_per_message = (
        round(total_characters / total_messages, 2) if total_messages else 0.0
    )
    messages_per_thread_avg = (
        round(sum(message_counts) / threads_count, 2) if threads_count else 0.0
    )

    return {
        "threads": threads_count,
        "messages": total_messages,
        "user_messages": total_user_messages,
        "assistant_messages": total_assistant_messages,
        "other_roles": total_other_roles,
        "other_role_breakdown": dict(sorted(other_role_breakdown.items())),
        "characters_total": total_characters,
        "characters_user": total_user_characters,
        "characters_assistant": total_assistant_characters,
        "avg_chars_per_message": avg_chars_per_message,
        "first_timestamp": to_iso_utc(global_first_ts),
        "last_timestamp": to_iso_utc(global_last_ts),
        "conversation_span_seconds": span_seconds(global_first_ts, global_last_ts),
        "messages_per_thread_min": min(message_counts) if message_counts else 0,
        "messages_per_thread_max": max(message_counts) if message_counts else 0,
        "messages_per_thread_avg": messages_per_thread_avg,
        "threads_detail": threads_detail,
    }


def render_stats_text(
    stats: dict[str, Any],
    *,
    per_thread: bool = False,
    include_role_breakdown: bool = False,
) -> str:
    """Render analyzer stats in a compact human-readable format.

    This summary is intentionally English-only. `analyze --json` is the
    machine-readable interface, and best-effort i18n should not create a
    partially localized terminal report.
    """
    first_timestamp = stats.get("first_timestamp") or "N/A"
    last_timestamp = stats.get("last_timestamp") or "N/A"
    conversation_span_seconds = stats.get("conversation_span_seconds")
    span_display = (
        str(conversation_span_seconds)
        if conversation_span_seconds is not None
        else "N/A"
    )

    lines = [
        f"Threads: {stats['threads']}",
        f"Messages: {stats['messages']}",
        f"User messages: {stats['user_messages']}",
        f"Assistant messages: {stats['assistant_messages']}",
        f"Other roles: {stats['other_roles']}",
        "",
        f"Characters total: {stats['characters_total']}",
        f"Characters (user): {stats['characters_user']}",
        f"Characters (assistant): {stats['characters_assistant']}",
        f"Average characters per message: {stats['avg_chars_per_message']:.2f}",
        "",
        f"First timestamp: {first_timestamp}",
        f"Last timestamp: {last_timestamp}",
        f"Conversation span (seconds): {span_display}",
        "",
        "Messages per thread:",
        f"  min: {stats['messages_per_thread_min']}",
        f"  max: {stats['messages_per_thread_max']}",
        f"  avg: {stats['messages_per_thread_avg']:.2f}",
    ]

    other_role_breakdown = stats.get("other_role_breakdown") or {}
    if include_role_breakdown and other_role_breakdown:
        lines.extend(["", "Other role breakdown:"])
        for role, count in other_role_breakdown.items():
            lines.append(f"  {role}: {count}")

    if per_thread:
        lines.extend(["", "Per-thread:"])
        for row in stats.get("threads_detail", []):
            span = row.get("conversation_span_seconds")
            span_display = str(span) if span is not None else "N/A"
            lines.append(
                "  "
                f"{row.get('conversation_id', 'unknown')}  "
                f"messages={row.get('message_count', 0)}  "
                f"chars={row.get('character_count', 0)}  "
                f"span={span_display}"
            )
    return "\n".join(lines)


def render_stats_json(stats: dict[str, Any]) -> str:
    """Render analyzer stats as formatted JSON."""
    return json.dumps(stats, ensure_ascii=False, indent=2)
