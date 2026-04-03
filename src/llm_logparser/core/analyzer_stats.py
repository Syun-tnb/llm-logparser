from __future__ import annotations

import json
from pathlib import Path
from statistics import median
from typing import Any

from .analyzer_common import resolve_canonical_text, safe_average, safe_ratio
from .analyzer_metrics import (
    _classify_safety_interventions,
    _load_intervention_indicators,
    _load_refusal_indicators,
)
from .l1_derivation import (
    derive_thread_metrics,
    discover_parsed_jsonl,
    iter_parsed_records,
    normalized_message_role,
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


def _round_optional_average(value: float | None) -> int | float | None:
    if value is None:
        return None
    rounded = round(value, 2)
    if rounded.is_integer():
        return int(rounded)
    return rounded


def _round_optional_ratio(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 4)


def _render_optional(value: Any) -> str:
    return "N/A" if value is None else str(value)


def _load_thread_safety_from_metrics(parsed_path: Path) -> tuple[bool, bool] | None:
    metrics_path = parsed_path.with_name("metrics.json")
    if not metrics_path.exists():
        return None

    try:
        payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict):
        return None
    safety = payload.get("safety")
    if not isinstance(safety, dict):
        return None

    refusal_count = safety.get("refusal_count")
    intervention_count = safety.get("intervention_count")
    if not isinstance(refusal_count, int) or refusal_count < 0:
        return None
    if not isinstance(intervention_count, int) or intervention_count < 0:
        return None

    return refusal_count > 0, intervention_count > 0


def _analyze_thread_research(
    parsed_path: Path,
    *,
    refusal_indicators: list[str],
    intervention_indicators: list[str],
) -> dict[str, Any]:
    # invariant: analyzer_stats operates on canonical roles only
    # Machine safety heuristics must come from the locale-independent analyzer
    # rule set in analyzer_metrics.py, never from runtime locale resources.
    assistant_texts: list[str] = []
    block_count_total = 0
    code_block_message_count = 0

    for row in iter_parsed_records(parsed_path):
        if row.get("record_type") != "message":
            continue

        text, _text_source = resolve_canonical_text(row)
        if text:
            block_count_total += 1

        if "```" in text:
            code_block_message_count += 1

        # L1 invariant: must use canonical normalized roles only
        if normalized_message_role(row) == "assistant":
            assistant_texts.append(text)

    sidecar_safety = _load_thread_safety_from_metrics(parsed_path)
    if sidecar_safety is None:
        safety = _classify_safety_interventions(
            assistant_texts,
            refusal_indicators,
            intervention_indicators,
        )
        has_refusal = safety["refusal_count"] > 0
        has_intervention = safety["intervention_count"] > 0
    else:
        has_refusal, has_intervention = sidecar_safety

    return {
        "block_count_total": block_count_total,
        "code_block_message_count": code_block_message_count,
        "has_code_blocks": code_block_message_count > 0,
        "has_refusal": has_refusal,
        "has_intervention": has_intervention,
    }


def compute_research_summary(
    *,
    duration_samples: list[int],
    char_ratio_samples: list[float],
    threads_with_refusal: int,
    threads_with_intervention: int,
    threads_with_code_blocks: int,
    block_count_total: int,
    code_block_message_count: int,
    total_messages: int,
) -> dict[str, Any]:
    avg_duration = (
        _round_optional_average(sum(duration_samples) / len(duration_samples))
        if duration_samples
        else None
    )
    median_duration = (
        _round_optional_average(median(duration_samples))
        if duration_samples
        else None
    )
    mean_char_ratio = (
        _round_optional_ratio(sum(char_ratio_samples) / len(char_ratio_samples))
        if char_ratio_samples
        else None
    )
    median_char_ratio = (
        _round_optional_ratio(median(char_ratio_samples))
        if char_ratio_samples
        else None
    )

    return {
        "temporal": {
            "avg_thread_duration_seconds": avg_duration,
            "median_thread_duration_seconds": median_duration,
            "multi_day_thread_count": sum(1 for value in duration_samples if value >= 86400),
        },
        "turn_taking": {
            "mean_char_ratio_user_vs_assistant": mean_char_ratio,
            "median_char_ratio_user_vs_assistant": median_char_ratio,
        },
        "safety": {
            "threads_with_refusal": threads_with_refusal,
            "threads_with_intervention": threads_with_intervention,
        },
        "structure": {
            "avg_blocks_per_message": safe_average(block_count_total, total_messages),
            "threads_with_code_blocks": threads_with_code_blocks,
            "code_block_message_ratio": safe_ratio(
                code_block_message_count,
                total_messages,
            ),
        },
    }


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
    duration_samples: list[int] = []
    char_ratio_samples: list[float] = []
    threads_with_refusal = 0
    threads_with_intervention = 0
    threads_with_code_blocks = 0
    block_count_total = 0
    code_block_message_count = 0
    refusal_indicators = _load_refusal_indicators()
    intervention_indicators = _load_intervention_indicators()

    for parsed_path in parsed_files:
        thread_stats = derive_thread_metrics(parsed_path)
        thread_research = _analyze_thread_research(
            parsed_path,
            refusal_indicators=refusal_indicators,
            intervention_indicators=intervention_indicators,
        )
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
        duration = span_seconds(thread_stats.first_ts, thread_stats.last_ts)
        if duration is not None:
            duration_samples.append(duration)
        if thread_stats.characters_assistant:
            char_ratio_samples.append(
                thread_stats.characters_user / thread_stats.characters_assistant
            )
        if thread_research["has_refusal"]:
            threads_with_refusal += 1
        if thread_research["has_intervention"]:
            threads_with_intervention += 1
        if thread_research["has_code_blocks"]:
            threads_with_code_blocks += 1
        block_count_total += thread_research["block_count_total"]
        code_block_message_count += thread_research["code_block_message_count"]

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
        "research_summary": compute_research_summary(
            duration_samples=duration_samples,
            char_ratio_samples=char_ratio_samples,
            threads_with_refusal=threads_with_refusal,
            threads_with_intervention=threads_with_intervention,
            threads_with_code_blocks=threads_with_code_blocks,
            block_count_total=block_count_total,
            code_block_message_count=code_block_message_count,
            total_messages=total_messages,
        ),
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

    research_summary = stats.get("research_summary") or {}
    if research_summary:
        temporal = research_summary.get("temporal") or {}
        turn_taking = research_summary.get("turn_taking") or {}
        safety = research_summary.get("safety") or {}
        structure = research_summary.get("structure") or {}
        lines.extend(
            [
                "",
                "Research Summary:",
                "  Temporal:",
                "    "
                f"avg_thread_duration_seconds: {_render_optional(temporal.get('avg_thread_duration_seconds'))}",
                "    "
                f"median_thread_duration_seconds: {_render_optional(temporal.get('median_thread_duration_seconds'))}",
                "    "
                f"multi_day_thread_count: {_render_optional(temporal.get('multi_day_thread_count'))}",
                "  Turn-taking:",
                "    "
                f"mean_char_ratio_user_vs_assistant: {_render_optional(turn_taking.get('mean_char_ratio_user_vs_assistant'))}",
                "    "
                f"median_char_ratio_user_vs_assistant: {_render_optional(turn_taking.get('median_char_ratio_user_vs_assistant'))}",
                "  Safety:",
                "    "
                f"threads_with_refusal: {_render_optional(safety.get('threads_with_refusal'))}",
                "    "
                f"threads_with_intervention: {_render_optional(safety.get('threads_with_intervention'))}",
                "  Structure:",
                "    "
                f"avg_blocks_per_message: {_render_optional(structure.get('avg_blocks_per_message'))}",
                "    "
                f"threads_with_code_blocks: {_render_optional(structure.get('threads_with_code_blocks'))}",
                "    "
                f"code_block_message_ratio: {_render_optional(structure.get('code_block_message_ratio'))}",
            ]
        )

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
