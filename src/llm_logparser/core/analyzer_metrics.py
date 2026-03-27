from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

from .analyzer_common import (
    detect_header_metadata,
    has_min_normalized_length,
    normalize_analysis_text,
    normalized_similarity,
    normalize_role,
    plan_sidecar_actions,
    render_artifact_json,
    resolve_canonical_text,
    safe_average,
    safe_ratio,
    string_or_none,
    write_json_artifact,
)
from .i18n import _, get_resource_list
from .l1_derivation import discover_parsed_jsonl, iter_parsed_records, ts_to_seconds

# Refusal detection is a normalized substring match against locale-backed cue
# lists in `src/llm_logparser/i18n/{locale}.yaml`, with fallback to `en-US`
# handled by `get_resource_list()`.
REFUSAL_INDICATORS_RESOURCE_KEY = "analysis.refusal.indicators"
INTERVENTION_INDICATORS_RESOURCE_KEY = "analysis.safety.intervention_indicators"
REVISION_CUES_RESOURCE_KEY = "analysis.revision.cues"
CORRECTION_CUES_RESOURCE_KEY = "analysis.correction.cues"
CLARIFICATION_CUES_RESOURCE_KEY = "analysis.clarification.cues"

# Revision heuristics are intentionally simple and local:
# - very short user messages are ignored to reduce false positives from replies
#   like "again" or "no"
# - otherwise a revision triggers if the message contains a revision cue or is
#   sufficiently similar to the previous user message after normalization
REVISION_MIN_NORMALIZED_LENGTH = 8
REVISION_SIMILARITY_THRESHOLD = 0.78
RAPID_REVISION_SECONDS = 60
SESSION_GAP_SECONDS = 3600


class MetricsDependencyError(RuntimeError):
    """Raised when analyze metrics is missing a required prerequisite artifact."""

    code = "LP7100"


def _load_token_stats(parsed_path: Path) -> dict[str, Any]:
    token_stats_path = parsed_path.with_name("token_stats.json")
    if not token_stats_path.exists():
        raise MetricsDependencyError(
            _(
                "runtime.analyze.metrics.missing_token_stats",
                path=token_stats_path,
            )
        )

    try:
        payload = json.loads(token_stats_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {token_stats_path}: {exc.msg}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"invalid token_stats artifact in {token_stats_path}: expected object")
    if payload.get("artifact_type") != "token_stats":
        raise ValueError(f"invalid token_stats artifact in {token_stats_path}: wrong artifact_type")

    return payload


def _require_int(payload: dict[str, Any], path: str) -> int:
    current: Any = payload
    for key in path.split("."):
        if not isinstance(current, dict) or key not in current:
            raise ValueError(f"missing required field in token_stats.json: {path}")
        current = current[key]
    if not isinstance(current, int):
        raise ValueError(f"invalid required field in token_stats.json: {path}")
    return current


def _require_number(payload: dict[str, Any], path: str) -> int | float:
    current: Any = payload
    for key in path.split("."):
        if not isinstance(current, dict) or key not in current:
            raise ValueError(f"missing required field in token_stats.json: {path}")
        current = current[key]
    if not isinstance(current, (int, float)):
        raise ValueError(f"invalid required field in token_stats.json: {path}")
    return current


def _load_diversity_units(token_stats: dict[str, Any], texts: list[str]) -> tuple[int, int]:
    tokenizer_meta = token_stats.get("tokenizer")
    if isinstance(tokenizer_meta, dict):
        if tokenizer_meta.get("library") == "tiktoken":
            resolved_encoding = string_or_none(tokenizer_meta.get("resolved_encoding"))
            if resolved_encoding:
                import tiktoken

                encoder = tiktoken.get_encoding(resolved_encoding)
                total_tokens = 0
                unique_tokens: set[int] = set()
                for text in texts:
                    # Diversity metrics prefer the same tokenizer units as
                    # token_stats.json when available. If that metadata is not
                    # usable, the fallback below degrades to whitespace pieces.
                    encoded = encoder.encode_ordinary(text)
                    total_tokens += len(encoded)
                    unique_tokens.update(encoded)
                return len(unique_tokens), total_tokens

    total_tokens = 0
    unique_tokens: set[str] = set()
    for text in texts:
        pieces = text.split()
        total_tokens += len(pieces)
        unique_tokens.update(pieces)
    return len(unique_tokens), total_tokens


def _load_normalized_phrases(key: str) -> list[str]:
    # Heuristic cue lists are locale-backed, but metrics.json remains a stable
    # machine-readable artifact with English schema keys by design. The i18n
    # layer already applies locale fallback to en-US, so the resulting phrase
    # list is deterministic for a selected locale.
    phrases = get_resource_list(key)
    normalized = []
    for phrase in phrases:
        folded = normalize_analysis_text(phrase)
        if folded:
            normalized.append(folded)
    return normalized


def _load_refusal_indicators() -> list[str]:
    return _load_normalized_phrases(REFUSAL_INDICATORS_RESOURCE_KEY)


def _load_intervention_indicators() -> list[str]:
    return _load_normalized_phrases(INTERVENTION_INDICATORS_RESOURCE_KEY)


def _load_revision_cues() -> list[str]:
    return _load_normalized_phrases(REVISION_CUES_RESOURCE_KEY)


def _load_correction_cues() -> list[str]:
    return _load_normalized_phrases(CORRECTION_CUES_RESOURCE_KEY)


def _load_clarification_cues() -> list[str]:
    return _load_normalized_phrases(CLARIFICATION_CUES_RESOURCE_KEY)


def _matches_phrase(text: str, phrases: list[str]) -> bool:
    """Return True when any normalized cue appears as a substring.

    This is a deliberately transparent heuristic, not an NLP classifier:
    message text and YAML phrases are both normalized with
    `normalize_analysis_text()`, then checked via substring inclusion.
    """
    if not text or not phrases:
        return False
    normalized_text = normalize_analysis_text(text)
    return any(phrase in normalized_text for phrase in phrases)


def _classify_revisions(
    user_texts: list[str],
    revision_cues: list[str],
    correction_cues: list[str],
    clarification_cues: list[str],
) -> dict[str, int]:
    counts = {
        "revision_count": 0,
        "correction_count": 0,
        "clarification_count": 0,
        "retry_count": 0,
    }
    if len(user_texts) < 2:
        return counts

    previous_text = user_texts[0]
    for current_text in user_texts[1:]:
        # Ignore very short candidate revisions to avoid over-counting terse
        # follow-ups that are not meaningful rewrites of the prior request.
        if not has_min_normalized_length(current_text, REVISION_MIN_NORMALIZED_LENGTH):
            previous_text = current_text
            continue

        cue_triggered_revision = _matches_phrase(current_text, revision_cues)
        similarity_triggered_revision = (
            has_min_normalized_length(previous_text, REVISION_MIN_NORMALIZED_LENGTH)
            and normalized_similarity(previous_text, current_text)
            >= REVISION_SIMILARITY_THRESHOLD
        )

        if cue_triggered_revision or similarity_triggered_revision:
            counts["revision_count"] += 1
            # Subtype precedence is intentional: explicit correction beats
            # clarification, and anything else falls back to a generic retry.
            if _matches_phrase(current_text, correction_cues):
                counts["correction_count"] += 1
            elif _matches_phrase(current_text, clarification_cues):
                counts["clarification_count"] += 1
            else:
                counts["retry_count"] += 1

        previous_text = current_text

    return counts


def _classify_safety_interventions(
    assistant_texts: list[str],
    refusal_indicators: list[str],
    intervention_indicators: list[str],
) -> dict[str, Any]:
    refusal_count = 0
    intervention_count = 0
    trigger_types = {
        "refusal": 0,
        "caveat": 0,
    }

    for text in assistant_texts:
        refusal_matched = _matches_phrase(text, refusal_indicators)
        caveat_matched = _matches_phrase(text, intervention_indicators)
        if refusal_matched:
            refusal_count += 1
            trigger_types["refusal"] += 1
        if caveat_matched:
            trigger_types["caveat"] += 1
        if refusal_matched or caveat_matched:
            intervention_count += 1

    return {
        "refusal_count": refusal_count,
        "intervention_count": intervention_count,
        "trigger_types": trigger_types,
    }


def _timestamp_seconds(value: Any) -> float | None:
    numeric_seconds = ts_to_seconds(value)
    if numeric_seconds is not None:
        return numeric_seconds
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _round_seconds(value: float | None) -> int | float | None:
    if value is None:
        return None
    rounded = round(value, 2)
    if rounded.is_integer():
        return int(rounded)
    return rounded


def compute_user_effort(messages: list[dict[str, Any]]) -> dict[str, Any]:
    total_user_characters = 0
    total_assistant_characters = 0
    rapid_revisions = 0
    negative_deltas = 0
    read_time_samples: list[float] = []
    excluded_long_gaps = 0
    previous_message: dict[str, Any] | None = None

    for message in messages:
        role = normalize_role(message.get("role"))
        text = message.get("text")
        char_count = len(text) if isinstance(text, str) else 0

        if role == "user":
            total_user_characters += char_count
        elif role == "assistant":
            total_assistant_characters += char_count

        if previous_message is not None:
            previous_role = normalize_role(previous_message.get("role"))
            if previous_role == "assistant" and role == "user":
                previous_ts = _timestamp_seconds(previous_message.get("ts"))
                current_ts = _timestamp_seconds(message.get("ts"))
                if previous_ts is not None and current_ts is not None:
                    delta_seconds = current_ts - previous_ts
                    if delta_seconds < 0:
                        negative_deltas += 1
                        previous_message = message
                        continue
                    if delta_seconds < RAPID_REVISION_SECONDS:
                        rapid_revisions += 1
                    if delta_seconds <= SESSION_GAP_SECONDS:
                        read_time_samples.append(delta_seconds)
                    else:
                        excluded_long_gaps += 1

        previous_message = message

    response_length_ratio = None
    if total_user_characters:
        response_length_ratio = round(
            total_assistant_characters / total_user_characters,
            4,
        )

    if read_time_samples:
        avg_seconds = _round_seconds(sum(read_time_samples) / len(read_time_samples))
        median_seconds = _round_seconds(median(read_time_samples))
        min_seconds = _round_seconds(min(read_time_samples))
        max_seconds = _round_seconds(max(read_time_samples))
    else:
        avg_seconds = None
        median_seconds = None
        min_seconds = None
        max_seconds = None

    return {
        "rapid_revisions": rapid_revisions,
        "response_length_ratio": response_length_ratio,
        "negative_deltas": negative_deltas,
        "human_read_time": {
            "avg_seconds": avg_seconds,
            "median_seconds": median_seconds,
            "min_seconds": min_seconds,
            "max_seconds": max_seconds,
            "sample_count": len(read_time_samples),
            "excluded_long_gaps": excluded_long_gaps,
            "session_gap_seconds": SESSION_GAP_SECONDS,
        },
    }


def build_metrics_artifact(parsed_path: Path) -> dict[str, Any]:
    token_stats = _load_token_stats(parsed_path)
    provider_id, conversation_id = detect_header_metadata(parsed_path)
    refusal_indicators = _load_refusal_indicators()
    intervention_indicators = _load_intervention_indicators()
    revision_cues = _load_revision_cues()
    correction_cues = _load_correction_cues()
    clarification_cues = _load_clarification_cues()

    char_count_total = 0
    char_count_user = 0
    char_count_assistant = 0
    texts: list[str] = []
    user_texts: list[str] = []
    assistant_texts: list[str] = []
    effort_messages: list[dict[str, Any]] = []
    message_count_user = 0
    message_count_assistant = 0

    for row in iter_parsed_records(parsed_path):
        if row.get("record_type") != "message":
            continue

        if provider_id is None:
            provider_id = string_or_none(row.get("provider_id"))
        if conversation_id is None:
            conversation_id = string_or_none(row.get("conversation_id"))

        text, _text_source = resolve_canonical_text(row)
        texts.append(text)
        effort_messages.append(
            {
                "role": row.get("role"),
                "ts": row.get("ts"),
                "text": text,
            }
        )
        char_count = len(text)
        char_count_total += char_count

        role = normalize_role(row.get("role"))
        if role == "user":
            message_count_user += 1
            char_count_user += char_count
            user_texts.append(text)
        elif role == "assistant":
            message_count_assistant += 1
            char_count_assistant += char_count
            assistant_texts.append(text)

    if conversation_id is None:
        raise ValueError(f"parsed thread has no conversation_id: {parsed_path}")

    token_total = _require_int(token_stats, "summary.tokens_total")
    turn_count = _require_int(token_stats, "summary.turn_count")
    token_count_user = _require_int(token_stats, "summary.tokens_user")
    token_count_assistant = _require_int(token_stats, "summary.tokens_assistant")
    message_count_total = _require_int(token_stats, "summary.message_count")
    avg_tokens_per_message = _require_number(token_stats, "summary.avg_tokens_per_message")
    avg_tokens_per_turn = _require_number(token_stats, "summary.avg_tokens_per_turn")

    unique_token_count, diversity_token_total = _load_diversity_units(token_stats, texts)
    interaction_counts = _classify_revisions(
        user_texts,
        revision_cues,
        correction_cues,
        clarification_cues,
    )
    revision_count = interaction_counts["revision_count"]
    user_effort = compute_user_effort(effort_messages)
    safety_counts = _classify_safety_interventions(
        assistant_texts,
        refusal_indicators,
        intervention_indicators,
    )

    artifact = {
        "artifact_type": "metrics",
        "schema_version": "1.0",
        "provider_id": provider_id or string_or_none(token_stats.get("provider_id")) or "unknown",
        "conversation_id": conversation_id,
        "ratios": {
            # Ratios use deterministic zero-division handling from
            # `safe_ratio()`, so empty assistant/user counts emit 0.0.
            "prompt_response_ratio_tokens": safe_ratio(
                token_count_user,
                token_count_assistant,
            ),
            "prompt_response_ratio_chars": safe_ratio(
                char_count_user,
                char_count_assistant,
            ),
            "assistant_to_user_ratio": safe_ratio(
                message_count_assistant,
                message_count_user,
            ),
        },
        "tokens": {
            "total": token_total,
            "avg_per_message": avg_tokens_per_message,
            "avg_per_turn": avg_tokens_per_turn,
        },
        "characters": {
            "total": char_count_total,
            "user": char_count_user,
            "assistant": char_count_assistant,
            "avg_per_message": safe_average(char_count_total, message_count_total),
            "avg_per_turn": safe_average(char_count_total, turn_count),
        },
        "distribution": {
            "message_total": message_count_total,
            "message_user": message_count_user,
            "message_assistant": message_count_assistant,
            "messages_per_turn": safe_average(message_count_total, turn_count),
        },
        "diversity": {
            "type_token_ratio": safe_ratio(unique_token_count, diversity_token_total),
            "unique_token_ratio": safe_ratio(unique_token_count, diversity_token_total),
        },
        "safety": {
            "refusal_count": safety_counts["refusal_count"],
            "refusal_rate": safe_ratio(
                safety_counts["refusal_count"],
                message_count_assistant,
            ),
            "intervention_count": safety_counts["intervention_count"],
            "intervention_rate": safe_ratio(
                safety_counts["intervention_count"],
                message_count_assistant,
            ),
            "trigger_types": safety_counts["trigger_types"],
        },
        "interaction": {
            "revision_count": revision_count,
            "revision_rate": safe_ratio(revision_count, message_count_user),
            "correction_count": interaction_counts["correction_count"],
            "correction_rate": safe_ratio(
                interaction_counts["correction_count"],
                message_count_user,
            ),
            "clarification_count": interaction_counts["clarification_count"],
            "clarification_rate": safe_ratio(
                interaction_counts["clarification_count"],
                message_count_user,
            ),
            "retry_count": interaction_counts["retry_count"],
            "retry_rate": safe_ratio(
                interaction_counts["retry_count"],
                message_count_user,
            ),
        },
        "user_effort": user_effort,
    }
    return artifact


def render_metrics_json(artifact: dict[str, Any]) -> str:
    return render_artifact_json(artifact)


def write_metrics_artifact(parsed_path: Path, artifact: dict[str, Any]) -> Path:
    artifact_path = parsed_path.with_name("metrics.json")
    return write_json_artifact(artifact_path, artifact)


def metrics_artifact_path(parsed_path: Path) -> Path:
    return parsed_path.with_name("metrics.json")


def analyze_metrics(
    input_path: Path,
    *,
    skip_existing: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    parsed_files = discover_parsed_jsonl(input_path)
    plan = plan_sidecar_actions(
        parsed_files,
        metrics_artifact_path,
        skip_existing=skip_existing,
    )
    written_artifacts: list[Path] = []

    for parsed_path, artifact_path in plan["planned_actions"]:
        artifact = build_metrics_artifact(parsed_path)
        if dry_run:
            continue
        written_artifacts.append(write_json_artifact(artifact_path, artifact))

    return {
        "threads": len(written_artifacts),
        "artifacts": written_artifacts,
        "detected_threads": plan["detected_threads"],
        "existing_threads": plan["existing_threads"],
        "new_threads": plan["new_threads"],
        "rebuild_threads": plan["rebuild_threads"],
        "skipped_threads": plan["skipped_threads"],
        "skipped_artifacts": plan["skipped_artifacts"],
        "dry_run": dry_run,
    }
