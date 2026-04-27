from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .analyzer_common import detect_header_metadata
from .analyzer_semantic_prototype import (
    cosine_similarity,
    derive_semantic_span_id,
    resolve_embedding_backend,
)
from .embedding_backend import EmbeddingBackend
from .l1_derivation import (
    canonical_role_or_unknown,
    discover_parsed_jsonl,
    iter_message_records,
    message_text,
)
from .message_window_reconstruction import parsed_path_for_message_windows

DEFAULT_INTRA_THREAD_WINDOW_SIZE = 3
DEFAULT_INTRA_THREAD_WINDOW_STRIDE = 1
DEFAULT_INTRA_THREAD_BOUNDARY_THRESHOLD = 0.75
DEFAULT_INTRA_THREAD_MIN_WINDOW_CONTENT_CHARS = 8
DEFAULT_INTRA_THREAD_LEXICAL_CONTINUITY_WEIGHT = 0.2
DEFAULT_INTRA_THREAD_STRUCTURAL_CONTINUITY_WEIGHT = 0.15
DEFAULT_SEGMENT_MERGE_SIMILARITY_THRESHOLD = 0.75
DEFAULT_SEGMENT_MERGE_MIN_LEXICAL_SIMILARITY = 0.0
SEGMENT_MERGE_MIN_MESSAGE_COUNT = 3
SEGMENT_MERGE_TOP_TOKEN_LIMIT = 10
SEGMENT_MERGE_FILTERED_TOP_TOKEN_LIMIT = 10
SEGMENT_MERGE_MIN_TOKEN_LENGTH = 2
SEGMENT_MERGE_GENERIC_TOKENS = frozenset(
    {
        "the",
        "and",
        "to",
        "of",
        "it",
        "is",
        "in",
        "for",
        "on",
        "10",
        "20",
        "30",
        "40",
        "50",
        "また",
        "これ",
        "それ",
        "この",
        "その",
        "ため",
        "ます",
        "です",
        "ください",
        "お願いします",
        "了解しました",
    }
)
BOUNDARY_SCHEMA_VERSION = "0.3"
SEGMENT_SCHEMA_VERSION = "0.1"
SEGMENT_MERGE_SCHEMA_VERSION = "0.1"
SEGMENT_MERGE_DIAGNOSTIC_SCHEMA_VERSION = "0.1"
TOPIC_CANDIDATE_SCHEMA_VERSION = "0.1"
LEXICAL_WORD_TOKEN_RE = re.compile(r"\w+")
REPORT_PREVIEW_MESSAGES_PER_EDGE = 3
REPORT_PREVIEW_TEXT_CHARS = 120
REPORT_SUPPRESSED_SCORE_MARGIN = 0.15
REPORT_NEAR_THRESHOLD_DISTANCE = 0.05
REPORT_SEGMENT_MERGE_PAIR_LIMIT = 20
REPORT_LONG_SEGMENT_MESSAGE_COUNT = 80
REPORT_DRIFT_WEAKEST_CANDIDATES = 5
REPORT_DRIFT_NEAR_THRESHOLD_DISTANCE = 0.08
REPORT_DRIFT_LOW_SCORE_CEILING = 0.55
REPORT_DRIFT_LOW_SCORE_RUN_LENGTH = 3
DRIFT_GUARDRAIL_LONG_SEGMENT_MESSAGE_COUNT = 80
DRIFT_GUARDRAIL_THRESHOLD_MARGIN = 0.01
STRUCTURAL_GAP_CONTINUITY = 0.2
STRUCTURAL_MAX_SKIPPED_NON_SUBSTANTIVE = 2


class IntraThreadTopicsError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReconstructedThreadMessage:
    provider_id: str
    conversation_id: str
    message_id: str
    role: str
    ts: int | None
    text: str
    ordinal: int


@dataclass(frozen=True)
class SlidingMessageWindow:
    provider_id: str
    conversation_id: str
    window_index: int
    message_ids: tuple[str, ...]
    start_index: int
    end_index: int
    text: str
    text_sha1: str
    content_char_count: int


@dataclass(frozen=True)
class AdjacentWindowBoundary:
    provider_id: str
    conversation_id: str
    previous_window_index: int
    next_window_index: int
    previous_window_message_ids: tuple[str, ...]
    next_window_message_ids: tuple[str, ...]
    similarity: float
    lexical_similarity: float
    structural_continuity: float
    continuity_score: float
    boundary: bool
    split_after_message_index: int
    split_before_message_index: int


@dataclass(frozen=True)
class ContiguousSegment:
    provider_id: str
    conversation_id: str
    segment_id: str
    start_index: int
    end_index: int
    message_ids: tuple[str, ...]
    message_count: int
    text_sha1: str


@dataclass(frozen=True)
class SegmentMergeGroup:
    provider_id: str
    conversation_id: str
    group_id: str
    segment_ids: tuple[str, ...]
    message_ids: tuple[str, ...]
    centroid_embedding: tuple[float, ...]


@dataclass(frozen=True)
class SegmentMergePairDiagnostic:
    provider_id: str
    conversation_id: str
    left_segment_id: str
    right_segment_id: str
    left_segment_index: int
    right_segment_index: int
    segment_index_gap: int
    similarity: float
    lexical_similarity: float
    shared_top_tokens: tuple[str, ...]
    left_top_tokens: tuple[str, ...]
    right_top_tokens: tuple[str, ...]
    filtered_shared_top_tokens: tuple[str, ...]
    filtered_left_top_tokens: tuple[str, ...]
    filtered_right_top_tokens: tuple[str, ...]
    lexical_anchor_score: float
    lexical_anchor_band: str
    content_type_flags: dict[str, tuple[str, ...]]
    decision: str
    reason: str


def intra_thread_topics_dir(parsed_path: Path) -> Path:
    return parsed_path.parent / "l3" / "intra-thread-topics"


def intra_thread_boundaries_artifact_path(parsed_path: Path) -> Path:
    return intra_thread_topics_dir(parsed_path) / "boundaries.jsonl"


def intra_thread_segments_artifact_path(parsed_path: Path) -> Path:
    return intra_thread_topics_dir(parsed_path) / "segments.jsonl"


def intra_thread_segment_merge_artifact_path(parsed_path: Path) -> Path:
    return intra_thread_topics_dir(parsed_path) / "segment-merge.jsonl"


def intra_thread_segment_merge_diagnostics_artifact_path(parsed_path: Path) -> Path:
    return intra_thread_topics_dir(parsed_path) / "segment-merge-diagnostics.jsonl"


def intra_thread_topic_candidates_artifact_path(parsed_path: Path) -> Path:
    return intra_thread_topics_dir(parsed_path) / "topic-candidates.jsonl"


def intra_thread_report_artifact_path(parsed_path: Path) -> Path:
    return intra_thread_topics_dir(parsed_path) / "report.md"


def _normalize_parsed_path(input_path: Path) -> Path:
    path = input_path.expanduser()
    if path.is_file() and path.name == "message_windows.jsonl":
        return parsed_path_for_message_windows(path)
    return path


def reconstruct_thread_messages(parsed_path: Path) -> list[ReconstructedThreadMessage]:
    provider_id, conversation_id = detect_header_metadata(parsed_path)
    rows: list[ReconstructedThreadMessage] = []

    for ordinal, row in enumerate(iter_message_records(parsed_path)):
        row_conversation_id = row.get("conversation_id")
        row_message_id = row.get("message_id")
        if not isinstance(row_conversation_id, str) or not row_conversation_id:
            raise IntraThreadTopicsError(
                f"message row missing conversation_id in {parsed_path}"
            )
        if not isinstance(row_message_id, str) or not row_message_id:
            raise IntraThreadTopicsError(
                f"message row missing message_id in {parsed_path}"
            )
        row_provider_id = row.get("provider_id")
        normalized_provider_id = (
            row_provider_id
            if isinstance(row_provider_id, str) and row_provider_id
            else provider_id
        )
        if not isinstance(normalized_provider_id, str) or not normalized_provider_id:
            raise IntraThreadTopicsError(
                f"message row missing provider_id in {parsed_path}"
            )
        rows.append(
            ReconstructedThreadMessage(
                provider_id=normalized_provider_id,
                conversation_id=row_conversation_id,
                message_id=row_message_id,
                role=canonical_role_or_unknown(row.get("role")),
                ts=row.get("ts") if isinstance(row.get("ts"), int) else None,
                text=message_text(row),
                ordinal=ordinal,
            )
        )

    if not rows:
        raise IntraThreadTopicsError(f"no message records found in {parsed_path}")
    unique_conversation_ids = {row.conversation_id for row in rows}
    if len(unique_conversation_ids) != 1:
        raise IntraThreadTopicsError(
            f"parsed thread contains multiple conversation_ids in {parsed_path}"
        )
    if conversation_id is not None and unique_conversation_ids != {conversation_id}:
        raise IntraThreadTopicsError(
            f"thread header conversation_id mismatch in {parsed_path}"
        )
    return rows


def build_sliding_windows(
    messages: list[ReconstructedThreadMessage],
    *,
    window_size: int = DEFAULT_INTRA_THREAD_WINDOW_SIZE,
    window_stride: int = DEFAULT_INTRA_THREAD_WINDOW_STRIDE,
) -> list[SlidingMessageWindow]:
    if window_size <= 0:
        raise ValueError("window_size must be > 0")
    if window_stride <= 0:
        raise ValueError("window_stride must be > 0")
    if not messages:
        return []

    effective_window_size = min(window_size, len(messages))
    windows: list[SlidingMessageWindow] = []
    start_indices = list(range(0, len(messages) - effective_window_size + 1, window_stride))
    if not start_indices:
        start_indices = [0]
    last_possible_start = len(messages) - effective_window_size
    if start_indices[-1] != last_possible_start:
        start_indices.append(last_possible_start)

    for window_index, start_index in enumerate(start_indices):
        end_index = start_index + effective_window_size - 1
        window_messages = messages[start_index : end_index + 1]
        text = "\n\n".join(
            message.text for message in window_messages if message.text
        )
        content_char_count = _non_whitespace_char_count(text)
        windows.append(
            SlidingMessageWindow(
                provider_id=window_messages[0].provider_id,
                conversation_id=window_messages[0].conversation_id,
                window_index=window_index,
                message_ids=tuple(message.message_id for message in window_messages),
                start_index=start_index,
                end_index=end_index,
                text=text,
                text_sha1=hashlib.sha1(text.encode("utf-8")).hexdigest(),
                content_char_count=content_char_count,
            )
        )
    return windows


def build_window_embedding_records(
    windows: list[SlidingMessageWindow],
    *,
    backend: EmbeddingBackend,
) -> list[list[float]]:
    if not windows:
        return []
    return backend.embed([window.text for window in windows])


def detect_adjacent_boundaries(
    messages: list[ReconstructedThreadMessage],
    windows: list[SlidingMessageWindow],
    embeddings: list[list[float]],
    *,
    threshold: float = DEFAULT_INTRA_THREAD_BOUNDARY_THRESHOLD,
    min_window_content_chars: int = DEFAULT_INTRA_THREAD_MIN_WINDOW_CONTENT_CHARS,
    lexical_continuity_weight: float = DEFAULT_INTRA_THREAD_LEXICAL_CONTINUITY_WEIGHT,
    structural_continuity_weight: float = (
        DEFAULT_INTRA_THREAD_STRUCTURAL_CONTINUITY_WEIGHT
    ),
) -> list[AdjacentWindowBoundary]:
    if len(windows) != len(embeddings):
        raise ValueError("windows and embeddings must have the same length")
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0.0 and 1.0")
    if min_window_content_chars < 0:
        raise ValueError("min_window_content_chars must be >= 0")
    if lexical_continuity_weight < 0.0:
        raise ValueError("lexical_continuity_weight must be >= 0.0")
    if structural_continuity_weight < 0.0:
        raise ValueError("structural_continuity_weight must be >= 0.0")

    boundaries: list[AdjacentWindowBoundary] = []
    for index in range(len(windows) - 1):
        previous_window = windows[index]
        next_window = windows[index + 1]
        similarity = cosine_similarity(embeddings[index], embeddings[index + 1])
        lexical_similarity = boundary_lexical_similarity(
            messages,
            previous_window,
            next_window,
        )
        structural_continuity = boundary_structural_continuity(
            messages,
            previous_window,
            next_window,
        )
        continuity_score = similarity + (
            lexical_continuity_weight * lexical_similarity
        ) + (
            structural_continuity_weight * structural_continuity
        )
        boundary_allowed = (
            previous_window.content_char_count >= min_window_content_chars
            and next_window.content_char_count >= min_window_content_chars
        )
        split_before_message_index = previous_window.end_index + 1
        boundaries.append(
            AdjacentWindowBoundary(
                provider_id=previous_window.provider_id,
                conversation_id=previous_window.conversation_id,
                previous_window_index=previous_window.window_index,
                next_window_index=next_window.window_index,
                previous_window_message_ids=previous_window.message_ids,
                next_window_message_ids=next_window.message_ids,
                similarity=round(similarity, 4),
                lexical_similarity=round(lexical_similarity, 4),
                structural_continuity=round(structural_continuity, 4),
                continuity_score=round(continuity_score, 4),
                boundary=boundary_allowed and continuity_score < threshold,
                split_after_message_index=split_before_message_index - 1,
                split_before_message_index=split_before_message_index,
            )
        )
    return boundaries


def _segment_id(
    provider_id: str,
    conversation_id: str,
    message_ids: tuple[str, ...],
) -> str:
    return "segment_" + derive_semantic_span_id(
        provider_id=provider_id,
        conversation_id=conversation_id,
        message_ids=message_ids,
        window_id="segment-fallback",
    )[:12]


def build_contiguous_segments(
    messages: list[ReconstructedThreadMessage],
    boundaries: list[AdjacentWindowBoundary],
    *,
    boundary_threshold: float = DEFAULT_INTRA_THREAD_BOUNDARY_THRESHOLD,
) -> list[ContiguousSegment]:
    if not messages:
        return []

    boundary_starts = sorted(
        {
            boundary.split_before_message_index
            for boundary in boundaries
            if boundary.boundary
        }
    )
    segment_ranges: list[tuple[int, int]] = []
    segment_start = 0
    for boundary_start in [*boundary_starts, len(messages)]:
        if boundary_start <= segment_start:
            continue
        segment_ranges.append((segment_start, boundary_start))
        segment_start = boundary_start
    segment_ranges = _apply_guarded_drift_splits(
        messages,
        boundaries,
        segment_ranges,
        boundary_threshold=boundary_threshold,
    )
    cleaned_ranges = _absorb_empty_segment_ranges(messages, segment_ranges)
    return [
        _build_contiguous_segment(messages, start_index, end_index)
        for start_index, end_index in cleaned_ranges
    ]


def _boundary_row(boundary: AdjacentWindowBoundary) -> dict[str, Any]:
    return {
        "record_type": "intra_thread_boundary",
        "schema_version": BOUNDARY_SCHEMA_VERSION,
        "provider_id": boundary.provider_id,
        "conversation_id": boundary.conversation_id,
        "previous_window_index": boundary.previous_window_index,
        "next_window_index": boundary.next_window_index,
        "previous_window_message_ids": list(boundary.previous_window_message_ids),
        "next_window_message_ids": list(boundary.next_window_message_ids),
        "similarity": boundary.similarity,
        "lexical_similarity": boundary.lexical_similarity,
        "structural_continuity": boundary.structural_continuity,
        "continuity_score": boundary.continuity_score,
        "boundary": boundary.boundary,
        "split_after_message_index": boundary.split_after_message_index,
        "split_before_message_index": boundary.split_before_message_index,
    }


def _segment_row(segment: ContiguousSegment) -> dict[str, Any]:
    return {
        "record_type": "intra_thread_segment",
        "schema_version": SEGMENT_SCHEMA_VERSION,
        "provider_id": segment.provider_id,
        "conversation_id": segment.conversation_id,
        "segment_id": segment.segment_id,
        "start_index": segment.start_index,
        "end_index": segment.end_index,
        "message_ids": list(segment.message_ids),
        "message_count": segment.message_count,
        "text_sha1": segment.text_sha1,
    }


def _segment_merge_group_row(group: SegmentMergeGroup) -> dict[str, Any]:
    return {
        "record_type": "intra_thread_segment_merge_group",
        "schema_version": SEGMENT_MERGE_SCHEMA_VERSION,
        "provider_id": group.provider_id,
        "conversation_id": group.conversation_id,
        "group_id": group.group_id,
        "segment_ids": list(group.segment_ids),
        "message_ids": list(group.message_ids),
        "centroid_embedding": list(group.centroid_embedding),
    }


def _segment_merge_pair_diagnostic_row(
    diagnostic: SegmentMergePairDiagnostic,
) -> dict[str, Any]:
    return {
        "record_type": "intra_thread_segment_merge_pair_diagnostic",
        "schema_version": SEGMENT_MERGE_DIAGNOSTIC_SCHEMA_VERSION,
        "provider_id": diagnostic.provider_id,
        "conversation_id": diagnostic.conversation_id,
        "left_segment_id": diagnostic.left_segment_id,
        "right_segment_id": diagnostic.right_segment_id,
        "left_segment_index": diagnostic.left_segment_index,
        "right_segment_index": diagnostic.right_segment_index,
        "segment_index_gap": diagnostic.segment_index_gap,
        "similarity": diagnostic.similarity,
        "lexical_similarity": diagnostic.lexical_similarity,
        "shared_top_tokens": list(diagnostic.shared_top_tokens),
        "left_top_tokens": list(diagnostic.left_top_tokens),
        "right_top_tokens": list(diagnostic.right_top_tokens),
        "filtered_shared_top_tokens": list(diagnostic.filtered_shared_top_tokens),
        "filtered_left_top_tokens": list(diagnostic.filtered_left_top_tokens),
        "filtered_right_top_tokens": list(diagnostic.filtered_right_top_tokens),
        "lexical_anchor_score": diagnostic.lexical_anchor_score,
        "lexical_anchor_band": diagnostic.lexical_anchor_band,
        "content_type_flags": {
            "left": list(diagnostic.content_type_flags.get("left", ())),
            "right": list(diagnostic.content_type_flags.get("right", ())),
        },
        "decision": diagnostic.decision,
        "reason": diagnostic.reason,
    }


def _topic_candidate_row(
    diagnostic: SegmentMergePairDiagnostic,
    *,
    messages: list[ReconstructedThreadMessage],
    segments: list[ContiguousSegment],
) -> dict[str, Any]:
    left_segment = segments[diagnostic.left_segment_index]
    right_segment = segments[diagnostic.right_segment_index]
    return {
        "record_type": "intra_thread_topic_candidate",
        "schema_version": TOPIC_CANDIDATE_SCHEMA_VERSION,
        "thread_id": diagnostic.conversation_id,
        "provider_id": diagnostic.provider_id,
        "conversation_id": diagnostic.conversation_id,
        "left_segment_id": diagnostic.left_segment_id,
        "right_segment_id": diagnostic.right_segment_id,
        "left_segment_index": diagnostic.left_segment_index,
        "right_segment_index": diagnostic.right_segment_index,
        "candidate_type": diagnostic.lexical_anchor_band,
        "candidate_role": _topic_candidate_role(diagnostic.lexical_anchor_band),
        "similarity": diagnostic.similarity,
        "lexical_similarity": diagnostic.lexical_similarity,
        "lexical_anchor_score": diagnostic.lexical_anchor_score,
        "lexical_anchor_band": diagnostic.lexical_anchor_band,
        "content_type_flags": {
            "left": list(diagnostic.content_type_flags.get("left", ())),
            "right": list(diagnostic.content_type_flags.get("right", ())),
        },
        "reasons": _topic_candidate_reasons(diagnostic),
        "left_preview": _segment_preview_text(left_segment, messages),
        "right_preview": _segment_preview_text(right_segment, messages),
        "segment_index_gap": diagnostic.segment_index_gap,
    }


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(path)
    return path


def _build_contiguous_segment(
    messages: list[ReconstructedThreadMessage],
    start_index: int,
    end_index: int,
) -> ContiguousSegment:
    segment_messages = messages[start_index:end_index]
    message_ids = tuple(message.message_id for message in segment_messages)
    segment_text = "\n\n".join(
        message.text for message in segment_messages if message.text
    )
    return ContiguousSegment(
        provider_id=segment_messages[0].provider_id,
        conversation_id=segment_messages[0].conversation_id,
        segment_id=_segment_id(
            segment_messages[0].provider_id,
            segment_messages[0].conversation_id,
            message_ids,
        ),
        start_index=start_index,
        end_index=end_index - 1,
        message_ids=message_ids,
        message_count=len(message_ids),
        text_sha1=hashlib.sha1(segment_text.encode("utf-8")).hexdigest(),
    )


def build_segment_merge_groups(
    messages: list[ReconstructedThreadMessage],
    segments: list[ContiguousSegment],
    *,
    backend: EmbeddingBackend,
    similarity_threshold: float = DEFAULT_SEGMENT_MERGE_SIMILARITY_THRESHOLD,
    min_lexical_similarity: float = DEFAULT_SEGMENT_MERGE_MIN_LEXICAL_SIMILARITY,
    min_message_count: int = SEGMENT_MERGE_MIN_MESSAGE_COUNT,
) -> list[SegmentMergeGroup]:
    groups, _diagnostics = build_segment_merge_groups_with_diagnostics(
        messages,
        segments,
        backend=backend,
        similarity_threshold=similarity_threshold,
        min_lexical_similarity=min_lexical_similarity,
        min_message_count=min_message_count,
    )
    return groups


def build_segment_merge_groups_with_diagnostics(
    messages: list[ReconstructedThreadMessage],
    segments: list[ContiguousSegment],
    *,
    backend: EmbeddingBackend,
    similarity_threshold: float = DEFAULT_SEGMENT_MERGE_SIMILARITY_THRESHOLD,
    min_lexical_similarity: float = DEFAULT_SEGMENT_MERGE_MIN_LEXICAL_SIMILARITY,
    min_message_count: int = SEGMENT_MERGE_MIN_MESSAGE_COUNT,
) -> tuple[list[SegmentMergeGroup], list[SegmentMergePairDiagnostic]]:
    if not 0.0 <= similarity_threshold <= 1.0:
        raise ValueError("similarity_threshold must be between 0.0 and 1.0")
    if not 0.0 <= min_lexical_similarity <= 1.0:
        raise ValueError("min_lexical_similarity must be between 0.0 and 1.0")
    if min_message_count < 0:
        raise ValueError("min_message_count must be >= 0")
    if not segments:
        return [], []

    segment_texts = [
        _segment_text_from_message_ids(messages, segment) for segment in segments
    ]
    token_counts = [_segment_merge_token_counts(text) for text in segment_texts]
    top_tokens = [_top_segment_merge_tokens(counts) for counts in token_counts]
    filtered_token_counts = [
        _filtered_segment_merge_token_counts(counts) for counts in token_counts
    ]
    filtered_top_tokens = [
        _top_segment_merge_tokens(
            counts,
            limit=SEGMENT_MERGE_FILTERED_TOP_TOKEN_LIMIT,
        )
        for counts in filtered_token_counts
    ]
    content_type_flags = [
        tuple(_segment_merge_content_type_flags(text)) for text in segment_texts
    ]
    embeddings = backend.embed(segment_texts)
    if len(embeddings) != len(segments):
        raise IntraThreadTopicsError("segment embedding count mismatch")

    parent = list(range(len(segments)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> bool:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return True
        if _segment_index_groups_would_be_adjacent(parent, left_root, right_root):
            return False
        if left_root < right_root:
            parent[right_root] = left_root
        else:
            parent[left_root] = right_root
        return True

    diagnostics: list[SegmentMergePairDiagnostic] = []
    for left_index, left_segment in enumerate(segments):
        for right_index in range(left_index + 1, len(segments)):
            right_segment = segments[right_index]
            similarity = cosine_similarity(embeddings[left_index], embeddings[right_index])
            left_token_set = frozenset(token_counts[left_index])
            right_token_set = frozenset(token_counts[right_index])
            lexical_similarity = lexical_jaccard_similarity(
                left_token_set,
                right_token_set,
            )
            shared_top_tokens = _shared_segment_merge_top_tokens(
                token_counts[left_index],
                token_counts[right_index],
            )
            filtered_shared_top_tokens = _shared_filtered_segment_merge_top_tokens(
                filtered_token_counts[left_index],
                filtered_token_counts[right_index],
                filtered_top_tokens[left_index],
                filtered_top_tokens[right_index],
            )
            lexical_anchor_score = _lexical_anchor_score(
                filtered_shared_top_tokens,
                filtered_top_tokens[left_index],
                filtered_top_tokens[right_index],
            )
            decision = "below_threshold"
            reason = f"similarity below {similarity_threshold:.2f}"
            if (
                left_segment.message_count < min_message_count
                or right_segment.message_count < min_message_count
            ):
                decision = "short_segment"
                reason = f"one or both segments below {min_message_count} messages"
            elif right_index == left_index + 1:
                decision = "blocked_adjacent_group"
                reason = "directly adjacent segments are not merged"
            elif similarity < similarity_threshold:
                decision = "below_threshold"
                reason = f"similarity below {similarity_threshold:.2f}"
            elif lexical_similarity < min_lexical_similarity:
                decision = "blocked_low_lexical_anchor"
                reason = f"lexical_similarity below {min_lexical_similarity:.2f}"
            elif similarity >= similarity_threshold:
                if union(left_index, right_index):
                    decision = "merged"
                    reason = f"similarity >= {similarity_threshold:.2f}"
                else:
                    decision = "blocked_adjacent_group"
                    reason = "merge would create a group containing adjacent segments"
            diagnostics.append(
                SegmentMergePairDiagnostic(
                    provider_id=left_segment.provider_id,
                    conversation_id=left_segment.conversation_id,
                    left_segment_id=left_segment.segment_id,
                    right_segment_id=right_segment.segment_id,
                    left_segment_index=left_index,
                    right_segment_index=right_index,
                    segment_index_gap=right_index - left_index - 1,
                    similarity=round(similarity, 4),
                    lexical_similarity=round(lexical_similarity, 4),
                    shared_top_tokens=tuple(shared_top_tokens),
                    left_top_tokens=tuple(top_tokens[left_index]),
                    right_top_tokens=tuple(top_tokens[right_index]),
                    filtered_shared_top_tokens=tuple(filtered_shared_top_tokens),
                    filtered_left_top_tokens=tuple(filtered_top_tokens[left_index]),
                    filtered_right_top_tokens=tuple(filtered_top_tokens[right_index]),
                    lexical_anchor_score=round(lexical_anchor_score, 4),
                    lexical_anchor_band=_lexical_anchor_band(lexical_anchor_score),
                    content_type_flags={
                        "left": content_type_flags[left_index],
                        "right": content_type_flags[right_index],
                    },
                    decision=decision,
                    reason=reason,
                )
            )

    grouped_indices: dict[int, list[int]] = {}
    for index in range(len(segments)):
        grouped_indices.setdefault(find(index), []).append(index)

    groups: list[SegmentMergeGroup] = []
    for indices in sorted(grouped_indices.values(), key=lambda item: item[0]):
        group_segments = [segments[index] for index in indices]
        group_embeddings = [embeddings[index] for index in indices]
        groups.append(
            SegmentMergeGroup(
                provider_id=group_segments[0].provider_id,
                conversation_id=group_segments[0].conversation_id,
                group_id=_segment_merge_group_id(group_segments),
                segment_ids=tuple(segment.segment_id for segment in group_segments),
                message_ids=tuple(
                    sorted(
                        {
                            message_id
                            for segment in group_segments
                            for message_id in segment.message_ids
                        }
                    )
                ),
                centroid_embedding=tuple(
                    round(value, 6) for value in _mean_embedding(group_embeddings)
                ),
            )
        )
    return groups, diagnostics


def _segment_index_groups_would_be_adjacent(
    parent: list[int],
    left_root: int,
    right_root: int,
) -> bool:
    left_indices = [
        index for index in range(len(parent)) if _find_parent(parent, index) == left_root
    ]
    right_indices = [
        index for index in range(len(parent)) if _find_parent(parent, index) == right_root
    ]
    return any(abs(left - right) == 1 for left in left_indices for right in right_indices)


def _find_parent(parent: list[int], index: int) -> int:
    while parent[index] != index:
        index = parent[index]
    return index


def _segment_text_from_message_ids(
    messages: list[ReconstructedThreadMessage],
    segment: ContiguousSegment,
) -> str:
    messages_by_id = {message.message_id: message for message in messages}
    return "\n\n".join(
        messages_by_id[message_id].text
        for message_id in segment.message_ids
        if message_id in messages_by_id and messages_by_id[message_id].text
    )


def _segment_merge_token_counts(text: str) -> dict[str, int]:
    normalized = unicodedata.normalize("NFKC", text).casefold().strip()
    word_tokens = [
        token
        for token in LEXICAL_WORD_TOKEN_RE.findall(normalized)
        if len(token) >= SEGMENT_MERGE_MIN_TOKEN_LENGTH
    ]
    if len(word_tokens) >= 2:
        return _token_counts(word_tokens)

    compact = "".join(char for char in normalized if not char.isspace())
    if len(compact) < SEGMENT_MERGE_MIN_TOKEN_LENGTH:
        return {}
    if len(compact) < 3:
        return _token_counts([compact])
    return _token_counts(
        compact[index : index + 3] for index in range(len(compact) - 2)
    )


def _token_counts(tokens: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for token in tokens:
        if not token:
            continue
        counts[token] = counts.get(token, 0) + 1
    return counts


def _top_segment_merge_tokens(
    token_counts: dict[str, int],
    *,
    limit: int = SEGMENT_MERGE_TOP_TOKEN_LIMIT,
) -> list[str]:
    return [
        token
        for token, _count in sorted(
            token_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[:limit]
    ]


def _shared_segment_merge_top_tokens(
    left_counts: dict[str, int],
    right_counts: dict[str, int],
    *,
    limit: int = SEGMENT_MERGE_TOP_TOKEN_LIMIT,
) -> list[str]:
    shared = set(left_counts) & set(right_counts)
    return sorted(
        shared,
        key=lambda token: (-(left_counts[token] + right_counts[token]), token),
    )[:limit]


def _shared_filtered_segment_merge_top_tokens(
    left_counts: dict[str, int],
    right_counts: dict[str, int],
    filtered_left_top_tokens: list[str],
    filtered_right_top_tokens: list[str],
) -> list[str]:
    shared = set(filtered_left_top_tokens) & set(filtered_right_top_tokens)
    return sorted(
        shared,
        key=lambda token: (-(left_counts[token] + right_counts[token]), token),
    )[:SEGMENT_MERGE_FILTERED_TOP_TOKEN_LIMIT]


def _filtered_segment_merge_token_counts(
    token_counts: dict[str, int],
) -> dict[str, int]:
    return {
        token: count
        for token, count in token_counts.items()
        if token not in SEGMENT_MERGE_GENERIC_TOKENS
    }


def _lexical_anchor_score(
    filtered_shared_top_tokens: list[str],
    filtered_left_top_tokens: list[str],
    filtered_right_top_tokens: list[str],
) -> float:
    denominator = max(
        1,
        min(len(filtered_left_top_tokens), len(filtered_right_top_tokens)),
    )
    return len(filtered_shared_top_tokens) / denominator


def _lexical_anchor_band(lexical_anchor_score: float) -> str:
    if lexical_anchor_score < 0.10:
        return "weak"
    if lexical_anchor_score < 0.20:
        return "gray"
    return "strong"


def _segment_merge_content_type_flags(text: str) -> list[str]:
    flags: list[str] = []
    normalized = unicodedata.normalize("NFKC", text)
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    numbered_lines = sum(
        1 for line in lines if re.match(r"^(?:\d+|[0-9]+)[\.)、:：]", line)
    )
    numeric_markers = len(re.findall(r"(?:^|\s)(?:\d+)[\.)、:：]", normalized))
    katakana_tokens = re.findall(r"[ァ-ヴー]{2,}", normalized)
    short_lines = [line for line in lines if 0 < len(line) <= 24]

    if numbered_lines >= 3 or numeric_markers >= 5:
        flags.append("list_like")
    if any(mark in normalized for mark in ("「", "」", "『", "』")) or len(normalized) >= 800:
        flags.append("prose_like")
    if len(katakana_tokens) >= 12 and len(short_lines) >= 5:
        flags.append("name_list_like")
    if any(
        phrase in normalized
        for phrase in (
            "挙げてください",
            "作成してください",
            "まとめてください",
            "提案してください",
        )
    ):
        flags.append("instruction_like")
    if any(term in normalized for term in ("まとめ", "設定", "概要")):
        flags.append("summary_like")
    return flags


def _segment_merge_group_id(segments: list[ContiguousSegment]) -> str:
    payload = "\n".join(sorted(segment.segment_id for segment in segments))
    return "segment_merge_" + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def _mean_embedding(embeddings: list[list[float]]) -> list[float]:
    if not embeddings:
        return []
    dimension = len(embeddings[0])
    totals = [0.0] * dimension
    for embedding in embeddings:
        if len(embedding) != dimension:
            raise IntraThreadTopicsError("segment embeddings have inconsistent dimensions")
        for index, value in enumerate(embedding):
            totals[index] += value
    return [value / len(embeddings) for value in totals]


def _apply_guarded_drift_splits(
    messages: list[ReconstructedThreadMessage],
    boundaries: list[AdjacentWindowBoundary],
    segment_ranges: list[tuple[int, int]],
    *,
    boundary_threshold: float,
) -> list[tuple[int, int]]:
    if not segment_ranges:
        return segment_ranges

    split_ranges: list[tuple[int, int]] = []
    for start_index, end_index in segment_ranges:
        split_before = _guarded_drift_split_index(
            messages,
            boundaries,
            start_index,
            end_index,
            boundary_threshold=boundary_threshold,
        )
        if split_before is None:
            split_ranges.append((start_index, end_index))
            continue
        split_ranges.extend([(start_index, split_before), (split_before, end_index)])
    return split_ranges


def _guarded_drift_split_index(
    messages: list[ReconstructedThreadMessage],
    boundaries: list[AdjacentWindowBoundary],
    start_index: int,
    end_index: int,
    *,
    boundary_threshold: float,
) -> int | None:
    if end_index - start_index < DRIFT_GUARDRAIL_LONG_SEGMENT_MESSAGE_COUNT:
        return None

    eligible = [
        boundary
        for boundary in boundaries
        if _is_guarded_drift_split_candidate(
            messages,
            boundary,
            start_index,
            end_index,
            boundary_threshold=boundary_threshold,
        )
    ]
    if not eligible:
        return None

    weakest = min(
        eligible,
        key=lambda boundary: (
            boundary.continuity_score,
            boundary.split_before_message_index,
        ),
    )
    return weakest.split_before_message_index


def _is_guarded_drift_split_candidate(
    messages: list[ReconstructedThreadMessage],
    boundary: AdjacentWindowBoundary,
    start_index: int,
    end_index: int,
    *,
    boundary_threshold: float,
) -> bool:
    split_before = boundary.split_before_message_index
    if boundary.boundary:
        return False
    if split_before <= start_index or split_before >= end_index:
        return False
    if boundary.continuity_score < boundary_threshold:
        return False
    if (
        boundary.continuity_score
        > boundary_threshold + DRIFT_GUARDRAIL_THRESHOLD_MARGIN
    ):
        return False
    if boundary.structural_continuity > 0.0:
        return False

    left = _message_at(messages, split_before - 1)
    right = _message_at(messages, split_before)
    if left is None or right is None:
        return False
    if not _is_substantive_message(left) or not _is_substantive_message(right):
        return False
    if right.role != "user":
        return False
    # Request/answer handoffs are protected by structural continuity and should
    # not be reintroduced as drift splits by the long-segment guardrail.
    if left.role == "user" and right.role == "assistant":
        return False
    return True


def _absorb_empty_segment_ranges(
    messages: list[ReconstructedThreadMessage],
    segment_ranges: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    if len(segment_ranges) <= 1:
        return segment_ranges

    cleaned = [[start_index, end_index] for start_index, end_index in segment_ranges]
    index = 0
    while index < len(cleaned):
        start_index, end_index = cleaned[index]
        if not _segment_is_effectively_empty(messages[start_index:end_index]):
            index += 1
            continue
        if len(cleaned) == 1:
            break
        if index > 0:
            cleaned[index - 1][1] = end_index
            del cleaned[index]
            continue
        cleaned[1][0] = start_index
        del cleaned[0]
    return [(start_index, end_index) for start_index, end_index in cleaned]


def _segment_is_effectively_empty(
    segment_messages: list[ReconstructedThreadMessage],
) -> bool:
    segment_text = "\n\n".join(
        message.text for message in segment_messages if message.text
    )
    return _non_whitespace_char_count(segment_text) == 0


def analyze_intra_thread_topics(
    input_path: Path,
    *,
    backend_name: str = "deterministic-hash",
    model: str | None = None,
    window_size: int = DEFAULT_INTRA_THREAD_WINDOW_SIZE,
    window_stride: int = DEFAULT_INTRA_THREAD_WINDOW_STRIDE,
    boundary_threshold: float = DEFAULT_INTRA_THREAD_BOUNDARY_THRESHOLD,
    overwrite: bool = False,
    max_input_bytes: int | None = None,
    chunk_overlap_bytes: int | None = None,
    aggregate: str | None = None,
    backend_options: dict[str, Any] | None = None,
    backend: EmbeddingBackend | None = None,
    merge_segments: bool = False,
) -> dict[str, Any]:
    normalized_input = _normalize_parsed_path(input_path)
    parsed_files = discover_parsed_jsonl(normalized_input)
    if window_size <= 0:
        raise IntraThreadTopicsError("--window-size must be > 0")
    if window_stride <= 0:
        raise IntraThreadTopicsError("--window-stride must be > 0")
    if not 0.0 <= boundary_threshold <= 1.0:
        raise IntraThreadTopicsError("--boundary-threshold must be between 0.0 and 1.0")

    if backend is None:
        backend = resolve_embedding_backend(
            backend_name=backend_name,
            model=model,
            max_input_bytes=max_input_bytes,
            chunk_overlap_bytes=chunk_overlap_bytes,
            aggregate=aggregate,
            backend_options=backend_options,
        )

    written_boundaries: list[Path] = []
    written_segments: list[Path] = []
    written_segment_merges: list[Path] = []
    total_windows = 0
    total_boundaries = 0
    total_segments = 0
    total_segment_merge_groups = 0

    for parsed_path in parsed_files:
        boundaries_path = intra_thread_boundaries_artifact_path(parsed_path)
        segments_path = intra_thread_segments_artifact_path(parsed_path)
        segment_merge_path = intra_thread_segment_merge_artifact_path(parsed_path)
        segment_merge_diagnostics_path = (
            intra_thread_segment_merge_diagnostics_artifact_path(parsed_path)
        )
        topic_candidates_path = intra_thread_topic_candidates_artifact_path(parsed_path)
        existing_artifacts = [
            path
            for path in (
                boundaries_path,
                segments_path,
                segment_merge_path if merge_segments else None,
                segment_merge_diagnostics_path if merge_segments else None,
                topic_candidates_path if merge_segments else None,
            )
            if path is not None and path.exists()
        ]
        if not overwrite and existing_artifacts:
            existing_path = existing_artifacts[0]
            raise IntraThreadTopicsError(
                f"artifact already exists: {existing_path} (rerun with --overwrite)"
            )

        messages = reconstruct_thread_messages(parsed_path)
        windows = build_sliding_windows(
            messages,
            window_size=window_size,
            window_stride=window_stride,
        )
        embeddings = build_window_embedding_records(windows, backend=backend)
        boundaries = detect_adjacent_boundaries(
            messages,
            windows,
            embeddings,
            threshold=boundary_threshold,
        )
        segments = build_contiguous_segments(
            messages,
            boundaries,
            boundary_threshold=boundary_threshold,
        )

        written_boundaries.append(
            _write_jsonl(boundaries_path, [_boundary_row(row) for row in boundaries])
        )
        written_segments.append(
            _write_jsonl(segments_path, [_segment_row(row) for row in segments])
        )
        if merge_segments:
            segment_merge_groups, segment_merge_diagnostics = (
                build_segment_merge_groups_with_diagnostics(
                    messages,
                    segments,
                    backend=backend,
                )
            )
            written_segment_merges.append(
                _write_jsonl(
                    segment_merge_path,
                    [_segment_merge_group_row(row) for row in segment_merge_groups],
                )
            )
            written_segment_merges.append(
                _write_jsonl(
                    segment_merge_diagnostics_path,
                    [
                        _segment_merge_pair_diagnostic_row(row)
                        for row in segment_merge_diagnostics
                    ],
                )
            )
            topic_candidate_rows = [
                _topic_candidate_row(row, messages=messages, segments=segments)
                for row in _sorted_topic_candidate_diagnostics(
                    segment_merge_diagnostics
                )
                if row.similarity >= DEFAULT_SEGMENT_MERGE_SIMILARITY_THRESHOLD
            ]
            written_segment_merges.append(
                _write_jsonl(topic_candidates_path, topic_candidate_rows)
            )
            total_segment_merge_groups += len(segment_merge_groups)

        total_windows += len(windows)
        total_boundaries += sum(1 for row in boundaries if row.boundary)
        total_segments += len(segments)

    return {
        "threads": len(parsed_files),
        "windows": total_windows,
        "boundaries": total_boundaries,
        "segments": total_segments,
        "embedding_model": backend.model_id,
        "boundaries_artifacts": written_boundaries,
        "segments_artifacts": written_segments,
        "segment_merge_groups": total_segment_merge_groups,
        "segment_merge_artifacts": written_segment_merges,
    }


def write_intra_thread_topic_reports(
    input_path: Path,
    *,
    boundary_threshold: float = DEFAULT_INTRA_THREAD_BOUNDARY_THRESHOLD,
) -> dict[str, Any]:
    normalized_input = _normalize_parsed_path(input_path)
    parsed_files = discover_parsed_jsonl(normalized_input)
    if not 0.0 <= boundary_threshold <= 1.0:
        raise IntraThreadTopicsError("--boundary-threshold must be between 0.0 and 1.0")

    report_paths: list[Path] = []
    for parsed_path in parsed_files:
        messages = reconstruct_thread_messages(parsed_path)
        boundaries = _load_intra_thread_jsonl(
            intra_thread_boundaries_artifact_path(parsed_path),
            "boundaries.jsonl",
        )
        segments = _load_intra_thread_jsonl(
            intra_thread_segments_artifact_path(parsed_path),
            "segments.jsonl",
        )
        segment_merges = _load_optional_intra_thread_jsonl(
            intra_thread_segment_merge_artifact_path(parsed_path),
        )
        segment_merge_diagnostics = _load_optional_intra_thread_jsonl(
            intra_thread_segment_merge_diagnostics_artifact_path(parsed_path),
        )
        report_paths.append(
            _write_text(
                intra_thread_report_artifact_path(parsed_path),
                render_intra_thread_topic_report(
                    messages,
                    boundaries,
                    segments,
                    segment_merges=segment_merges,
                    segment_merge_diagnostics=segment_merge_diagnostics,
                    boundary_threshold=boundary_threshold,
                ),
            )
        )

    return {
        "threads": len(parsed_files),
        "reports": report_paths,
    }


def render_intra_thread_topic_report(
    messages: list[ReconstructedThreadMessage],
    boundaries: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    *,
    segment_merges: list[dict[str, Any]] | None = None,
    segment_merge_diagnostics: list[dict[str, Any]] | None = None,
    boundary_threshold: float = DEFAULT_INTRA_THREAD_BOUNDARY_THRESHOLD,
) -> str:
    conversation_id = messages[0].conversation_id if messages else "unknown"
    boundary_schema_versions = sorted(
        {
            str(row.get("schema_version"))
            for row in boundaries
            if row.get("schema_version") is not None
        }
    )
    fired_boundaries = [row for row in boundaries if row.get("boundary") is True]
    lines = [
        f"# Intra-thread Topics Report: {conversation_id}",
        "",
        "## Summary",
        "",
        f"- Conversation ID: `{conversation_id}`",
        f"- Total messages: {len(messages)}",
        f"- Boundary schema version: {_display_schema_versions(boundary_schema_versions)}",
        f"- Boundary rows: {len(boundaries)}",
        f"- Fired boundaries: {len(fired_boundaries)}",
        f"- Segments: {len(segments)}",
        f"- Near-threshold distance: {REPORT_NEAR_THRESHOLD_DISTANCE:.2f}",
        f"- Boundary threshold used for diagnostics: {boundary_threshold:.4f}",
        "",
        "## Segment Preview",
        "",
    ]

    if segments:
        for index, segment in enumerate(segments):
            lines.extend(
                _render_segment_preview(
                    index,
                    segment,
                    messages,
                    boundaries,
                    boundary_threshold=boundary_threshold,
                )
            )
    else:
        lines.append("_No segments found._")
        lines.append("")

    lines.extend(["## Fired Boundaries", ""])
    if fired_boundaries:
        for boundary in fired_boundaries:
            lines.extend(_render_boundary_preview(boundary, messages))
    else:
        lines.append("_No fired boundaries._")
        lines.append("")

    suppressed = [
        row
        for row in boundaries
        if _is_suppressed_high_signal_candidate(
            row,
            boundary_threshold=boundary_threshold,
        )
    ]
    lines.extend(["## Suppressed High-Signal Candidates", ""])
    if suppressed:
        for boundary in suppressed:
            lines.extend(_render_boundary_preview(boundary, messages))
    else:
        lines.append("_No suppressed high-signal candidates._")
        lines.append("")

    near_threshold = [
        row
        for row in boundaries
        if abs(_float_field(row, "continuity_score") - boundary_threshold)
        <= REPORT_NEAR_THRESHOLD_DISTANCE
    ]
    lines.extend(["## Near-Threshold Candidates", ""])
    if near_threshold:
        for boundary in near_threshold:
            lines.extend(_render_boundary_preview(boundary, messages))
    else:
        lines.append("_No near-threshold candidates._")
        lines.append("")

    lines.extend(
        _render_drift_diagnostics(
            messages,
            boundaries,
            segments,
            boundary_threshold=boundary_threshold,
        )
    )
    lines.extend(
        _render_segment_merge_diagnostics(
            messages,
            segments,
            segment_merges,
            segment_merge_diagnostics,
        )
    )

    return "\n".join(lines).rstrip() + "\n"


def _render_segment_preview(
    index: int,
    segment: dict[str, Any],
    messages: list[ReconstructedThreadMessage],
    boundaries: list[dict[str, Any]],
    *,
    boundary_threshold: float,
) -> list[str]:
    start_index = _int_field(segment, "start_index")
    end_index = _int_field(segment, "end_index")
    message_count = _int_field(segment, "message_count")
    segment_messages = _messages_in_range(messages, start_index, end_index)
    lines = [
        f"### Segment {index}",
        "",
        f"- Range: `{start_index}-{end_index}`",
        f"- Message count: {message_count}",
        "- Start source: `"
        + _segment_start_source(
            segment,
            boundaries,
            messages,
            boundary_threshold=boundary_threshold,
        )
        + "`",
        "- First messages:",
    ]
    for message in segment_messages[:REPORT_PREVIEW_MESSAGES_PER_EDGE]:
        lines.append(_message_preview_bullet(message))
    if not segment_messages:
        lines.append("  - _No messages in range._")
    lines.append("- Last messages:")
    tail = segment_messages[-REPORT_PREVIEW_MESSAGES_PER_EDGE:]
    for message in tail:
        lines.append(_message_preview_bullet(message))
    if not tail:
        lines.append("  - _No messages in range._")
    lines.append("")
    return lines


def _render_boundary_preview(
    boundary: dict[str, Any],
    messages: list[ReconstructedThreadMessage],
) -> list[str]:
    split_before = _int_field(boundary, "split_before_message_index")
    left = _message_at(messages, split_before - 1)
    right = _message_at(messages, split_before)
    lines = [
        f"### split_before_message_index={split_before}",
        "",
        f"- boundary: `{bool(boundary.get('boundary'))}`",
        f"- similarity: {_float_field(boundary, 'similarity'):.4f}",
        f"- lexical_similarity: {_float_field(boundary, 'lexical_similarity'):.4f}",
        f"- structural_continuity: {_float_field(boundary, 'structural_continuity'):.4f}",
        f"- continuity_score: {_float_field(boundary, 'continuity_score'):.4f}",
        f"- left: {_message_preview_inline(left)}",
        f"- right: {_message_preview_inline(right)}",
        "",
    ]
    return lines


def _segment_start_source(
    segment: dict[str, Any],
    boundaries: list[dict[str, Any]],
    messages: list[ReconstructedThreadMessage],
    *,
    boundary_threshold: float,
) -> str:
    start_index = _int_field(segment, "start_index")
    if start_index == 0:
        return "conversation_start"
    matching_boundary = next(
        (
            boundary
            for boundary in boundaries
            if _int_field(boundary, "split_before_message_index") == start_index
        ),
        None,
    )
    if matching_boundary is None:
        return "derived_or_cleanup"
    if matching_boundary.get("boundary") is True:
        return "boundary"
    if _is_guarded_drift_report_candidate(
        matching_boundary,
        messages,
        boundary_threshold=boundary_threshold,
    ):
        return "drift_guardrail"
    return "derived_or_cleanup"


def _is_guarded_drift_report_candidate(
    boundary: dict[str, Any],
    messages: list[ReconstructedThreadMessage],
    *,
    boundary_threshold: float,
) -> bool:
    continuity_score = _float_field(boundary, "continuity_score")
    if boundary.get("boundary") is True:
        return False
    if continuity_score < boundary_threshold:
        return False
    if continuity_score > boundary_threshold + DRIFT_GUARDRAIL_THRESHOLD_MARGIN:
        return False
    if _float_field(boundary, "structural_continuity") > 0.0:
        return False
    split_before = _int_field(boundary, "split_before_message_index")
    left = _message_at(messages, split_before - 1)
    right = _message_at(messages, split_before)
    if left is None or right is None:
        return False
    if not _is_substantive_message(left) or not _is_substantive_message(right):
        return False
    return right.role == "user" and not (
        left.role == "user" and right.role == "assistant"
    )


def _render_drift_diagnostics(
    messages: list[ReconstructedThreadMessage],
    boundaries: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    *,
    boundary_threshold: float,
) -> list[str]:
    lines = [
        "## Drift Diagnostics",
        "",
        f"- Long segment threshold: {REPORT_LONG_SEGMENT_MESSAGE_COUNT} messages",
        f"- Weakest candidates shown per segment: {REPORT_DRIFT_WEAKEST_CANDIDATES}",
        f"- Drift near-threshold distance: {REPORT_DRIFT_NEAR_THRESHOLD_DISTANCE:.2f}",
        f"- Low-score run ceiling: {REPORT_DRIFT_LOW_SCORE_CEILING:.2f}",
        f"- Low-score run minimum length: {REPORT_DRIFT_LOW_SCORE_RUN_LENGTH}",
        "",
    ]
    long_segment_seen = False
    for index, segment in enumerate(segments):
        message_count = _int_field(segment, "message_count")
        if message_count < REPORT_LONG_SEGMENT_MESSAGE_COUNT:
            continue
        long_segment_seen = True
        lines.extend(
            _render_segment_drift_diagnostics(
                index,
                segment,
                boundaries,
                messages,
                boundary_threshold=boundary_threshold,
            )
        )

    if not long_segment_seen:
        lines.append("_No long segments above drift diagnostic threshold._")
        lines.append("")
    return lines


def _render_segment_drift_diagnostics(
    index: int,
    segment: dict[str, Any],
    boundaries: list[dict[str, Any]],
    messages: list[ReconstructedThreadMessage],
    *,
    boundary_threshold: float,
) -> list[str]:
    start_index = _int_field(segment, "start_index")
    end_index = _int_field(segment, "end_index")
    message_count = _int_field(segment, "message_count")
    internal_candidates = _internal_boundary_candidates(segment, boundaries)
    scores = [_float_field(row, "continuity_score") for row in internal_candidates]
    lines = [
        f"### Segment {index} Drift",
        "",
        f"- Range: `{start_index}-{end_index}`",
        f"- Message count: {message_count}",
        f"- Internal candidates: {len(internal_candidates)}",
    ]
    if not scores:
        lines.append("- Score summary: _No internal candidates._")
        lines.append("")
        return lines

    lines.extend(
        [
            f"- Min continuity_score: {min(scores):.4f}",
            f"- Median continuity_score: {_median(scores):.4f}",
            f"- P10 continuity_score: {_percentile(scores, 0.10):.4f}",
            "",
            "#### Weakest Internal Candidates",
            "",
        ]
    )
    weakest = sorted(
        internal_candidates,
        key=lambda row: (
            _float_field(row, "continuity_score"),
            _int_field(row, "split_before_message_index"),
        ),
    )[:REPORT_DRIFT_WEAKEST_CANDIDATES]
    if weakest:
        for boundary in weakest:
            lines.extend(_render_boundary_preview(boundary, messages))
    else:
        lines.append("_No internal candidates._")
        lines.append("")

    lines.extend(["#### Near-Threshold Internal Candidates", ""])
    near_threshold = [
        row
        for row in internal_candidates
        if abs(_float_field(row, "continuity_score") - boundary_threshold)
        <= REPORT_DRIFT_NEAR_THRESHOLD_DISTANCE
    ]
    if near_threshold:
        for boundary in near_threshold:
            lines.extend(_render_boundary_preview(boundary, messages))
    else:
        lines.append("_No near-threshold internal candidates._")
        lines.append("")

    lines.extend(["#### Low-Score Runs", ""])
    low_score_runs = _low_score_runs(internal_candidates)
    if low_score_runs:
        for run in low_score_runs:
            lines.extend(_render_low_score_run(run, messages))
    else:
        lines.append("_No low-score runs detected._")
        lines.append("")
    return lines


def _internal_boundary_candidates(
    segment: dict[str, Any],
    boundaries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    start_index = _int_field(segment, "start_index")
    end_index = _int_field(segment, "end_index")
    return [
        row
        for row in boundaries
        if start_index < _int_field(row, "split_before_message_index") <= end_index
    ]


def _low_score_runs(
    internal_candidates: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    runs: list[list[dict[str, Any]]] = []
    current_run: list[dict[str, Any]] = []
    for boundary in sorted(
        internal_candidates,
        key=lambda row: _int_field(row, "split_before_message_index"),
    ):
        if _float_field(boundary, "continuity_score") < REPORT_DRIFT_LOW_SCORE_CEILING:
            current_run.append(boundary)
            continue
        if len(current_run) >= REPORT_DRIFT_LOW_SCORE_RUN_LENGTH:
            runs.append(current_run)
        current_run = []
    if len(current_run) >= REPORT_DRIFT_LOW_SCORE_RUN_LENGTH:
        runs.append(current_run)
    return runs


def _render_low_score_run(
    run: list[dict[str, Any]],
    messages: list[ReconstructedThreadMessage],
) -> list[str]:
    weakest = min(
        run,
        key=lambda row: (
            _float_field(row, "continuity_score"),
            _int_field(row, "split_before_message_index"),
        ),
    )
    start_split = _int_field(run[0], "split_before_message_index")
    end_split = _int_field(run[-1], "split_before_message_index")
    weakest_split = _int_field(weakest, "split_before_message_index")
    return [
        f"##### Low-score run {start_split}-{end_split}",
        "",
        f"- Start split_before_message_index: {start_split}",
        f"- End split_before_message_index: {end_split}",
        f"- Run length: {len(run)}",
        f"- Min continuity_score: {_float_field(weakest, 'continuity_score'):.4f}",
        f"- Weakest split_before_message_index: {weakest_split}",
        f"- left: {_message_preview_inline(_message_at(messages, weakest_split - 1))}",
        f"- right: {_message_preview_inline(_message_at(messages, weakest_split))}",
        "",
    ]


def _render_segment_merge_diagnostics(
    messages: list[ReconstructedThreadMessage],
    segments: list[dict[str, Any]],
    segment_merges: list[dict[str, Any]] | None,
    segment_merge_diagnostics: list[dict[str, Any]] | None,
) -> list[str]:
    lines = ["## Segment Merge Diagnostics", ""]
    if segment_merges is None:
        lines.append(
            "_Segment merge artifact not found. Run with --merge-segments "
            "to generate diagnostics._"
        )
        lines.append("")
        return lines

    merged_groups = [
        row
        for row in segment_merges
        if len(_string_list_field(row, "segment_ids")) > 1
    ]
    lines.extend(
        [
            "### Merge Summary",
            "",
            f"- Segments: {len(segments)}",
            f"- Merge groups: {len(segment_merges)}",
            f"- Merged groups: {len(merged_groups)}",
            f"- Merge similarity threshold: {DEFAULT_SEGMENT_MERGE_SIMILARITY_THRESHOLD:.2f}",
            "- Minimum lexical similarity for merge: "
            f"{DEFAULT_SEGMENT_MERGE_MIN_LEXICAL_SIMILARITY:.2f}",
            f"- Minimum merge candidate message count: {SEGMENT_MERGE_MIN_MESSAGE_COUNT}",
            "- Note: experimental Phase4 baseline; inspect for stylistic over-merge.",
            "",
            "### Merge Groups",
            "",
        ]
    )
    if segment_merges:
        for group in segment_merges:
            lines.extend(_render_segment_merge_group(group, segments, messages))
    else:
        lines.append("_No merge groups found._")
        lines.append("")

    lines.extend(["### Pairwise Segment Similarities", ""])
    if segment_merge_diagnostics:
        for row in _sorted_segment_merge_pair_diagnostics(
            segment_merge_diagnostics
        )[:REPORT_SEGMENT_MERGE_PAIR_LIMIT]:
            lines.extend(_render_segment_pair_diagnostic(row, segments, messages))
    elif segment_merge_diagnostics is None:
        lines.append(
            "_Segment merge pair diagnostics artifact not found. Rerun with "
            "--merge-segments to capture pair decisions._"
        )
        lines.append("")
    else:
        lines.append("_No pair diagnostics found._")
        lines.append("")

    for title, band in (
        ("### Strong Anchor Merge Candidates", "strong"),
        ("### Gray-Zone Merge Candidates", "gray"),
        ("### Weak Anchor Merge Candidates", "weak"),
    ):
        lines.extend([title, ""])
        band_rows = [
            row
            for row in _sorted_segment_merge_anchor_band_diagnostics(
                segment_merge_diagnostics or []
            )
            if row.get("lexical_anchor_band") == band
        ]
        if band_rows:
            for row in band_rows[:REPORT_SEGMENT_MERGE_PAIR_LIMIT]:
                lines.extend(_render_segment_pair_diagnostic(row, segments, messages))
        else:
            lines.append(f"_No {band} anchor candidates._")
            lines.append("")

    lines.extend(["### Suspicious Merge Candidates", ""])
    suspicious = [
        row
        for row in _sorted_segment_merge_pair_diagnostics(
            segment_merge_diagnostics or []
        )
        if _segment_merge_suspicious_reason(row) is not None
    ]
    if suspicious:
        for row in suspicious[:REPORT_SEGMENT_MERGE_PAIR_LIMIT]:
            reasons = _segment_merge_suspicious_reasons(row)
            if reasons:
                lines.extend(["#### Suspicious reasons", ""])
                lines.extend(f"- {reason}" for reason in reasons)
                lines.append("")
            lines.extend(_render_segment_pair_diagnostic(row, segments, messages))
    else:
        lines.append("_No suspicious merge candidates detected._")
        lines.append("")
    return lines


def _render_segment_merge_group(
    group: dict[str, Any],
    segments: list[dict[str, Any]],
    messages: list[ReconstructedThreadMessage],
) -> list[str]:
    segment_ids = _string_list_field(group, "segment_ids")
    lines = [
        f"#### Group `{group.get('group_id', 'unknown')}`",
        "",
        f"- Segment IDs: {', '.join(f'`{segment_id}`' for segment_id in segment_ids)}",
        f"- Segment index gaps: {_segment_group_gap_display(segment_ids, segments)}",
        "- Segments:",
    ]
    for segment_id in segment_ids:
        segment = _segment_by_id(segments, segment_id)
        if segment is None:
            lines.append(f"  - `{segment_id}`: _missing segment row_")
            continue
        index = _segment_index_by_id(segments, segment_id)
        start_index = _int_field(segment, "start_index")
        end_index = _int_field(segment, "end_index")
        segment_messages = _messages_in_range(messages, start_index, end_index)
        first = segment_messages[0] if segment_messages else None
        last = segment_messages[-1] if segment_messages else None
        lines.extend(
            [
                f"  - index `{index}` range `{start_index}-{end_index}` "
                f"messages `{_int_field(segment, 'message_count')}`",
                f"    - first: {_message_preview_inline(first)}",
                f"    - last: {_message_preview_inline(last)}",
            ]
        )
    lines.append("")
    return lines


def _render_segment_pair_diagnostic(
    row: dict[str, Any],
    segments: list[dict[str, Any]],
    messages: list[ReconstructedThreadMessage],
) -> list[str]:
    left_id = str(row.get("left_segment_id", ""))
    right_id = str(row.get("right_segment_id", ""))
    left = _segment_by_id(segments, left_id)
    right = _segment_by_id(segments, right_id)
    lines = [
        "#### "
        f"{_int_field(row, 'left_segment_index')} -> "
        f"{_int_field(row, 'right_segment_index')}",
        "",
        f"- left: `{left_id}` {_segment_range_display(left)}",
        f"- right: `{right_id}` {_segment_range_display(right)}",
        f"- segment_index_gap: {_int_field(row, 'segment_index_gap')}",
        f"- similarity: {_float_field(row, 'similarity'):.4f}",
        f"- lexical_similarity: {_float_field(row, 'lexical_similarity'):.4f}",
        "- shared_top_tokens: "
        f"{_token_list_display(_string_list_field(row, 'shared_top_tokens'))}",
        f"- left_top_tokens: {_token_list_display(_string_list_field(row, 'left_top_tokens'))}",
        f"- right_top_tokens: {_token_list_display(_string_list_field(row, 'right_top_tokens'))}",
        "- filtered_shared_top_tokens: "
        f"{_token_list_display(_string_list_field(row, 'filtered_shared_top_tokens'))}",
        "- filtered_left_top_tokens: "
        f"{_token_list_display(_string_list_field(row, 'filtered_left_top_tokens'))}",
        "- filtered_right_top_tokens: "
        f"{_token_list_display(_string_list_field(row, 'filtered_right_top_tokens'))}",
        f"- lexical_anchor_score: {_float_field(row, 'lexical_anchor_score'):.4f}",
        f"- lexical_anchor_band: `{row.get('lexical_anchor_band', 'unknown')}`",
        "- left_content_type_flags: "
        f"{_token_list_display(_content_type_flags(row, 'left'))}",
        "- right_content_type_flags: "
        f"{_token_list_display(_content_type_flags(row, 'right'))}",
        f"- decision: `{row.get('decision', 'unknown')}`",
        f"- reason: {row.get('reason', '')}",
    ]
    left_preview = _segment_edge_preview(left, messages)
    right_preview = _segment_edge_preview(right, messages)
    lines.extend(
        [
            f"- left first: {left_preview[0]}",
            f"- left last: {left_preview[1]}",
            f"- right first: {right_preview[0]}",
            f"- right last: {right_preview[1]}",
            "",
        ]
    )
    return lines


def _segment_merge_suspicious_reasons(row: dict[str, Any]) -> list[str]:
    if row.get("decision") != "merged":
        return []
    reasons: list[str] = []
    if (
        _float_field(row, "similarity")
        >= DEFAULT_SEGMENT_MERGE_SIMILARITY_THRESHOLD
        and _float_field(row, "lexical_similarity") < 0.05
    ):
        reasons.append("low lexical anchor despite high embedding similarity")
    if (
        _float_field(row, "similarity")
        >= DEFAULT_SEGMENT_MERGE_SIMILARITY_THRESHOLD
        and _float_field(row, "lexical_anchor_score") < 0.10
    ):
        reasons.append("low filtered lexical anchor despite high embedding similarity")
    left_flags = set(_content_type_flags(row, "left"))
    right_flags = set(_content_type_flags(row, "right"))
    if _content_type_mismatch(left_flags, right_flags):
        reasons.append("content type mismatch in merged pair")
    if _int_field(row, "segment_index_gap") > 2:
        reasons.append("large segment gap")
    return reasons


def _segment_merge_suspicious_reason(row: dict[str, Any]) -> str | None:
    reasons = _segment_merge_suspicious_reasons(row)
    return reasons[0] if reasons else None


def _topic_candidate_role(candidate_type: str) -> str:
    if candidate_type == "strong":
        return "primary_merge"
    if candidate_type == "gray":
        return "review_candidate"
    return "suspicious_candidate"


def _topic_candidate_reasons(diagnostic: SegmentMergePairDiagnostic) -> list[str]:
    reasons: list[str] = []
    if diagnostic.lexical_anchor_score < 0.10:
        reasons.append("low filtered lexical anchor")
    if _content_type_mismatch(
        set(diagnostic.content_type_flags.get("left", ())),
        set(diagnostic.content_type_flags.get("right", ())),
    ):
        reasons.append("content type mismatch")
    if diagnostic.decision in {
        "blocked_adjacent_group",
        "below_threshold",
        "short_segment",
        "blocked_low_lexical_anchor",
    }:
        reasons.append(diagnostic.decision)
    return reasons


def _content_type_mismatch(left_flags: set[str], right_flags: set[str]) -> bool:
    return (
        "summary_like" in left_flags
        and bool(right_flags & {"prose_like", "name_list_like"})
    ) or (
        "summary_like" in right_flags
        and bool(left_flags & {"prose_like", "name_list_like"})
    )


def _sorted_segment_merge_pair_diagnostics(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            -_float_field(row, "similarity"),
            _int_field(row, "left_segment_index"),
            _int_field(row, "right_segment_index"),
        ),
    )


def _sorted_segment_merge_anchor_band_diagnostics(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            -_float_field(row, "similarity"),
            _float_field(row, "lexical_anchor_score"),
            _int_field(row, "left_segment_index"),
            _int_field(row, "right_segment_index"),
        ),
    )


def _sorted_topic_candidate_diagnostics(
    diagnostics: list[SegmentMergePairDiagnostic],
) -> list[SegmentMergePairDiagnostic]:
    return sorted(
        diagnostics,
        key=lambda row: (
            -row.similarity,
            row.lexical_anchor_score,
            row.left_segment_index,
            row.right_segment_index,
        ),
    )


def _segment_group_gap_display(
    segment_ids: list[str],
    segments: list[dict[str, Any]],
) -> str:
    indices = [
        index
        for segment_id in segment_ids
        for index in [_segment_index_by_id(segments, segment_id)]
        if index >= 0
    ]
    if len(indices) < 2:
        return "`none`"
    gaps = [
        str(right - left - 1)
        for left, right in zip(sorted(indices), sorted(indices)[1:], strict=False)
    ]
    return ", ".join(f"`{gap}`" for gap in gaps)


def _segment_by_id(
    segments: list[dict[str, Any]],
    segment_id: str,
) -> dict[str, Any] | None:
    return next(
        (segment for segment in segments if segment.get("segment_id") == segment_id),
        None,
    )


def _segment_index_by_id(segments: list[dict[str, Any]], segment_id: str) -> int:
    for index, segment in enumerate(segments):
        if segment.get("segment_id") == segment_id:
            return index
    return -1


def _segment_range_display(segment: dict[str, Any] | None) -> str:
    if segment is None:
        return "`unknown`"
    return f"`{_int_field(segment, 'start_index')}-{_int_field(segment, 'end_index')}`"


def _segment_edge_preview(
    segment: dict[str, Any] | None,
    messages: list[ReconstructedThreadMessage],
) -> tuple[str, str]:
    if segment is None:
        return "_missing segment_", "_missing segment_"
    segment_messages = _messages_in_range(
        messages,
        _int_field(segment, "start_index"),
        _int_field(segment, "end_index"),
    )
    if not segment_messages:
        return "_no messages_", "_no messages_"
    return (
        _message_preview_inline(segment_messages[0]),
        _message_preview_inline(segment_messages[-1]),
    )


def _segment_preview_text(
    segment: ContiguousSegment,
    messages: list[ReconstructedThreadMessage],
) -> str:
    segment_messages = _messages_in_range(messages, segment.start_index, segment.end_index)
    if not segment_messages:
        return ""
    first = _message_preview_inline(segment_messages[0])
    last = _message_preview_inline(segment_messages[-1])
    return first if first == last else f"{first} ... {last}"


def _token_list_display(tokens: list[str]) -> str:
    if not tokens:
        return "`none`"
    return ", ".join(f"`{token}`" for token in tokens)


def _is_suppressed_high_signal_candidate(
    boundary: dict[str, Any],
    *,
    boundary_threshold: float,
) -> bool:
    if boundary.get("boundary") is True:
        return False
    lexical_similarity = _float_field(boundary, "lexical_similarity")
    structural_continuity = _float_field(boundary, "structural_continuity")
    if lexical_similarity <= 0.0 and structural_continuity <= 0.0:
        return False
    continuity_score = _float_field(boundary, "continuity_score")
    base_score = _float_field(boundary, "similarity") + (
        DEFAULT_INTRA_THREAD_LEXICAL_CONTINUITY_WEIGHT * lexical_similarity
    )
    # Keep this diagnostic section focused on candidates whose continuity
    # signals plausibly affected review, not every stable request/answer pair.
    return (
        base_score < boundary_threshold <= continuity_score
        or abs(continuity_score - boundary_threshold)
        <= REPORT_SUPPRESSED_SCORE_MARGIN
    )


def _load_intra_thread_jsonl(path: Path, artifact_name: str) -> list[dict[str, Any]]:
    if not path.exists():
        raise IntraThreadTopicsError(
            f"missing intra-thread {artifact_name}: {path} "
            "(run analyze intra-thread-topics before --report)"
        )
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise IntraThreadTopicsError(
                    f"invalid JSON in {path} at line {line_number}: {exc.msg}"
                ) from exc
            if not isinstance(row, dict):
                raise IntraThreadTopicsError(
                    f"invalid row in {path} at line {line_number}: expected object"
                )
            rows.append(row)
    return rows


def _load_optional_intra_thread_jsonl(path: Path) -> list[dict[str, Any]] | None:
    if not path.exists():
        return None
    return _load_intra_thread_jsonl(path, path.name)


def _write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return path


def _message_preview_bullet(message: ReconstructedThreadMessage) -> str:
    return (
        f"  - `{message.ordinal}` `{message.role}` `{message.message_id}`: "
        f"{_preview_text(message.text)}"
    )


def _message_preview_inline(message: ReconstructedThreadMessage | None) -> str:
    if message is None:
        return "_out of range_"
    return (
        f"`{message.ordinal}` `{message.role}` `{message.message_id}`: "
        f"{_preview_text(message.text)}"
    )


def _preview_text(text: str) -> str:
    collapsed = " ".join(text.split())
    if not collapsed:
        return "_empty_"
    if len(collapsed) <= REPORT_PREVIEW_TEXT_CHARS:
        return collapsed
    return collapsed[: REPORT_PREVIEW_TEXT_CHARS - 3] + "..."


def _messages_in_range(
    messages: list[ReconstructedThreadMessage],
    start_index: int,
    end_index: int,
) -> list[ReconstructedThreadMessage]:
    if start_index < 0 or end_index < start_index:
        return []
    return messages[start_index : min(end_index + 1, len(messages))]


def _message_at(
    messages: list[ReconstructedThreadMessage],
    index: int,
) -> ReconstructedThreadMessage | None:
    if 0 <= index < len(messages):
        return messages[index]
    return None


def _int_field(row: dict[str, Any], field: str) -> int:
    value = row.get(field)
    if isinstance(value, int):
        return value
    return 0


def _float_field(row: dict[str, Any], field: str) -> float:
    value = row.get(field)
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def _string_list_field(row: dict[str, Any], field: str) -> list[str]:
    value = row.get(field)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _content_type_flags(row: dict[str, Any], side: str) -> list[str]:
    value = row.get("content_type_flags")
    if not isinstance(value, dict):
        return []
    flags = value.get(side)
    if not isinstance(flags, list):
        return []
    return [flag for flag in flags if isinstance(flag, str)]


def _display_schema_versions(schema_versions: list[str]) -> str:
    if not schema_versions:
        return "`unknown`"
    return ", ".join(f"`{version}`" for version in schema_versions)


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    midpoint = len(sorted_values) // 2
    if len(sorted_values) % 2:
        return sorted_values[midpoint]
    return (sorted_values[midpoint - 1] + sorted_values[midpoint]) / 2


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    index = round((len(sorted_values) - 1) * percentile)
    return sorted_values[index]


def _non_whitespace_char_count(text: str) -> int:
    return sum(1 for char in text if not char.isspace())


def lexical_token_set(text: str) -> frozenset[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold().strip()
    if not normalized:
        return frozenset()

    word_tokens = tuple(
        token
        for token in LEXICAL_WORD_TOKEN_RE.findall(normalized)
        if token and not token.isspace()
    )
    if len(word_tokens) >= 2:
        return frozenset(word_tokens)

    compact = "".join(char for char in normalized if not char.isspace())
    if not compact:
        return frozenset()
    if len(compact) < 3:
        return frozenset({compact})
    return frozenset(compact[index : index + 3] for index in range(len(compact) - 2))


def lexical_jaccard_similarity(
    left_tokens: frozenset[str],
    right_tokens: frozenset[str],
) -> float:
    if not left_tokens or not right_tokens:
        return 0.0
    union = left_tokens | right_tokens
    if not union:
        return 0.0
    return len(left_tokens & right_tokens) / len(union)


def boundary_lexical_similarity(
    messages: list[ReconstructedThreadMessage],
    previous_window: SlidingMessageWindow,
    next_window: SlidingMessageWindow,
) -> float:
    # Adjacent sliding windows already share messages by construction. Use only
    # the non-overlapping boundary-side text so lexical continuity is not
    # trivially inflated by the shared overlap.
    previous_text, next_text = _boundary_exclusive_texts(
        messages,
        previous_window,
        next_window,
    )
    return lexical_jaccard_similarity(
        lexical_token_set(previous_text),
        lexical_token_set(next_text),
    )


def boundary_structural_continuity(
    messages: list[ReconstructedThreadMessage],
    previous_window: SlidingMessageWindow,
    next_window: SlidingMessageWindow,
) -> float:
    split_before_message_index = previous_window.end_index + 1
    if (
        split_before_message_index <= 0
        or split_before_message_index >= len(messages)
    ):
        return 0.0

    left_context = _nearest_substantive_message(
        messages,
        split_before_message_index - 1,
        "left",
    )
    right_context = _nearest_substantive_message(
        messages,
        split_before_message_index,
        "right",
    )
    if left_context is None or right_context is None:
        return 0.0

    left, left_index, left_skipped = left_context
    right, right_index, right_skipped = right_context
    skipped_non_substantive = left_skipped + right_skipped
    is_direct_boundary = (
        left_index == split_before_message_index - 1
        and right_index == split_before_message_index
    )

    if left.role == "user" and right.role == "assistant":
        if is_direct_boundary:
            return 1.0
        if skipped_non_substantive <= STRUCTURAL_MAX_SKIPPED_NON_SUBSTANTIVE:
            return STRUCTURAL_GAP_CONTINUITY
        return 0.0

    if is_direct_boundary and left.role == "user" and right.role == "user":
        return 0.4

    return 0.0


def _nearest_substantive_message(
    messages: list[ReconstructedThreadMessage],
    start_index: int,
    direction: str,
) -> tuple[ReconstructedThreadMessage, int, int] | None:
    if direction == "left":
        step = -1
    elif direction == "right":
        step = 1
    else:
        raise ValueError("direction must be 'left' or 'right'")

    index = start_index
    skipped_count = 0
    while 0 <= index < len(messages):
        message = messages[index]
        if _is_substantive_message(message):
            return message, index, skipped_count
        skipped_count += 1
        index += step
    return None


def _is_substantive_message(message: ReconstructedThreadMessage) -> bool:
    return (
        message.role in {"user", "assistant"}
        and _non_whitespace_char_count(message.text) > 0
    )


def _boundary_exclusive_texts(
    messages: list[ReconstructedThreadMessage],
    previous_window: SlidingMessageWindow,
    next_window: SlidingMessageWindow,
) -> tuple[str, str]:
    previous_exclusive_end = min(previous_window.end_index, next_window.start_index - 1)
    next_exclusive_start = max(next_window.start_index, previous_window.end_index + 1)
    previous_text = _joined_non_empty_message_text(
        messages[previous_window.start_index : previous_exclusive_end + 1]
    )
    next_text = _joined_non_empty_message_text(
        messages[next_exclusive_start : next_window.end_index + 1]
    )
    return previous_text, next_text


def _joined_non_empty_message_text(
    rows: list[ReconstructedThreadMessage],
) -> str:
    return "\n\n".join(row.text for row in rows if row.text)
