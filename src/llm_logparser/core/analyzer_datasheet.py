from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .analyzer_metrics import (
    _load_intervention_indicators,
    _load_refusal_indicators,
)
from .analyzer_stats import _analyze_thread_research, compute_research_summary
from .l1_derivation import derive_thread_metrics, discover_parsed_jsonl


def _load_thread_stats_sidecar(parsed_path: Path) -> dict[str, Any] | None:
    thread_stats_path = parsed_path.with_name("thread_stats.json")
    if not thread_stats_path.exists():
        return None

    try:
        payload = json.loads(thread_stats_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict):
        return None
    if payload.get("artifact_type") != "thread_stats":
        return None

    required_non_negative_ints = (
        "message_count",
        "user_messages",
        "assistant_messages",
        "other_roles",
        "character_count",
        "characters_user",
        "characters_assistant",
    )
    for key in required_non_negative_ints:
        value = payload.get(key)
        if not isinstance(value, int) or value < 0:
            return None

    duration = payload.get("conversation_span_seconds")
    if duration is not None and (not isinstance(duration, int) or duration < 0):
        return None

    conversation_id = payload.get("conversation_id")
    if not isinstance(conversation_id, str) or not conversation_id:
        return None

    return {
        "conversation_id": conversation_id,
        "message_count": payload["message_count"],
        "user_messages": payload["user_messages"],
        "assistant_messages": payload["assistant_messages"],
        "other_roles": payload["other_roles"],
        "character_count": payload["character_count"],
        "characters_user": payload["characters_user"],
        "characters_assistant": payload["characters_assistant"],
        "conversation_span_seconds": duration,
    }


def _thread_summary_from_canonical(parsed_path: Path) -> dict[str, Any]:
    metrics = derive_thread_metrics(parsed_path)
    return {
        "conversation_id": metrics.conversation_id,
        "message_count": metrics.message_count,
        "user_messages": metrics.user_messages,
        "assistant_messages": metrics.assistant_messages,
        "other_roles": metrics.other_roles,
        "character_count": metrics.character_count,
        "characters_user": metrics.characters_user,
        "characters_assistant": metrics.characters_assistant,
        "conversation_span_seconds": (
            int(metrics.last_ts - metrics.first_ts)
            if metrics.first_ts is not None and metrics.last_ts is not None
            else None
        ),
    }


def _format_markdown_value(value: Any) -> str:
    if value is None:
        return "N/A"
    return str(value)


def build_datasheet_summary(input_path: Path) -> dict[str, Any]:
    parsed_files = discover_parsed_jsonl(input_path)
    refusal_indicators = _load_refusal_indicators()
    intervention_indicators = _load_intervention_indicators()

    total_messages = 0
    total_user_messages = 0
    total_assistant_messages = 0
    duration_samples: list[int] = []
    char_ratio_samples: list[float] = []
    threads_with_refusal = 0
    threads_with_intervention = 0
    threads_with_code_blocks = 0
    block_count_total = 0
    code_block_message_count = 0

    for parsed_path in parsed_files:
        thread_summary = _load_thread_stats_sidecar(parsed_path) or _thread_summary_from_canonical(
            parsed_path
        )
        thread_research = _analyze_thread_research(
            parsed_path,
            refusal_indicators=refusal_indicators,
            intervention_indicators=intervention_indicators,
        )

        total_messages += thread_summary["message_count"]
        total_user_messages += thread_summary["user_messages"]
        total_assistant_messages += thread_summary["assistant_messages"]

        duration = thread_summary["conversation_span_seconds"]
        if duration is not None:
            duration_samples.append(duration)
        if thread_summary["characters_assistant"]:
            char_ratio_samples.append(
                thread_summary["characters_user"]
                / thread_summary["characters_assistant"]
            )

        if thread_research["has_refusal"]:
            threads_with_refusal += 1
        if thread_research["has_intervention"]:
            threads_with_intervention += 1
        if thread_research["has_code_blocks"]:
            threads_with_code_blocks += 1
        block_count_total += thread_research["block_count_total"]
        code_block_message_count += thread_research["code_block_message_count"]

    research_summary = compute_research_summary(
        duration_samples=duration_samples,
        char_ratio_samples=char_ratio_samples,
        threads_with_refusal=threads_with_refusal,
        threads_with_intervention=threads_with_intervention,
        threads_with_code_blocks=threads_with_code_blocks,
        block_count_total=block_count_total,
        code_block_message_count=code_block_message_count,
        total_messages=total_messages,
    )

    return {
        "artifact_type": "datasheet",
        "schema_version": "1.0",
        "corpus_overview": {
            "total_threads": len(parsed_files),
            "total_messages": total_messages,
            "total_user_messages": total_user_messages,
            "total_assistant_messages": total_assistant_messages,
        },
        "temporal": research_summary["temporal"],
        "turn_taking": research_summary["turn_taking"],
        "safety": research_summary["safety"],
        "structure": research_summary["structure"],
        "notes": [
            "Generated locally and deterministically from canonical parsed artifacts.",
            "Safety counts may reuse existing metrics.json sidecars when present.",
            "Structural metrics are lightweight heuristics based on canonical text.",
        ],
    }


def render_datasheet_json(summary: dict[str, Any]) -> str:
    return json.dumps(summary, ensure_ascii=False, indent=2)


def render_datasheet_markdown(summary: dict[str, Any]) -> str:
    corpus = summary.get("corpus_overview") or {}
    temporal = summary.get("temporal") or {}
    turn_taking = summary.get("turn_taking") or {}
    safety = summary.get("safety") or {}
    structure = summary.get("structure") or {}
    notes = summary.get("notes") or []

    lines = [
        "# Dataset Summary",
        "",
        "## Corpus Overview",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Total threads | {_format_markdown_value(corpus.get('total_threads'))} |",
        f"| Total messages | {_format_markdown_value(corpus.get('total_messages'))} |",
        f"| User messages | {_format_markdown_value(corpus.get('total_user_messages'))} |",
        f"| Assistant messages | {_format_markdown_value(corpus.get('total_assistant_messages'))} |",
        "",
        "## Temporal Characteristics",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Average thread duration (seconds) | {_format_markdown_value(temporal.get('avg_thread_duration_seconds'))} |",
        f"| Median thread duration (seconds) | {_format_markdown_value(temporal.get('median_thread_duration_seconds'))} |",
        f"| Multi-day threads | {_format_markdown_value(temporal.get('multi_day_thread_count'))} |",
        "",
        "## Turn-Taking Characteristics",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Mean user/assistant character ratio | {_format_markdown_value(turn_taking.get('mean_char_ratio_user_vs_assistant'))} |",
        f"| Median user/assistant character ratio | {_format_markdown_value(turn_taking.get('median_char_ratio_user_vs_assistant'))} |",
        "",
        "## Safety Characteristics",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Threads with refusal | {_format_markdown_value(safety.get('threads_with_refusal'))} |",
        f"| Threads with intervention | {_format_markdown_value(safety.get('threads_with_intervention'))} |",
        "",
        "## Structural Characteristics",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Average blocks per message | {_format_markdown_value(structure.get('avg_blocks_per_message'))} |",
        f"| Threads with code blocks | {_format_markdown_value(structure.get('threads_with_code_blocks'))} |",
        f"| Code-block message ratio | {_format_markdown_value(structure.get('code_block_message_ratio'))} |",
        "",
        "## Notes",
        "",
    ]
    for note in notes:
        lines.append(f"- {note}")
    return "\n".join(lines)
