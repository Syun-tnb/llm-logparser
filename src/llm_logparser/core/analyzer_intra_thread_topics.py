from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
BOUNDARY_SCHEMA_VERSION = "0.3"
SEGMENT_SCHEMA_VERSION = "0.1"
LEXICAL_WORD_TOKEN_RE = re.compile(r"\w+")
HANDOFF_ROLES = frozenset({"assistant", "tool"})
HANDOFF_CONTENT_CHAR_LIMIT = 16


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


def intra_thread_topics_dir(parsed_path: Path) -> Path:
    return parsed_path.parent / "l3" / "intra-thread-topics"


def intra_thread_boundaries_artifact_path(parsed_path: Path) -> Path:
    return intra_thread_topics_dir(parsed_path) / "boundaries.jsonl"


def intra_thread_segments_artifact_path(parsed_path: Path) -> Path:
    return intra_thread_topics_dir(parsed_path) / "segments.jsonl"


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
    total_windows = 0
    total_boundaries = 0
    total_segments = 0

    for parsed_path in parsed_files:
        boundaries_path = intra_thread_boundaries_artifact_path(parsed_path)
        segments_path = intra_thread_segments_artifact_path(parsed_path)
        if not overwrite and (boundaries_path.exists() or segments_path.exists()):
            existing_path = boundaries_path if boundaries_path.exists() else segments_path
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
        segments = build_contiguous_segments(messages, boundaries)

        written_boundaries.append(
            _write_jsonl(boundaries_path, [_boundary_row(row) for row in boundaries])
        )
        written_segments.append(
            _write_jsonl(segments_path, [_segment_row(row) for row in segments])
        )

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
    }


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

    left = messages[split_before_message_index - 1]
    right = messages[split_before_message_index]

    # A direct user request followed by an assistant answer is usually one
    # semantic unit. Boundaries belong before the request or after the answer.
    if left.role == "user" and right.role == "assistant":
        return 1.0

    if left.role == "user" and _is_handoff_message(right):
        return 0.85
    if _is_handoff_message(left) and right.role == "assistant":
        return 0.7
    if left.role == "tool" and right.role == "user":
        return 0.6

    if _is_handoff_message(left) or _is_handoff_message(right):
        previous_substantive = _nearest_substantive_message(
            messages,
            split_before_message_index - 1,
            -1,
        )
        next_substantive = _nearest_substantive_message(
            messages,
            split_before_message_index,
            1,
        )
        if previous_substantive is None or next_substantive is None:
            return 0.0
        if (
            previous_substantive.role == "user"
            and next_substantive.role == "assistant"
        ):
            return 0.7
        if previous_substantive.role == "user" and next_substantive.role == "user":
            return 0.4

    return 0.0


def _is_handoff_message(message: ReconstructedThreadMessage) -> bool:
    return (
        message.role in HANDOFF_ROLES
        and _non_whitespace_char_count(message.text) <= HANDOFF_CONTENT_CHAR_LIMIT
    )


def _nearest_substantive_message(
    messages: list[ReconstructedThreadMessage],
    start_index: int,
    step: int,
) -> ReconstructedThreadMessage | None:
    index = start_index
    while 0 <= index < len(messages):
        message = messages[index]
        if not _is_handoff_message(message):
            return message
        index += step
    return None


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
