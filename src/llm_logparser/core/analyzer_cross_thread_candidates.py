from __future__ import annotations

import bisect
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .analyzer_common import (
    normalize_analysis_text,
    normalized_similarity,
    write_json_artifact,
)
from .l1_derivation import iter_input_message_records
from .analyzer_semantic_preview import WindowPreviewRecord, load_window_preview_index
from .embedding_backend import create_embedding_backend
from .schema_validation import load_cross_thread_candidate_validator, load_topics_validator

CROSS_THREAD_CANDIDATE_SCHEMA_VERSION = "0.3"
CROSS_THREAD_CANDIDATE_RECORD_TYPE = "cross_thread_candidate"
CROSS_THREAD_CANDIDATE_SUMMARY_ARTIFACT_TYPE = "cross_thread_candidates_summary"
DEFAULT_CROSS_THREAD_MIN_SCORE = 0.6
DEFAULT_CROSS_THREAD_TOP_PER_SOURCE = 3
_DAY_MS = 24 * 60 * 60 * 1000

_LABEL_MATCH_SCORE = 0.45
_RAW_LABEL_MATCH_SCORE = 0.15
_KEYWORD_OVERLAP_LOW_SCORE = 0.1
_KEYWORD_OVERLAP_HIGH_SCORE = 0.2
_TOPIC_LABEL_SIMILARITY_MEDIUM_SCORE = 0.12
_TOPIC_LABEL_SIMILARITY_HIGH_SCORE = 0.2
_EXCERPT_SIMILARITY_LOW_SCORE = 0.12
_EXCERPT_SIMILARITY_MEDIUM_SCORE = 0.2
_EXCERPT_SIMILARITY_HIGH_SCORE = 0.3
_TOPIC_EXCERPT_COMBINATION_HIGH_SCORE = 0.09
# Modest recurrence preference: enough to separate temporally distant revisits
# before top-k pruning, but not enough to overpower semantic similarity.
_TIMESTAMP_DISTANCE_MEDIUM_THRESHOLD_MS = 2 * _DAY_MS
_TIMESTAMP_DISTANCE_HIGH_THRESHOLD_MS = 7 * _DAY_MS
_TIMESTAMP_DISTANCE_MEDIUM_SCORE = 0.08
_TIMESTAMP_DISTANCE_HIGH_SCORE = 0.15
_ARTIFACT_WRAPPER_MARKERS = (
    "gpt-4o returned",
    "returned 1 image",
    "returned 1 images",
)
_TURN_CONTROL_MARKERS = (
    "please end this turn now",
    "end this turn now",
    "do not say or show anything",
    "do not summarize",
    "do not ask follow-up",
)
_SPECIFICITY_TOKEN_RE = re.compile(r"[a-z0-9_./:-]{3,}|[一-龯ぁ-んァ-ヶー]{2,}", re.IGNORECASE)
_SPECIFICITY_GENERIC_TOKENS = frozenset(
    {
        "this",
        "that",
        "with",
        "from",
        "about",
        "there",
        "their",
        "your",
        "please",
        "thank",
        "thanks",
        "think",
        "really",
        "maybe",
        "would",
        "could",
        "should",
        "hello",
        "today",
        "thing",
        "stuff",
        "okay",
        "おはよう",
        "こんにちは",
        "こんばんは",
        "ありがとう",
        "了解",
        "はい",
        "うん",
        "それ",
        "これ",
        "あれ",
        "ここ",
        "そこ",
        "やね",
        "やで",
        "ほんま",
        "なるほど",
        "感じ",
    }
)
# Continuity masking is instrumentation only. With fewer than ~12 intervening
# messages or fewer than ~2 intervening representative spans, the pair is
# likely ongoing local continuity rather than reactivated recall.
_CONTINUITY_VOLUME_GAP_MESSAGE_THRESHOLD = 12
_CONTINUITY_VOLUME_GAP_SPAN_THRESHOLD = 2
_DORMANCY_MESSAGE_SCALE = 64
_DORMANCY_SPAN_SCALE = 8
_WEAK_RECURRENCE_TEMPORAL_GAP_SECONDS = 6 * 60 * 60
_WEAK_RECURRENCE_SPECIFICITY_THRESHOLD = 0.45
_WEAK_RECURRENCE_LOCAL_CONTEXT_DELTA_THRESHOLD = 0.12
_ANCHOR_TOKEN_SYMBOLS = frozenset("/._:-")


class CrossThreadCandidateError(RuntimeError):
    pass


@dataclass(frozen=True)
class _RepresentativeSpanUnit:
    provider_id: str
    topic_id: str
    conversation_id: str
    span_id: str
    message_ids: tuple[str, ...]
    source_window_ids: tuple[str, ...]
    excerpt: str
    topic_label: str | None
    keywords: tuple[str, ...]
    normalized_label: str | None
    raw_label: str | None
    first_seen: int | None
    last_seen: int | None
    excerpt_specificity: float
    anchor_tokens: tuple[str, ...]


@dataclass(frozen=True)
class _Evidence:
    score: float
    reason_codes: tuple[str, ...]
    excerpt_similarity: float
    topic_label_similarity: float
    shared_keywords: tuple[str, ...]
    normalized_label_match: bool
    raw_label_match: bool
    timestamp_delta_ms: int | None
    volume_gap: int | None
    temporal_gap_seconds: int | None
    continuity_mask: bool
    dormancy_score: float
    specificity_score: float
    local_context_delta: float | None


@dataclass(frozen=True)
class _RecurrenceInstrumentationContext:
    message_timestamps: tuple[int, ...]
    span_timestamps: tuple[int, ...]
    previous_target_by_key: dict[tuple[str, str], _RepresentativeSpanUnit]


@dataclass(frozen=True)
class _VolumeGap:
    value: int | None
    unit: str | None


@dataclass(frozen=True)
class _PairSignals:
    excerpt_similarity: float
    topic_label_similarity: float
    shared_keywords: tuple[str, ...]
    normalized_label_match: bool
    raw_label_match: bool
    timestamp_delta_ms: int | None
    temporal_gap_seconds: int | None
    volume_gap: _VolumeGap
    continuity_mask: bool
    dormancy_score: float
    specificity_score: float
    local_context_delta: float | None
    shared_anchor_tokens: tuple[str, ...]


@dataclass(frozen=True)
class _WeakRecurrenceCandidate:
    evidence: _Evidence
    shared_anchor_count: int


def cross_thread_candidates_path(input_root: Path) -> Path:
    return input_root / "l3" / "cross-thread-candidates" / "candidates.jsonl"


def _load_topics_artifact(input_root: Path) -> dict[str, Any]:
    path = input_root / "l3" / "semantic-topics" / "topics.json"
    if not path.exists():
        raise CrossThreadCandidateError(
            f"semantic-topics artifact not found: {path}"
        )
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CrossThreadCandidateError(
            f"invalid JSON in {path}: {exc.msg}"
        ) from exc
    if not isinstance(artifact, dict):
        raise CrossThreadCandidateError(
            f"invalid topics artifact in {path}: expected object"
        )
    errors = list(load_topics_validator().iter_errors(artifact))
    if errors:
        raise CrossThreadCandidateError(
            f"topics schema validation failed for {path}: {errors[0].message}"
        )
    return artifact


def _normalized_keywords(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized = [
        normalize_analysis_text(value)
        for value in values
        if isinstance(value, str) and value.strip()
    ]
    return tuple(value for value in normalized if value)


def _text_specificity_score(text: str) -> float:
    normalized = normalize_analysis_text(text)
    tokens = [token for token in _SPECIFICITY_TOKEN_RE.findall(normalized) if token]
    if not tokens:
        return 0.0
    unique_ratio = len(set(tokens)) / len(tokens)
    content_tokens = [
        token
        for token in tokens
        if token not in _SPECIFICITY_GENERIC_TOKENS
    ]
    content_ratio = len(content_tokens) / len(tokens)
    long_content_ratio = (
        sum(
            1
            for token in content_tokens
            if len(token) >= 6 or any(char.isdigit() for char in token)
        )
        / len(tokens)
    )
    content_length_factor = min(
        1.0,
        sum(len(token) for token in content_tokens) / 80.0,
    )
    return round(
        min(
            1.0,
            0.3 * content_ratio
            + 0.25 * unique_ratio
            + 0.25 * long_content_ratio
            + 0.2 * content_length_factor,
        ),
        4,
    )


def _is_anchor_like_token(token: str) -> bool:
    if token in _SPECIFICITY_GENERIC_TOKENS or len(token) < 4:
        return False
    if any(char.isdigit() for char in token):
        return True
    if any(char in _ANCHOR_TOKEN_SYMBOLS for char in token):
        return True
    has_cjk = bool(re.search(r"[一-龯ぁ-んァ-ヶー]", token))
    if has_cjk:
        return len(token) >= 4
    return len(token) >= 7


def _anchor_tokens_for_texts(values: tuple[str, ...]) -> tuple[str, ...]:
    tokens: set[str] = set()
    for value in values:
        normalized = normalize_analysis_text(value)
        for token in _SPECIFICITY_TOKEN_RE.findall(normalized):
            if _is_anchor_like_token(token):
                tokens.add(token)
    return tuple(sorted(tokens))


def _representative_units(topics_artifact: dict[str, Any]) -> list[_RepresentativeSpanUnit]:
    provider_id = str(topics_artifact["provider_id"])
    units: list[_RepresentativeSpanUnit] = []
    for topic in topics_artifact["topics"]:
        topic_id = str(topic["topic_id"])
        first_seen = topic.get("first_seen")
        topic_first_seen = first_seen if isinstance(first_seen, int) else None
        last_seen = topic.get("last_seen")
        topic_last_seen = last_seen if isinstance(last_seen, int) else None
        topic_label = topic.get("label")
        normalized_topic_label = (
            " ".join(str(topic_label).split()) if isinstance(topic_label, str) and topic_label.strip() else None
        )
        keywords = tuple(
            str(keyword)
            for keyword in topic.get("keywords", [])
            if isinstance(keyword, str) and keyword.strip()
        )
        for span in topic["representative_spans"]:
            normalization = span.get("semantic_normalization")
            normalized_label = None
            raw_label = None
            if isinstance(normalization, dict):
                raw = normalization.get("raw_label")
                normalized = normalization.get("normalized_label")
                raw_label = str(raw) if isinstance(raw, str) and raw else None
                normalized_label = (
                    str(normalized) if isinstance(normalized, str) and normalized else None
                )
            source_window_ids = tuple(
                str(window_id)
                for window_id in span.get("source_window_ids", [])
                if isinstance(window_id, str) and window_id
            )
            if not source_window_ids:
                window_id = span.get("window_id")
                if isinstance(window_id, str) and window_id:
                    source_window_ids = (window_id,)
            units.append(
                _RepresentativeSpanUnit(
                    provider_id=provider_id,
                    topic_id=topic_id,
                    conversation_id=str(span["conversation_id"]),
                    span_id=str(span["span_id"]),
                    message_ids=tuple(str(message_id) for message_id in span["message_ids"]),
                    source_window_ids=source_window_ids,
                    excerpt=str(span["excerpt"]),
                    topic_label=normalized_topic_label,
                    keywords=keywords,
                    normalized_label=normalized_label,
                    raw_label=raw_label,
                    first_seen=topic_first_seen,
                    last_seen=topic_last_seen,
                    excerpt_specificity=_text_specificity_score(str(span["excerpt"])),
                    anchor_tokens=_anchor_tokens_for_texts(
                        tuple(
                            value
                            for value in (
                                normalized_topic_label or "",
                                str(span["excerpt"]),
                                *keywords,
                            )
                            if value
                        )
                    ),
                )
            )
    units.sort(
        key=lambda item: (
            item.conversation_id,
            item.topic_id,
            item.span_id,
        )
    )
    return units


def _build_recurrence_instrumentation_context(
    input_root: Path,
    units: list[_RepresentativeSpanUnit],
) -> _RecurrenceInstrumentationContext:
    message_timestamps: list[int] = []
    try:
        for row in iter_input_message_records(input_root):
            ts = row.get("ts")
            if isinstance(ts, int):
                message_timestamps.append(ts)
    except (FileNotFoundError, ValueError):
        message_timestamps = []
    message_timestamps.sort()

    span_timestamps = sorted(
        unit.first_seen
        for unit in units
        if unit.first_seen is not None
    )
    previous_target_by_key: dict[tuple[str, str], _RepresentativeSpanUnit] = {}
    units_by_conversation: dict[str, list[_RepresentativeSpanUnit]] = {}
    for unit in units:
        if unit.first_seen is None:
            continue
        units_by_conversation.setdefault(unit.conversation_id, []).append(unit)
    for conversation_units in units_by_conversation.values():
        conversation_units.sort(
            key=lambda item: (
                item.first_seen if item.first_seen is not None else -1,
                item.last_seen if item.last_seen is not None else -1,
                item.topic_id,
                item.span_id,
            )
        )
        previous: _RepresentativeSpanUnit | None = None
        for unit in conversation_units:
            previous_target_by_key[(unit.conversation_id, unit.span_id)] = previous
            previous = unit

    return _RecurrenceInstrumentationContext(
        message_timestamps=tuple(message_timestamps),
        span_timestamps=tuple(span_timestamps),
        previous_target_by_key=previous_target_by_key,
    )


def _volume_gap(
    source: _RepresentativeSpanUnit,
    target: _RepresentativeSpanUnit,
    context: _RecurrenceInstrumentationContext,
) -> _VolumeGap:
    if source.first_seen is None or target.first_seen is None:
        return _VolumeGap(value=None, unit=None)

    earlier, later = sorted(
        (source, target),
        key=lambda item: (
            item.first_seen if item.first_seen is not None else -1,
            item.last_seen if item.last_seen is not None else -1,
            item.conversation_id,
            item.span_id,
        ),
    )
    earlier_end = (
        earlier.last_seen
        if earlier.last_seen is not None
        else earlier.first_seen
    )
    later_start = later.first_seen
    if earlier_end is None or later_start is None:
        return _VolumeGap(value=None, unit=None)

    # This currently counts all intervening canonical messages under the input
    # root, not only messages from the two conversations being compared. It is
    # therefore a coarse dormancy proxy and may include unrelated conversations.
    if context.message_timestamps:
        start = bisect.bisect_right(context.message_timestamps, earlier_end)
        end = bisect.bisect_left(context.message_timestamps, later_start)
        return _VolumeGap(value=max(0, end - start), unit="message")

    if context.span_timestamps:
        start = bisect.bisect_right(context.span_timestamps, earlier_end)
        end = bisect.bisect_left(context.span_timestamps, later_start)
        return _VolumeGap(value=max(0, end - start), unit="span")

    return _VolumeGap(value=None, unit=None)


def _continuity_mask(volume_gap: _VolumeGap) -> bool:
    if volume_gap.value is None or volume_gap.unit is None:
        return False
    if volume_gap.unit == "message":
        return volume_gap.value <= _CONTINUITY_VOLUME_GAP_MESSAGE_THRESHOLD
    return volume_gap.value <= _CONTINUITY_VOLUME_GAP_SPAN_THRESHOLD


def _dormancy_score(volume_gap: _VolumeGap) -> float:
    if volume_gap.value is None or volume_gap.unit is None:
        return 0.0
    scale = (
        _DORMANCY_MESSAGE_SCALE
        if volume_gap.unit == "message"
        else _DORMANCY_SPAN_SCALE
    )
    if scale <= 1:
        return 0.0
    return round(
        min(1.0, math.log1p(volume_gap.value) / math.log1p(scale)),
        4,
    )


def _local_context_delta(
    *,
    source: _RepresentativeSpanUnit,
    target: _RepresentativeSpanUnit,
    excerpt_similarity: float,
    topic_label_similarity: float,
    context: _RecurrenceInstrumentationContext,
) -> float | None:
    # This is a lightweight proxy for local context re-entry. It only compares
    # the current target against the immediately previous representative span in
    # the target conversation; it does not yet model fuller local context windows.
    previous_target = context.previous_target_by_key.get((target.conversation_id, target.span_id))
    if previous_target is None:
        return None
    prior_excerpt_similarity = normalized_similarity(source.excerpt, previous_target.excerpt)
    prior_topic_label_similarity = normalized_similarity(
        source.topic_label or "",
        previous_target.topic_label or "",
    )
    current_signal = max(excerpt_similarity, topic_label_similarity)
    prior_signal = max(round(prior_excerpt_similarity, 4), round(prior_topic_label_similarity, 4))
    return round(max(0.0, current_signal - prior_signal), 4)


def _intervening_temporal_gap_seconds(
    source: _RepresentativeSpanUnit,
    target: _RepresentativeSpanUnit,
) -> int | None:
    if source.first_seen is None or target.first_seen is None:
        return None

    earlier, later = sorted(
        (source, target),
        key=lambda item: (
            item.first_seen if item.first_seen is not None else -1,
            item.last_seen if item.last_seen is not None else -1,
            item.conversation_id,
            item.span_id,
        ),
    )
    earlier_end = earlier.last_seen if earlier.last_seen is not None else earlier.first_seen
    later_start = later.first_seen
    if earlier_end is None or later_start is None:
        return None

    # We align this with volume_gap semantics by measuring the intervening time
    # window from the earlier span end to the later span start. When an explicit
    # span end is unavailable, last_seen falls back to the best available
    # approximation (effectively max source timestamp / min target timestamp).
    gap_ms = max(0, later_start - earlier_end)
    return int(math.ceil(gap_ms / 1000.0))


def _pair_signals(
    source: _RepresentativeSpanUnit,
    target: _RepresentativeSpanUnit,
    *,
    recurrence_context: _RecurrenceInstrumentationContext,
) -> _PairSignals:
    excerpt_similarity = round(
        normalized_similarity(source.excerpt, target.excerpt),
        4,
    )
    topic_label_similarity = round(
        normalized_similarity(source.topic_label or "", target.topic_label or ""),
        4,
    )
    source_keywords = set(_normalized_keywords(source.keywords))
    target_keywords = set(_normalized_keywords(target.keywords))
    shared_keywords = tuple(sorted(source_keywords & target_keywords))
    normalized_label_match = (
        source.normalized_label is not None
        and target.normalized_label is not None
        and source.normalized_label == target.normalized_label
    )
    raw_label_match = (
        source.raw_label is not None
        and target.raw_label is not None
        and source.raw_label == target.raw_label
    )
    timestamp_delta_ms: int | None = None
    if source.first_seen is not None and target.first_seen is not None:
        timestamp_delta_ms = abs(source.first_seen - target.first_seen)
    temporal_gap_seconds = _intervening_temporal_gap_seconds(source, target)
    volume_gap = _volume_gap(source, target, recurrence_context)
    continuity_mask = _continuity_mask(volume_gap)
    dormancy_score = _dormancy_score(volume_gap)
    specificity_score = round(
        (source.excerpt_specificity + target.excerpt_specificity) / 2.0,
        4,
    )
    local_context_delta = _local_context_delta(
        source=source,
        target=target,
        excerpt_similarity=excerpt_similarity,
        topic_label_similarity=topic_label_similarity,
        context=recurrence_context,
    )
    shared_anchor_tokens = tuple(
        sorted(set(source.anchor_tokens) & set(target.anchor_tokens))
    )
    return _PairSignals(
        excerpt_similarity=excerpt_similarity,
        topic_label_similarity=topic_label_similarity,
        shared_keywords=shared_keywords,
        normalized_label_match=normalized_label_match,
        raw_label_match=raw_label_match,
        timestamp_delta_ms=timestamp_delta_ms,
        temporal_gap_seconds=temporal_gap_seconds,
        volume_gap=volume_gap,
        continuity_mask=continuity_mask,
        dormancy_score=dormancy_score,
        specificity_score=specificity_score,
        local_context_delta=local_context_delta,
        shared_anchor_tokens=shared_anchor_tokens,
    )


def _evidence_from_signals(
    *,
    signals: _PairSignals,
    score: float,
    reason_codes: tuple[str, ...],
) -> _Evidence:
    return _Evidence(
        score=round(min(score, 1.0), 4),
        reason_codes=reason_codes,
        excerpt_similarity=signals.excerpt_similarity,
        topic_label_similarity=signals.topic_label_similarity,
        shared_keywords=signals.shared_keywords,
        normalized_label_match=signals.normalized_label_match,
        raw_label_match=signals.raw_label_match,
        timestamp_delta_ms=signals.timestamp_delta_ms,
        volume_gap=signals.volume_gap.value,
        temporal_gap_seconds=signals.temporal_gap_seconds,
        continuity_mask=signals.continuity_mask,
        dormancy_score=signals.dormancy_score,
        specificity_score=signals.specificity_score,
        local_context_delta=signals.local_context_delta,
    )


def _dedupe_reason_codes(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _similarity_score_and_reasons(
    signals: _PairSignals,
) -> tuple[float, tuple[str, ...], bool]:
    score = 0.0
    reason_codes: list[str] = []
    if signals.normalized_label_match:
        score += _LABEL_MATCH_SCORE
        reason_codes.append("normalized_label_match")
    if signals.raw_label_match:
        score += _RAW_LABEL_MATCH_SCORE
        reason_codes.append("raw_label_match")

    if len(signals.shared_keywords) >= 2:
        score += _KEYWORD_OVERLAP_HIGH_SCORE
        reason_codes.append("shared_keywords_high")
    elif len(signals.shared_keywords) == 1:
        score += _KEYWORD_OVERLAP_LOW_SCORE
        reason_codes.append("shared_keywords_low")

    if signals.topic_label_similarity >= 0.88:
        score += _TOPIC_LABEL_SIMILARITY_HIGH_SCORE
        reason_codes.append("topic_label_similarity_high")
    elif signals.topic_label_similarity >= 0.72:
        score += _TOPIC_LABEL_SIMILARITY_MEDIUM_SCORE
        reason_codes.append("topic_label_similarity_medium")

    if signals.excerpt_similarity >= 0.78:
        score += _EXCERPT_SIMILARITY_HIGH_SCORE
        reason_codes.append("excerpt_similarity_high")
    elif signals.excerpt_similarity >= 0.64:
        score += _EXCERPT_SIMILARITY_MEDIUM_SCORE
        reason_codes.append("excerpt_similarity_medium")
    elif signals.excerpt_similarity >= 0.52:
        score += _EXCERPT_SIMILARITY_LOW_SCORE
        reason_codes.append("excerpt_similarity_low")

    if (
        signals.topic_label_similarity >= 0.88
        and signals.excerpt_similarity >= 0.78
    ):
        score += _TOPIC_EXCERPT_COMBINATION_HIGH_SCORE
        reason_codes.append("topic_excerpt_combination_high")

    if signals.timestamp_delta_ms is not None:
        if signals.timestamp_delta_ms >= _TIMESTAMP_DISTANCE_HIGH_THRESHOLD_MS:
            score += _TIMESTAMP_DISTANCE_HIGH_SCORE
            reason_codes.append("timestamp_distance_high")
        elif signals.timestamp_delta_ms >= _TIMESTAMP_DISTANCE_MEDIUM_THRESHOLD_MS:
            score += _TIMESTAMP_DISTANCE_MEDIUM_SCORE
            reason_codes.append("timestamp_distance_medium")

    has_strong_signal = (
        signals.excerpt_similarity >= 0.52
        or signals.topic_label_similarity >= 0.72
        or len(signals.shared_keywords) >= 1
    )
    return score, _dedupe_reason_codes(reason_codes), has_strong_signal


def _weak_recurrence_evidence_for_pair(
    source: _RepresentativeSpanUnit,
    target: _RepresentativeSpanUnit,
    *,
    recurrence_context: _RecurrenceInstrumentationContext,
    base_evidence: _Evidence | None = None,
) -> _WeakRecurrenceCandidate | None:
    signals = _pair_signals(
        source,
        target,
        recurrence_context=recurrence_context,
    )
    if not signals.shared_anchor_tokens:
        return None

    has_meaningful_separation = (
        signals.volume_gap.value is not None
        and signals.volume_gap.unit is not None
        and not signals.continuity_mask
    ) or (
        signals.temporal_gap_seconds is not None
        and signals.temporal_gap_seconds >= _WEAK_RECURRENCE_TEMPORAL_GAP_SECONDS
    )
    if not has_meaningful_separation:
        return None

    has_taskish_signal = (
        signals.specificity_score >= _WEAK_RECURRENCE_SPECIFICITY_THRESHOLD
        or (
            signals.local_context_delta is not None
            and signals.local_context_delta >= _WEAK_RECURRENCE_LOCAL_CONTEXT_DELTA_THRESHOLD
        )
    )
    if not has_taskish_signal:
        return None

    score, base_reason_codes, _has_strong_signal = _similarity_score_and_reasons(signals)
    weak_reason_codes = [
        (
            "weak_recurrence_anchor_overlap_high"
            if len(signals.shared_anchor_tokens) >= 2
            else "weak_recurrence_anchor_overlap"
        ),
        "weak_recurrence_dormant",
    ]
    if signals.specificity_score >= _WEAK_RECURRENCE_SPECIFICITY_THRESHOLD:
        weak_reason_codes.append("weak_recurrence_specificity")
    if (
        signals.local_context_delta is not None
        and signals.local_context_delta >= _WEAK_RECURRENCE_LOCAL_CONTEXT_DELTA_THRESHOLD
    ):
        weak_reason_codes.append("weak_recurrence_context_jump")

    if base_evidence is not None:
        evidence = _Evidence(
            score=base_evidence.score,
            reason_codes=_dedupe_reason_codes(
                list(base_evidence.reason_codes) + weak_reason_codes
            ),
            excerpt_similarity=base_evidence.excerpt_similarity,
            topic_label_similarity=base_evidence.topic_label_similarity,
            shared_keywords=base_evidence.shared_keywords,
            normalized_label_match=base_evidence.normalized_label_match,
            raw_label_match=base_evidence.raw_label_match,
            timestamp_delta_ms=base_evidence.timestamp_delta_ms,
            volume_gap=base_evidence.volume_gap,
            temporal_gap_seconds=base_evidence.temporal_gap_seconds,
            continuity_mask=base_evidence.continuity_mask,
            dormancy_score=base_evidence.dormancy_score,
            specificity_score=base_evidence.specificity_score,
            local_context_delta=base_evidence.local_context_delta,
        )
    else:
        evidence = _evidence_from_signals(
            signals=signals,
            score=score,
            reason_codes=_dedupe_reason_codes(
                list(base_reason_codes) + weak_reason_codes
            ),
        )

    return _WeakRecurrenceCandidate(
        evidence=evidence,
        shared_anchor_count=len(signals.shared_anchor_tokens),
    )


def _evidence_for_pair(
    source: _RepresentativeSpanUnit,
    target: _RepresentativeSpanUnit,
    *,
    recurrence_context: _RecurrenceInstrumentationContext,
) -> _Evidence | None:
    signals = _pair_signals(
        source,
        target,
        recurrence_context=recurrence_context,
    )
    score, reason_codes, has_strong_signal = _similarity_score_and_reasons(signals)
    if not has_strong_signal or not reason_codes:
        return None
    return _evidence_from_signals(
        signals=signals,
        score=score,
        reason_codes=reason_codes,
    )


def _candidate_row(
    *,
    source: _RepresentativeSpanUnit,
    target: _RepresentativeSpanUnit,
    evidence: _Evidence,
    rank: int,
    embedding_similarity: float | None = None,
) -> dict[str, Any]:
    row = {
        "record_type": CROSS_THREAD_CANDIDATE_RECORD_TYPE,
        "schema_version": CROSS_THREAD_CANDIDATE_SCHEMA_VERSION,
        "provider_id": source.provider_id,
        "source_conversation_id": source.conversation_id,
        "target_conversation_id": target.conversation_id,
        "source_topic_id": source.topic_id,
        "target_topic_id": target.topic_id,
        "source_span_id": source.span_id,
        "target_span_id": target.span_id,
        "source_message_ids": list(source.message_ids),
        "target_message_ids": list(target.message_ids),
        "source_excerpt": source.excerpt,
        "target_excerpt": target.excerpt,
        "source_topic_label": source.topic_label,
        "target_topic_label": target.topic_label,
        "source_normalized_label": source.normalized_label,
        "target_normalized_label": target.normalized_label,
        "source_raw_label": source.raw_label,
        "target_raw_label": target.raw_label,
        "timestamp_delta_ms": evidence.timestamp_delta_ms,
        "volume_gap": evidence.volume_gap,
        "temporal_gap_seconds": evidence.temporal_gap_seconds,
        "continuity_mask": evidence.continuity_mask,
        "dormancy_score": evidence.dormancy_score,
        "specificity_score": evidence.specificity_score,
        "local_context_delta": evidence.local_context_delta,
        "score": evidence.score,
        "rank": rank,
        "evidence": {
            "reason_codes": list(evidence.reason_codes),
            "excerpt_similarity": evidence.excerpt_similarity,
            "topic_label_similarity": evidence.topic_label_similarity,
            "keyword_overlap_count": len(evidence.shared_keywords),
            "shared_keywords": list(evidence.shared_keywords),
            "normalized_label_match": evidence.normalized_label_match,
            "raw_label_match": evidence.raw_label_match,
            "volume_gap": evidence.volume_gap,
            "temporal_gap_seconds": evidence.temporal_gap_seconds,
            "continuity_mask": evidence.continuity_mask,
            "dormancy_score": evidence.dormancy_score,
            "specificity_score": evidence.specificity_score,
            "local_context_delta": evidence.local_context_delta,
        },
    }
    if embedding_similarity is not None:
        row["embedding_similarity"] = embedding_similarity
    errors = list(load_cross_thread_candidate_validator().iter_errors(row))
    if errors:
        raise CrossThreadCandidateError(
            f"cross-thread candidate schema validation failed: {errors[0].message}"
        )
    return row


def _unit_key(unit: _RepresentativeSpanUnit) -> tuple[str, str]:
    return (unit.conversation_id, unit.span_id)


def _is_low_value_artifact_instruction_text(text: str) -> bool:
    text_norm = " ".join(text.lower().split())
    if len(text_norm) < 48:
        return False
    wrapper_markers = (" ".join(marker.lower().split()) for marker in _ARTIFACT_WRAPPER_MARKERS)
    turn_control_markers = (
        " ".join(marker.lower().split()) for marker in _TURN_CONTROL_MARKERS
    )
    has_wrapper = any(marker in text_norm for marker in wrapper_markers)
    has_turn_control = any(marker in text_norm for marker in turn_control_markers)
    return has_wrapper and has_turn_control


def _should_filter_low_value_pair(
    source: _RepresentativeSpanUnit,
    target: _RepresentativeSpanUnit,
    evidence: _Evidence,
) -> bool:
    return (
        evidence.excerpt_similarity >= 0.78
        and _is_low_value_artifact_instruction_text(source.excerpt)
        and _is_low_value_artifact_instruction_text(target.excerpt)
    )


def _reconstructed_unit_text(
    unit: _RepresentativeSpanUnit,
    windows: dict[tuple[str, str], WindowPreviewRecord],
) -> str | None:
    if not unit.source_window_ids:
        return None
    message_lookup: dict[str, Any] = {}
    for window_id in unit.source_window_ids:
        record = windows.get((unit.conversation_id, window_id))
        if record is None:
            return None
        for message in record.messages:
            message_lookup.setdefault(message.message_id, message)
    texts: list[str] = []
    for message_id in unit.message_ids:
        message = message_lookup.get(message_id)
        if message is None:
            return None
        if message.text:
            texts.append(message.text)
    return "\n\n".join(texts)


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return max(
        -1.0,
        min(
            1.0,
            sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm),
        ),
    )


def _embedding_similarity_by_pair(
    *,
    input_root: Path,
    ranked_candidates: list[tuple[_RepresentativeSpanUnit, list[tuple[_RepresentativeSpanUnit, _Evidence]]]],
    embedding_model: str,
    embedding_base_url: str,
    embedding_timeout_seconds: float,
) -> dict[tuple[tuple[str, str], tuple[str, str]], float]:
    try:
        windows = load_window_preview_index(input_root)
        backend = create_embedding_backend(
            backend_name="ollama",
            model=embedding_model,
            backend_options={
                "base_url": embedding_base_url,
                "timeout_seconds": embedding_timeout_seconds,
            },
        )
    except Exception:
        return {}

    units_by_key: dict[tuple[str, str], _RepresentativeSpanUnit] = {}
    for source, targets in ranked_candidates:
        units_by_key.setdefault(_unit_key(source), source)
        for target, _ in targets:
            units_by_key.setdefault(_unit_key(target), target)

    ordered_unit_keys = sorted(units_by_key)
    texts: list[str] = []
    text_keys: list[tuple[str, str]] = []
    for key in ordered_unit_keys:
        text = _reconstructed_unit_text(units_by_key[key], windows)
        if not text:
            continue
        text_keys.append(key)
        texts.append(text)
    if not texts:
        return {}

    try:
        vectors = backend.embed(texts)
    except Exception:
        return {}
    if len(vectors) != len(texts):
        return {}

    vectors_by_key = {
        key: vector
        for key, vector in zip(text_keys, vectors, strict=True)
    }
    similarities: dict[tuple[tuple[str, str], tuple[str, str]], float] = {}
    for source, targets in ranked_candidates:
        source_key = _unit_key(source)
        source_vector = vectors_by_key.get(source_key)
        if source_vector is None:
            continue
        for target, _ in targets:
            target_key = _unit_key(target)
            target_vector = vectors_by_key.get(target_key)
            if target_vector is None:
                continue
            similarities[(source_key, target_key)] = round(
                _cosine_similarity(source_vector, target_vector),
                4,
            )
    return similarities


def _build_cross_thread_candidate_rows_with_stats(
    input_root: Path,
    *,
    min_score: float = DEFAULT_CROSS_THREAD_MIN_SCORE,
    top_per_source: int = DEFAULT_CROSS_THREAD_TOP_PER_SOURCE,
    embedding_model: str | None = None,
    embedding_base_url: str = "http://localhost:11434",
    embedding_timeout_seconds: float = 30.0,
) -> tuple[list[dict[str, Any]], int]:
    if top_per_source < 1:
        raise CrossThreadCandidateError("top_per_source must be at least 1")
    if min_score < 0 or min_score > 1:
        raise CrossThreadCandidateError("min_score must be between 0 and 1")

    topics_artifact = _load_topics_artifact(input_root)
    units = _representative_units(topics_artifact)
    recurrence_context = _build_recurrence_instrumentation_context(input_root, units)
    filtered_low_value_pair_count = 0
    selected_by_source: list[
        tuple[_RepresentativeSpanUnit, list[tuple[_RepresentativeSpanUnit, _Evidence]]]
    ] = []
    min_score_rounded = round(min_score, 4)
    for source in units:
        ranked_similarity: list[tuple[_RepresentativeSpanUnit, _Evidence]] = []
        ranked_weak: list[tuple[_RepresentativeSpanUnit, _Evidence, int]] = []
        for target in units:
            if source.conversation_id == target.conversation_id:
                continue
            if source.topic_id == target.topic_id and source.span_id == target.span_id:
                continue
            evidence = _evidence_for_pair(
                source,
                target,
                recurrence_context=recurrence_context,
            )
            weak_candidate = _weak_recurrence_evidence_for_pair(
                source,
                target,
                recurrence_context=recurrence_context,
                base_evidence=evidence,
            )
            similarity_evidence = weak_candidate.evidence if weak_candidate is not None else evidence
            if similarity_evidence is None and weak_candidate is None:
                continue
            candidate_for_filter = similarity_evidence or weak_candidate.evidence
            assert candidate_for_filter is not None
            if _should_filter_low_value_pair(source, target, candidate_for_filter):
                filtered_low_value_pair_count += 1
                continue
            if similarity_evidence is not None and similarity_evidence.score >= min_score_rounded:
                ranked_similarity.append((target, similarity_evidence))
            elif weak_candidate is not None:
                ranked_weak.append(
                    (
                        target,
                        weak_candidate.evidence,
                        weak_candidate.shared_anchor_count,
                    )
                )
        ranked_similarity.sort(
            key=lambda item: (
                -item[1].score,
                -item[1].excerpt_similarity,
                item[0].conversation_id,
                item[0].topic_id,
                item[0].span_id,
            )
        )
        ranked_weak.sort(
            key=lambda item: (
                -item[2],
                -item[1].dormancy_score,
                -(item[1].local_context_delta if item[1].local_context_delta is not None else -1.0),
                -item[1].specificity_score,
                -item[1].score,
                item[0].conversation_id,
                item[0].topic_id,
                item[0].span_id,
            )
        )
        selected = ranked_similarity[:top_per_source]
        selected_target_keys = {_unit_key(target) for target, _evidence in selected}
        weak_selected = [
            (target, evidence)
            for target, evidence, _shared_anchor_count in ranked_weak
            if _unit_key(target) not in selected_target_keys
        ][:top_per_source]
        selected.extend(weak_selected)
        if selected:
            selected_by_source.append((source, selected))

    embedding_similarities: dict[tuple[tuple[str, str], tuple[str, str]], float] = {}
    if embedding_model:
        embedding_similarities = _embedding_similarity_by_pair(
            input_root=input_root,
            ranked_candidates=selected_by_source,
            embedding_model=embedding_model,
            embedding_base_url=embedding_base_url,
            embedding_timeout_seconds=embedding_timeout_seconds,
        )

    rows: list[dict[str, Any]] = []
    for source, ranked in selected_by_source:
        ranked.sort(
            key=lambda item: (
                -item[1].score,
                -embedding_similarities.get((_unit_key(source), _unit_key(item[0])), -1.0),
                -item[1].excerpt_similarity,
                item[0].conversation_id,
                item[0].topic_id,
                item[0].span_id,
            )
        )
        for rank, (target, evidence) in enumerate(ranked, start=1):
            embedding_similarity = embedding_similarities.get(
                (_unit_key(source), _unit_key(target))
            )
            rows.append(
                _candidate_row(
                    source=source,
                    target=target,
                    evidence=evidence,
                    rank=rank,
                    embedding_similarity=embedding_similarity,
                )
            )

    rows.sort(
        key=lambda row: (
            row["source_conversation_id"],
            row["source_topic_id"],
            row["source_span_id"],
            row["rank"],
            row["target_conversation_id"],
            row["target_topic_id"],
            row["target_span_id"],
        )
    )
    return rows, filtered_low_value_pair_count


def build_cross_thread_candidate_rows(
    input_root: Path,
    *,
    min_score: float = DEFAULT_CROSS_THREAD_MIN_SCORE,
    top_per_source: int = DEFAULT_CROSS_THREAD_TOP_PER_SOURCE,
    embedding_model: str | None = None,
    embedding_base_url: str = "http://localhost:11434",
    embedding_timeout_seconds: float = 30.0,
) -> list[dict[str, Any]]:
    rows, _filtered_low_value_pair_count = _build_cross_thread_candidate_rows_with_stats(
        input_root,
        min_score=min_score,
        top_per_source=top_per_source,
        embedding_model=embedding_model,
        embedding_base_url=embedding_base_url,
        embedding_timeout_seconds=embedding_timeout_seconds,
    )
    return rows


def _score_band(score: float) -> str:
    if score >= 0.9:
        return "high"
    if score >= 0.75:
        return "medium"
    return "low"


def _summary(
    *,
    topics_artifact: dict[str, Any],
    rows: list[dict[str, Any]],
    input_root: Path,
    min_score: float,
    top_per_source: int,
    filtered_low_value_pair_count: int = 0,
) -> dict[str, Any]:
    units = _representative_units(topics_artifact)
    reason_counts: Counter[str] = Counter()
    score_bands: Counter[str] = Counter()
    source_keys = {
        (
            row["source_conversation_id"],
            row["source_topic_id"],
            row["source_span_id"],
        )
        for row in rows
    }
    threads_involved = {
        row["source_conversation_id"] for row in rows
    } | {
        row["target_conversation_id"] for row in rows
    }
    for row in rows:
        for reason_code in row["evidence"]["reason_codes"]:
            reason_counts[str(reason_code)] += 1
        score_bands[_score_band(float(row["score"]))] += 1
    summary = {
        "artifact_type": CROSS_THREAD_CANDIDATE_SUMMARY_ARTIFACT_TYPE,
        "schema_version": CROSS_THREAD_CANDIDATE_SCHEMA_VERSION,
        "provider_id": topics_artifact["provider_id"],
        "generated_from": str((input_root / "l3" / "semantic-topics" / "topics.json").resolve()),
        "source_unit_count": len(units),
        "source_unit_with_candidates_count": len(source_keys),
        "candidate_link_count": len(rows),
        "thread_count_with_candidates": len(threads_involved),
        "guardrails": {
            "min_score": round(min_score, 4),
            "top_per_source": top_per_source,
        },
        "reason_counts": dict(sorted(reason_counts.items())),
        "score_band_counts": {
            band: score_bands.get(band, 0)
            for band in ("high", "medium", "low")
        },
    }
    if filtered_low_value_pair_count:
        summary["filtered_low_value_pair_count"] = filtered_low_value_pair_count
    return summary


def write_cross_thread_candidates_artifact(
    input_root: Path,
    *,
    min_score: float = DEFAULT_CROSS_THREAD_MIN_SCORE,
    top_per_source: int = DEFAULT_CROSS_THREAD_TOP_PER_SOURCE,
    embedding_model: str | None = None,
    embedding_base_url: str = "http://localhost:11434",
    embedding_timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    provider_root = input_root.expanduser()
    if not provider_root.exists() or not provider_root.is_dir():
        raise CrossThreadCandidateError(f"provider root not found: {provider_root}")

    topics_artifact = _load_topics_artifact(provider_root)
    rows, filtered_low_value_pair_count = _build_cross_thread_candidate_rows_with_stats(
        provider_root,
        min_score=min_score,
        top_per_source=top_per_source,
        embedding_model=embedding_model,
        embedding_base_url=embedding_base_url,
        embedding_timeout_seconds=embedding_timeout_seconds,
    )
    output_dir = provider_root / "l3" / "cross-thread-candidates"
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates_path = output_dir / "candidates.jsonl"
    summary_path = output_dir / "summary.json"

    tmp_candidates_path = candidates_path.with_suffix(".tmp")
    with tmp_candidates_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp_candidates_path.replace(candidates_path)

    summary = _summary(
        topics_artifact=topics_artifact,
        rows=rows,
        input_root=provider_root,
        min_score=min_score,
        top_per_source=top_per_source,
        filtered_low_value_pair_count=filtered_low_value_pair_count,
    )
    write_json_artifact(summary_path, summary)

    return {
        "candidate_count": len(rows),
        "candidates_path": candidates_path,
        "summary_path": summary_path,
    }
