from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from llm_logparser.cli.cli import main
from llm_logparser.core.analyzer_intra_thread_topics import (
    AdjacentWindowBoundary,
    ReconstructedThreadMessage,
    analyze_intra_thread_topics,
    boundary_lexical_similarity,
    boundary_structural_continuity,
    build_contiguous_segments,
    build_segment_merge_groups,
    build_sliding_windows,
    detect_adjacent_boundaries,
    intra_thread_boundaries_artifact_path,
    intra_thread_segment_merge_artifact_path,
    intra_thread_segment_merge_diagnostics_artifact_path,
    intra_thread_report_artifact_path,
    intra_thread_segments_artifact_path,
    lexical_jaccard_similarity,
    lexical_token_set,
    reconstruct_thread_messages,
    write_intra_thread_topic_reports,
)


class StaticEmbeddingBackend:
    def __init__(self, vectors: list[list[float]], *, model_id: str = "local/test-static"):
        self.model_id = model_id
        self._vectors = vectors
        self._offset = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        end_offset = self._offset + len(texts)
        assert end_offset <= len(self._vectors)
        vectors = self._vectors[self._offset : end_offset]
        self._offset = end_offset
        return vectors


def _boundary(
    *,
    conversation_id: str,
    split_before_message_index: int,
    boundary: bool = True,
    similarity: float = 0.0,
    lexical_similarity: float = 0.0,
    structural_continuity: float = 0.0,
    continuity_score: float = 0.0,
) -> AdjacentWindowBoundary:
    return AdjacentWindowBoundary(
        provider_id="openai",
        conversation_id=conversation_id,
        previous_window_index=0,
        next_window_index=1,
        previous_window_message_ids=(),
        next_window_message_ids=(),
        similarity=similarity,
        lexical_similarity=lexical_similarity,
        structural_continuity=structural_continuity,
        continuity_score=continuity_score,
        boundary=boundary,
        split_after_message_index=split_before_message_index - 1,
        split_before_message_index=split_before_message_index,
    )


def _message_row(
    *,
    provider_id: str,
    conversation_id: str,
    message_id: str,
    role: str,
    ts: int,
    text: str,
) -> dict:
    return {
        "record_type": "message",
        "provider_id": provider_id,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "role": role,
        "ts": ts,
        "text": text,
        "content": {"content_type": "text", "parts": [text]},
    }


def _write_parsed_jsonl(
    path: Path,
    *,
    provider_id: str,
    conversation_id: str,
    messages: list[dict],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "record_type": "thread",
                    "provider_id": provider_id,
                    "conversation_id": conversation_id,
                    "message_count": len(messages),
                },
                ensure_ascii=True,
            )
            + "\n"
        )
        for row in messages:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def _load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_jsonl_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def _synthetic_reconstructed_messages(
    *,
    conversation_id: str = "conv-a",
    count: int = 90,
    split_before: int = 40,
    left_role: str = "assistant",
    right_role: str = "user",
    left_text: str = "left topic",
    right_text: str = "right topic",
) -> list[ReconstructedThreadMessage]:
    messages = [
        ReconstructedThreadMessage(
            provider_id="openai",
            conversation_id=conversation_id,
            message_id=f"m{index}",
            role="assistant",
            ts=100 + index,
            text=f"message {index}",
            ordinal=index,
        )
        for index in range(count)
    ]
    messages[split_before - 1] = ReconstructedThreadMessage(
        provider_id="openai",
        conversation_id=conversation_id,
        message_id=f"m{split_before - 1}",
        role=left_role,
        ts=100 + split_before - 1,
        text=left_text,
        ordinal=split_before - 1,
    )
    messages[split_before] = ReconstructedThreadMessage(
        provider_id="openai",
        conversation_id=conversation_id,
        message_id=f"m{split_before}",
        role=right_role,
        ts=100 + split_before,
        text=right_text,
        ordinal=split_before,
    )
    return messages


def test_reconstruct_thread_messages_preserves_order_and_ordinals(tmp_path):
    parsed_path = tmp_path / "openai" / "thread-conv-a" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed_path,
        provider_id="openai",
        conversation_id="conv-a",
        messages=[
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m1",
                role="user",
                ts=100,
                text="first",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m2",
                role="assistant",
                ts=110,
                text="second",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m3",
                role="user",
                ts=120,
                text="third",
            ),
        ],
    )

    messages = reconstruct_thread_messages(parsed_path)

    assert [message.message_id for message in messages] == ["m1", "m2", "m3"]
    assert [message.ordinal for message in messages] == [0, 1, 2]
    assert [message.role for message in messages] == ["user", "assistant", "user"]
    assert [message.ts for message in messages] == [100, 110, 120]


def test_build_sliding_windows_uses_overlapping_defaults(tmp_path):
    parsed_path = tmp_path / "openai" / "thread-conv-a" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed_path,
        provider_id="openai",
        conversation_id="conv-a",
        messages=[
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id=f"m{index}",
                role="user" if index % 2 else "assistant",
                ts=100 + index,
                text=f"text {index}",
            )
            for index in range(1, 6)
        ],
    )

    messages = reconstruct_thread_messages(parsed_path)
    windows = build_sliding_windows(messages)

    assert [window.window_index for window in windows] == [0, 1, 2]
    assert [window.message_ids for window in windows] == [
        ("m1", "m2", "m3"),
        ("m2", "m3", "m4"),
        ("m3", "m4", "m5"),
    ]
    assert [(window.start_index, window.end_index) for window in windows] == [
        (0, 2),
        (1, 3),
        (2, 4),
    ]
    assert [window.content_char_count for window in windows] == [15, 15, 15]


def test_lexical_token_set_prefers_word_tokens_and_falls_back_to_ngrams():
    assert lexical_token_set("Fix cache key mismatch") == frozenset(
        {"fix", "cache", "key", "mismatch"}
    )
    assert lexical_token_set("設定更新") == frozenset({"設定更", "定更新"})


def test_lexical_jaccard_similarity_is_deterministic():
    assert lexical_jaccard_similarity(
        frozenset({"cache", "key", "fix"}),
        frozenset({"cache", "fix", "plan"}),
    ) == 0.5
    assert lexical_jaccard_similarity(frozenset(), frozenset({"cache"})) == 0.0


def test_adjacent_boundary_detection_suppresses_empty_window_noise(tmp_path):
    parsed_path = tmp_path / "openai" / "thread-conv-a" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed_path,
        provider_id="openai",
        conversation_id="conv-a",
        messages=[
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m1",
                role="user",
                ts=101,
                text="This is a substantial opening message.",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m2",
                role="assistant",
                ts=102,
                text="",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m3",
                role="user",
                ts=103,
                text="",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m4",
                role="assistant",
                ts=104,
                text="",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m5",
                role="user",
                ts=105,
                text="This is another substantial message later on.",
            ),
        ],
    )

    messages = reconstruct_thread_messages(parsed_path)
    windows = build_sliding_windows(messages)
    boundaries = detect_adjacent_boundaries(
        messages,
        windows,
        StaticEmbeddingBackend(
            [
                [1.0, 0.0],
                [0.0, 1.0],
                [1.0, 0.0],
            ]
        ).embed([window.text for window in windows]),
        threshold=0.75,
    )
    segments = build_contiguous_segments(messages, boundaries)

    assert [window.content_char_count for window in windows] == [33, 0, 39]
    assert [boundary.boundary for boundary in boundaries] == [False, False]
    assert [segment.message_ids for segment in segments] == [
        ("m1", "m2", "m3", "m4", "m5"),
    ]


def test_boundary_lexical_similarity_uses_non_overlapping_boundary_text(tmp_path):
    parsed_path = tmp_path / "openai" / "thread-conv-a" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed_path,
        provider_id="openai",
        conversation_id="conv-a",
        messages=[
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m1",
                role="user",
                ts=101,
                text="draft api migration notes",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m2",
                role="assistant",
                ts=102,
                text="shared implementation details",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m3",
                role="user",
                ts=103,
                text="shared implementation details",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m4",
                role="assistant",
                ts=104,
                text="api migration next steps",
            ),
        ],
    )

    messages = reconstruct_thread_messages(parsed_path)
    windows = build_sliding_windows(messages)

    assert boundary_lexical_similarity(messages, windows[0], windows[1]) == 0.3333333333333333


def test_boundary_structural_continuity_scores_request_answer_and_handoff(tmp_path):
    parsed_path = tmp_path / "openai" / "thread-conv-a" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed_path,
        provider_id="openai",
        conversation_id="conv-a",
        messages=[
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m1",
                role="assistant",
                ts=101,
                text="prior substantial answer",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m2",
                role="user",
                ts=102,
                text="prior follow up",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m3",
                role="user",
                ts=103,
                text="please generate an image",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m4",
                role="tool",
                ts=104,
                text="",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m5",
                role="assistant",
                ts=105,
                text="generated image result",
            ),
        ],
    )

    messages = reconstruct_thread_messages(parsed_path)
    windows = build_sliding_windows(messages)

    assert boundary_structural_continuity(messages, windows[0], windows[1]) == 0.2
    assert boundary_structural_continuity(messages, windows[1], windows[2]) == 0.2


def test_adjacent_boundary_detection_and_contiguous_segments(tmp_path):
    parsed_path = tmp_path / "openai" / "thread-conv-a" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed_path,
        provider_id="openai",
        conversation_id="conv-a",
        messages=[
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id=f"m{index}",
                role="user",
                ts=100 + index,
                text=f"text {index}",
            )
            for index in range(1, 6)
        ],
    )

    messages = reconstruct_thread_messages(parsed_path)
    windows = build_sliding_windows(messages)
    boundaries = detect_adjacent_boundaries(
        messages,
        windows,
        StaticEmbeddingBackend(
            [
                [1.0, 0.0],
                [0.0, 1.0],
                [0.0, 1.0],
            ]
        ).embed([window.text for window in windows]),
        threshold=0.75,
    )
    segments = build_contiguous_segments(messages, boundaries)

    assert [boundary.boundary for boundary in boundaries] == [True, False]
    assert boundaries[0].split_before_message_index == 3
    assert [segment.message_ids for segment in segments] == [
        ("m1", "m2", "m3"),
        ("m4", "m5"),
    ]
    assert [(segment.start_index, segment.end_index) for segment in segments] == [
        (0, 2),
        (3, 4),
    ]


def test_guarded_drift_split_splits_long_segment_at_weak_candidate():
    messages = _synthetic_reconstructed_messages(count=90, split_before=40)
    segments = build_contiguous_segments(
        messages,
        [
            _boundary(
                conversation_id="conv-a",
                split_before_message_index=40,
                boundary=False,
                continuity_score=0.435,
            )
        ],
        boundary_threshold=0.43,
    )

    assert [(segment.start_index, segment.end_index) for segment in segments] == [
        (0, 39),
        (40, 89),
    ]


def test_guarded_drift_split_ignores_short_segments():
    messages = _synthetic_reconstructed_messages(count=79, split_before=40)
    segments = build_contiguous_segments(
        messages,
        [
            _boundary(
                conversation_id="conv-a",
                split_before_message_index=40,
                boundary=False,
                continuity_score=0.435,
            )
        ],
        boundary_threshold=0.43,
    )

    assert [(segment.start_index, segment.end_index) for segment in segments] == [
        (0, 78),
    ]


def test_guarded_drift_split_ignores_user_assistant_handoff():
    messages = _synthetic_reconstructed_messages(
        count=90,
        split_before=40,
        left_role="user",
        right_role="assistant",
    )
    segments = build_contiguous_segments(
        messages,
        [
            _boundary(
                conversation_id="conv-a",
                split_before_message_index=40,
                boundary=False,
                continuity_score=0.435,
            )
        ],
        boundary_threshold=0.43,
    )

    assert [(segment.start_index, segment.end_index) for segment in segments] == [
        (0, 89),
    ]


def test_guarded_drift_split_ignores_system_or_tool_adjacent_rows():
    messages = _synthetic_reconstructed_messages(
        count=90,
        split_before=40,
        left_role="assistant",
        right_role="tool",
        right_text="tool payload",
    )
    segments = build_contiguous_segments(
        messages,
        [
            _boundary(
                conversation_id="conv-a",
                split_before_message_index=40,
                boundary=False,
                continuity_score=0.435,
            )
        ],
        boundary_threshold=0.43,
    )

    assert [(segment.start_index, segment.end_index) for segment in segments] == [
        (0, 89),
    ]


def test_guarded_drift_split_ignores_structurally_protected_candidate():
    messages = _synthetic_reconstructed_messages(count=90, split_before=40)
    segments = build_contiguous_segments(
        messages,
        [
            _boundary(
                conversation_id="conv-a",
                split_before_message_index=40,
                boundary=False,
                structural_continuity=0.2,
                continuity_score=0.435,
            )
        ],
        boundary_threshold=0.43,
    )

    assert [(segment.start_index, segment.end_index) for segment in segments] == [
        (0, 89),
    ]


def test_guarded_drift_split_ignores_score_far_above_threshold():
    messages = _synthetic_reconstructed_messages(count=90, split_before=40)
    segments = build_contiguous_segments(
        messages,
        [
            _boundary(
                conversation_id="conv-a",
                split_before_message_index=40,
                boundary=False,
                continuity_score=0.48,
            )
        ],
        boundary_threshold=0.43,
    )

    assert [(segment.start_index, segment.end_index) for segment in segments] == [
        (0, 89),
    ]


def test_guarded_drift_split_chooses_lowest_score_then_earliest_split():
    messages = _synthetic_reconstructed_messages(count=90, split_before=40)
    segments = build_contiguous_segments(
        messages,
        [
            _boundary(
                conversation_id="conv-a",
                split_before_message_index=30,
                boundary=False,
                continuity_score=0.438,
            ),
            _boundary(
                conversation_id="conv-a",
                split_before_message_index=40,
                boundary=False,
                continuity_score=0.435,
            ),
            _boundary(
                conversation_id="conv-a",
                split_before_message_index=50,
                boundary=False,
                continuity_score=0.435,
            ),
        ],
        boundary_threshold=0.43,
    )

    assert [(segment.start_index, segment.end_index) for segment in segments] == [
        (0, 39),
        (40, 89),
    ]


def test_segment_merge_groups_non_contiguous_similar_segments():
    messages = _synthetic_reconstructed_messages(count=9, split_before=4)
    segments = build_contiguous_segments(
        messages,
        [
            _boundary(conversation_id="conv-a", split_before_message_index=3),
            _boundary(conversation_id="conv-a", split_before_message_index=6),
        ],
    )

    groups = build_segment_merge_groups(
        messages,
        segments,
        backend=StaticEmbeddingBackend(
            [
                [1.0, 0.0],
                [0.0, 1.0],
                [1.0, 0.0],
            ]
        ),
    )

    assert [group.segment_ids for group in groups] == [
        (segments[0].segment_id, segments[2].segment_id),
        (segments[1].segment_id,),
    ]


def test_segment_merge_groups_keeps_dissimilar_segments_separate():
    messages = _synthetic_reconstructed_messages(count=9, split_before=4)
    segments = build_contiguous_segments(
        messages,
        [
            _boundary(conversation_id="conv-a", split_before_message_index=3),
            _boundary(conversation_id="conv-a", split_before_message_index=6),
        ],
    )

    groups = build_segment_merge_groups(
        messages,
        segments,
        backend=StaticEmbeddingBackend(
            [
                [1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
                [0.0, 0.0, 1.0],
            ]
        ),
    )

    assert [group.segment_ids for group in groups] == [
        (segments[0].segment_id,),
        (segments[1].segment_id,),
        (segments[2].segment_id,),
    ]


def test_segment_merge_groups_are_deterministic():
    messages = _synthetic_reconstructed_messages(count=9, split_before=4)
    segments = build_contiguous_segments(
        messages,
        [
            _boundary(conversation_id="conv-a", split_before_message_index=3),
            _boundary(conversation_id="conv-a", split_before_message_index=6),
        ],
    )
    vectors = [[1.0, 0.0], [0.0, 1.0], [1.0, 0.0]]

    first = build_segment_merge_groups(
        messages,
        segments,
        backend=StaticEmbeddingBackend(vectors),
    )
    second = build_segment_merge_groups(
        messages,
        segments,
        backend=StaticEmbeddingBackend(vectors),
    )

    assert [group.group_id for group in first] == [
        group.group_id for group in second
    ]


def test_segment_merge_group_message_ids_are_deduplicated_and_sorted():
    messages = _synthetic_reconstructed_messages(count=9, split_before=4)
    segments = build_contiguous_segments(
        messages,
        [
            _boundary(conversation_id="conv-a", split_before_message_index=3),
            _boundary(conversation_id="conv-a", split_before_message_index=6),
        ],
    )

    groups = build_segment_merge_groups(
        messages,
        segments,
        backend=StaticEmbeddingBackend(
            [
                [1.0, 0.0],
                [0.0, 1.0],
                [1.0, 0.0],
            ]
        ),
    )

    merged_group = groups[0]
    assert merged_group.message_ids == tuple(sorted(set(merged_group.message_ids)))
    assert merged_group.message_ids == tuple(
        sorted({*segments[0].message_ids, *segments[2].message_ids})
    )


def test_segment_merge_groups_do_not_merge_adjacent_segments():
    messages = _synthetic_reconstructed_messages(count=6, split_before=3)
    segments = build_contiguous_segments(
        messages,
        [_boundary(conversation_id="conv-a", split_before_message_index=3)],
    )

    groups = build_segment_merge_groups(
        messages,
        segments,
        backend=StaticEmbeddingBackend(
            [
                [1.0, 0.0],
                [1.0, 0.0],
            ]
        ),
    )

    assert [group.segment_ids for group in groups] == [
        (segments[0].segment_id,),
        (segments[1].segment_id,),
    ]


def test_segment_merge_groups_do_not_create_transitive_adjacent_group():
    messages = _synthetic_reconstructed_messages(count=12, split_before=4)
    segments = build_contiguous_segments(
        messages,
        [
            _boundary(conversation_id="conv-a", split_before_message_index=3),
            _boundary(conversation_id="conv-a", split_before_message_index=6),
            _boundary(conversation_id="conv-a", split_before_message_index=9),
        ],
    )

    groups = build_segment_merge_groups(
        messages,
        segments,
        backend=StaticEmbeddingBackend(
            [
                [1.0, 0.0],
                [1.0, 0.0],
                [1.0, 0.0],
                [1.0, 0.0],
            ]
        ),
    )

    assert [group.segment_ids for group in groups] == [
        (segments[0].segment_id, segments[2].segment_id),
        (segments[1].segment_id, segments[3].segment_id),
    ]


def test_lexical_continuity_suppresses_overeager_embedding_boundary(tmp_path):
    parsed_path = tmp_path / "openai" / "thread-conv-a" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed_path,
        provider_id="openai",
        conversation_id="conv-a",
        messages=[
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m1",
                role="user",
                ts=101,
                text="draft api migration notes",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m2",
                role="assistant",
                ts=102,
                text="shared implementation details",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m3",
                role="user",
                ts=103,
                text="shared implementation details",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m4",
                role="assistant",
                ts=104,
                text="api migration next steps",
            ),
        ],
    )

    messages = reconstruct_thread_messages(parsed_path)
    windows = build_sliding_windows(messages)
    boundaries = detect_adjacent_boundaries(
        messages,
        windows,
        StaticEmbeddingBackend([[1.0, 0.0], [0.7, 0.714142842854285]]).embed(
            [window.text for window in windows]
        ),
        threshold=0.75,
        structural_continuity_weight=0.0,
    )

    assert boundaries[0].similarity == 0.7
    assert boundaries[0].lexical_similarity == 0.3333
    assert boundaries[0].continuity_score == 0.7667
    assert boundaries[0].boundary is False


def test_structural_continuity_suppresses_user_request_answer_boundary(tmp_path):
    parsed_path = tmp_path / "openai" / "thread-conv-a" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed_path,
        provider_id="openai",
        conversation_id="conv-a",
        messages=[
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m1",
                role="assistant",
                ts=101,
                text="prior substantial answer",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m2",
                role="assistant",
                ts=102,
                text="more prior context",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m3",
                role="user",
                ts=103,
                text="please explain cache invalidation",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m4",
                role="assistant",
                ts=104,
                text="cache invalidation needs clear ownership",
            ),
        ],
    )

    messages = reconstruct_thread_messages(parsed_path)
    windows = build_sliding_windows(messages)
    boundaries = detect_adjacent_boundaries(
        messages,
        windows,
        StaticEmbeddingBackend([[1.0, 0.0], [0.35, 0.9367496997597597]]).embed(
            [window.text for window in windows]
        ),
        threshold=0.43,
    )

    assert boundaries[0].similarity == 0.35
    assert boundaries[0].structural_continuity == 1.0
    assert boundaries[0].continuity_score == 0.5
    assert boundaries[0].boundary is False


def test_structural_continuity_bridges_empty_system_gap(tmp_path):
    parsed_path = tmp_path / "openai" / "thread-conv-a" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed_path,
        provider_id="openai",
        conversation_id="conv-a",
        messages=[
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m1",
                role="assistant",
                ts=101,
                text="prior substantial answer",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m2",
                role="user",
                ts=102,
                text="please create the header image",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m3",
                role="system",
                ts=103,
                text="",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m4",
                role="assistant",
                ts=104,
                text="here is the header image prompt",
            ),
        ],
    )

    messages = reconstruct_thread_messages(parsed_path)
    windows = build_sliding_windows(messages, window_size=2)
    boundaries = detect_adjacent_boundaries(
        messages,
        windows,
        StaticEmbeddingBackend(
            [
                [1.0, 0.0],
                [0.405, 0.9143144969794304],
                [0.405, 0.9143144969794304],
            ]
        ).embed([window.text for window in windows]),
        threshold=0.43,
    )

    assert [boundary.structural_continuity for boundary in boundaries] == [0.2, 0.2]
    assert boundaries[0].continuity_score == 0.435
    assert [boundary.boundary for boundary in boundaries] == [False, False]


def test_structural_continuity_bridges_tool_gap(tmp_path):
    parsed_path = tmp_path / "openai" / "thread-conv-a" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed_path,
        provider_id="openai",
        conversation_id="conv-a",
        messages=[
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m1",
                role="assistant",
                ts=101,
                text="prior substantial answer",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m2",
                role="user",
                ts=102,
                text="please generate the chart",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m3",
                role="tool",
                ts=103,
                text='{"status":"ok"}',
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m4",
                role="assistant",
                ts=104,
                text="generated chart is ready",
            ),
        ],
    )

    messages = reconstruct_thread_messages(parsed_path)
    windows = build_sliding_windows(messages, window_size=2)
    boundaries = detect_adjacent_boundaries(
        messages,
        windows,
        StaticEmbeddingBackend(
            [
                [1.0, 0.0],
                [0.405, 0.9143144969794304],
                [0.405, 0.9143144969794304],
            ]
        ).embed([window.text for window in windows]),
        threshold=0.43,
    )

    assert [boundary.structural_continuity for boundary in boundaries] == [0.2, 0.2]
    assert [boundary.boundary for boundary in boundaries] == [False, False]


def test_structural_continuity_does_not_bridge_long_non_substantive_gap(tmp_path):
    parsed_path = tmp_path / "openai" / "thread-conv-a" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed_path,
        provider_id="openai",
        conversation_id="conv-a",
        messages=[
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m1",
                role="user",
                ts=101,
                text="please generate the chart",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m2",
                role="system",
                ts=102,
                text="",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m3",
                role="tool",
                ts=103,
                text='{"status":"ok"}',
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m4",
                role="system",
                ts=104,
                text="",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m5",
                role="assistant",
                ts=105,
                text="generated chart is ready",
            ),
        ],
    )

    messages = reconstruct_thread_messages(parsed_path)
    windows = build_sliding_windows(messages, window_size=2)

    assert boundary_structural_continuity(messages, windows[1], windows[2]) == 0.0


def test_structural_continuity_does_not_hide_clear_topic_pivot(tmp_path):
    parsed_path = tmp_path / "openai" / "thread-conv-a" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed_path,
        provider_id="openai",
        conversation_id="conv-a",
        messages=[
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m1",
                role="assistant",
                ts=101,
                text="prior substantial answer",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m2",
                role="assistant",
                ts=102,
                text="more prior context",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m3",
                role="user",
                ts=103,
                text="let's switch topic to deployment planning",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m4",
                role="assistant",
                ts=104,
                text="sure, new topic: deployment planning",
            ),
        ],
    )

    messages = reconstruct_thread_messages(parsed_path)
    windows = build_sliding_windows(messages)
    boundaries = detect_adjacent_boundaries(
        messages,
        windows,
        StaticEmbeddingBackend([[1.0, 0.0], [0.2, 0.9797958971132712]]).embed(
            [window.text for window in windows]
        ),
        threshold=0.43,
    )

    assert boundaries[0].structural_continuity == 1.0
    assert boundaries[0].continuity_score == 0.35
    assert boundaries[0].boundary is True


def test_structural_continuity_prevents_singleton_user_request_segment(tmp_path):
    parsed_path = tmp_path / "openai" / "thread-conv-a" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed_path,
        provider_id="openai",
        conversation_id="conv-a",
        messages=[
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m1",
                role="user",
                ts=101,
                text="alpha topic opening",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m2",
                role="assistant",
                ts=102,
                text="alpha topic answer",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m3",
                role="user",
                ts=103,
                text="please explain cache invalidation",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m4",
                role="assistant",
                ts=104,
                text="cache invalidation needs clear ownership",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m5",
                role="user",
                ts=105,
                text="thanks, continue with examples",
            ),
        ],
    )

    messages = reconstruct_thread_messages(parsed_path)
    windows = build_sliding_windows(messages, window_size=2)
    boundaries = detect_adjacent_boundaries(
        messages,
        windows,
        StaticEmbeddingBackend(
            [
                [1.0, 0.0],
                [0.35, 0.9367496997597597],
                [-0.755, 0.6557247906021538],
                [-0.755, 0.6557247906021538],
            ]
        ).embed([window.text for window in windows]),
        threshold=0.43,
    )
    segments = build_contiguous_segments(messages, boundaries)

    assert [boundary.boundary for boundary in boundaries] == [True, False, False]
    assert boundaries[1].structural_continuity == 1.0
    assert [segment.message_ids for segment in segments] == [
        ("m1", "m2"),
        ("m3", "m4", "m5"),
    ]


def test_structural_continuity_suppresses_empty_tool_handoff_boundary(tmp_path):
    parsed_path = tmp_path / "openai" / "thread-conv-a" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed_path,
        provider_id="openai",
        conversation_id="conv-a",
        messages=[
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m1",
                role="assistant",
                ts=101,
                text="prior substantial answer",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m2",
                role="assistant",
                ts=102,
                text="more prior context",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m3",
                role="user",
                ts=103,
                text="please generate an image",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m4",
                role="tool",
                ts=104,
                text="",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m5",
                role="assistant",
                ts=105,
                text="generated image result",
            ),
        ],
    )

    messages = reconstruct_thread_messages(parsed_path)
    windows = build_sliding_windows(messages)
    boundaries = detect_adjacent_boundaries(
        messages,
        windows,
        StaticEmbeddingBackend(
            [
                [1.0, 0.0],
                [0.405, 0.9143144969794304],
                [0.1225, 0.9924666241912424],
            ]
        ).embed([window.text for window in windows]),
        threshold=0.43,
    )

    assert [boundary.structural_continuity for boundary in boundaries] == [0.2, 0.2]
    assert [boundary.boundary for boundary in boundaries] == [False, False]


def test_adjacent_boundary_detection_blocks_low_content_pairs_deterministically(tmp_path):
    parsed_path = tmp_path / "openai" / "thread-conv-a" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed_path,
        provider_id="openai",
        conversation_id="conv-a",
        messages=[
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m1",
                role="user",
                ts=100,
                text="ok",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m2",
                role="assistant",
                ts=101,
                text="go",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m3",
                role="user",
                ts=102,
                text="hi",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m4",
                role="assistant",
                ts=103,
                text="yo",
            ),
        ],
    )

    messages = reconstruct_thread_messages(parsed_path)
    windows = build_sliding_windows(messages)
    boundaries = detect_adjacent_boundaries(
        messages,
        windows,
        StaticEmbeddingBackend(
            [
                [1.0, 0.0],
                [0.0, 1.0],
            ]
        ).embed([window.text for window in windows]),
        threshold=0.75,
    )

    assert [window.content_char_count for window in windows] == [6, 6]
    assert [boundary.boundary for boundary in boundaries] == [False]


def test_empty_standalone_segment_is_absorbed_into_previous_neighbor(tmp_path):
    parsed_path = tmp_path / "openai" / "thread-conv-a" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed_path,
        provider_id="openai",
        conversation_id="conv-a",
        messages=[
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m1",
                role="user",
                ts=101,
                text="alpha topic",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m2",
                role="assistant",
                ts=102,
                text="beta topic",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m3",
                role="user",
                ts=103,
                text="gamma topic",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m4",
                role="assistant",
                ts=104,
                text="",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m5",
                role="user",
                ts=105,
                text="delta topic",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m6",
                role="assistant",
                ts=106,
                text="epsilon topic",
            ),
        ],
    )

    messages = reconstruct_thread_messages(parsed_path)
    segments = build_contiguous_segments(
        messages,
        [
            _boundary(conversation_id="conv-a", split_before_message_index=3),
            _boundary(conversation_id="conv-a", split_before_message_index=4),
        ],
    )

    assert [segment.message_ids for segment in segments] == [
        ("m1", "m2", "m3", "m4"),
        ("m5", "m6"),
    ]
    assert all(
        segment.text_sha1 != "da39a3ee5e6b4b0d3255bfef95601890afd80709"
        for segment in segments
    )


def test_leading_empty_segment_is_absorbed_into_next_neighbor(tmp_path):
    parsed_path = tmp_path / "openai" / "thread-conv-a" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed_path,
        provider_id="openai",
        conversation_id="conv-a",
        messages=[
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m1",
                role="user",
                ts=101,
                text="",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m2",
                role="assistant",
                ts=102,
                text="",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m3",
                role="user",
                ts=103,
                text="alpha topic",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m4",
                role="assistant",
                ts=104,
                text="beta topic",
            ),
        ],
    )

    messages = reconstruct_thread_messages(parsed_path)
    segments = build_contiguous_segments(
        messages,
        [_boundary(conversation_id="conv-a", split_before_message_index=2)],
    )

    assert [segment.message_ids for segment in segments] == [
        ("m1", "m2", "m3", "m4"),
    ]


def test_analyze_intra_thread_topics_writes_inspectable_artifacts(tmp_path):
    parsed_path = tmp_path / "openai" / "thread-conv-a" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed_path,
        provider_id="openai",
        conversation_id="conv-a",
        messages=[
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id=f"m{index}",
                role="user",
                ts=100 + index,
                text=f"text {index}",
            )
            for index in range(1, 6)
        ],
    )

    result = analyze_intra_thread_topics(
        parsed_path,
        boundary_threshold=0.75,
        backend=StaticEmbeddingBackend(
            [
                [1.0, 0.0],
                [0.0, 1.0],
                [0.0, 1.0],
            ]
        ),
    )

    boundaries_path = intra_thread_boundaries_artifact_path(parsed_path)
    segments_path = intra_thread_segments_artifact_path(parsed_path)
    boundary_rows = _load_jsonl(boundaries_path)
    segment_rows = _load_jsonl(segments_path)

    assert result["threads"] == 1
    assert result["windows"] == 3
    assert result["boundaries"] == 1
    assert result["segments"] == 2
    assert result["embedding_model"] == "local/test-static"
    assert boundary_rows[0]["record_type"] == "intra_thread_boundary"
    assert boundary_rows[0]["schema_version"] == "0.3"
    assert boundary_rows[0]["boundary"] is True
    assert "lexical_similarity" in boundary_rows[0]
    assert "structural_continuity" in boundary_rows[0]
    assert "continuity_score" in boundary_rows[0]
    assert boundary_rows[0]["split_before_message_index"] == 3
    assert [row["message_ids"] for row in segment_rows] == [
        ["m1", "m2", "m3"],
        ["m4", "m5"],
    ]
    assert all(row["segment_id"].startswith("segment_") for row in segment_rows)


def test_analyze_intra_thread_topics_writes_segment_merge_artifact(tmp_path):
    parsed_path = tmp_path / "openai" / "thread-conv-a" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed_path,
        provider_id="openai",
        conversation_id="conv-a",
        messages=[
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id=f"m{index}",
                role="user",
                ts=100 + index,
                text=f"text {index}",
            )
            for index in range(1, 6)
        ],
    )

    result = analyze_intra_thread_topics(
        parsed_path,
        boundary_threshold=0.75,
        backend=StaticEmbeddingBackend(
            [
                [1.0, 0.0],
                [0.0, 1.0],
                [0.0, 1.0],
                [1.0, 0.0],
                [0.0, 1.0],
            ]
        ),
        merge_segments=True,
    )

    merge_path = intra_thread_segment_merge_artifact_path(parsed_path)
    diagnostics_path = intra_thread_segment_merge_diagnostics_artifact_path(parsed_path)
    merge_rows = _load_jsonl(merge_path)
    diagnostic_rows = _load_jsonl(diagnostics_path)

    assert result["segment_merge_groups"] == 2
    assert result["segment_merge_artifacts"] == [merge_path, diagnostics_path]
    assert merge_path.exists()
    assert diagnostics_path.exists()
    assert merge_rows[0]["record_type"] == "intra_thread_segment_merge_group"
    assert merge_rows[0]["schema_version"] == "0.1"
    assert "group_id" in merge_rows[0]
    assert "centroid_embedding" in merge_rows[0]
    assert diagnostic_rows[0]["record_type"] == (
        "intra_thread_segment_merge_pair_diagnostic"
    )
    assert diagnostic_rows[0]["schema_version"] == "0.1"
    assert {row["decision"] for row in diagnostic_rows} >= {"short_segment"}
    assert {"left_segment_id", "right_segment_id", "similarity", "reason"} <= set(
        diagnostic_rows[0]
    )


def test_intra_thread_report_reconstructs_segments_and_boundary_diagnostics(tmp_path):
    parsed_path = tmp_path / "openai" / "thread-conv-a" / "parsed.jsonl"
    messages = [
        _message_row(
            provider_id="openai",
            conversation_id="conv-a",
            message_id="m1",
            role="user",
            ts=101,
            text="alpha opening request",
        ),
        _message_row(
            provider_id="openai",
            conversation_id="conv-a",
            message_id="m2",
            role="assistant",
            ts=102,
            text="alpha opening answer",
        ),
        _message_row(
            provider_id="openai",
            conversation_id="conv-a",
            message_id="m3",
            role="user",
            ts=103,
            text="beta topic request",
        ),
        _message_row(
            provider_id="openai",
            conversation_id="conv-a",
            message_id="m4",
            role="assistant",
            ts=104,
            text="beta topic answer",
        ),
        _message_row(
            provider_id="openai",
            conversation_id="conv-a",
            message_id="m5",
            role="user",
            ts=105,
            text="shared cache migration terms",
        ),
        _message_row(
            provider_id="openai",
            conversation_id="conv-a",
            message_id="m6",
            role="assistant",
            ts=106,
            text="shared cache migration answer",
        ),
    ]
    for index in range(7, 87):
        messages.append(
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id=f"m{index}",
                role="user" if index % 2 else "assistant",
                ts=100 + index,
                text=f"long segment drift message {index}",
            )
        )
    _write_parsed_jsonl(
        parsed_path,
        provider_id="openai",
        conversation_id="conv-a",
        messages=messages,
    )
    boundary_rows = []
    for split_before in range(2, 86):
        boundary_rows.append(
            {
                "record_type": "intra_thread_boundary",
                "schema_version": "0.3",
                "provider_id": "openai",
                "conversation_id": "conv-a",
                "previous_window_index": split_before - 2,
                "next_window_index": split_before - 1,
                "previous_window_message_ids": [],
                "next_window_message_ids": [],
                "similarity": 0.9,
                "lexical_similarity": 0.0,
                "structural_continuity": 0.0,
                "continuity_score": 0.9,
                "boundary": False,
                "split_after_message_index": split_before - 1,
                "split_before_message_index": split_before,
            }
        )
    boundary_rows[0]["similarity"] = 0.39
    boundary_rows[0]["continuity_score"] = 0.39
    boundary_rows[0]["boundary"] = True
    boundary_rows[2]["similarity"] = 0.3
    boundary_rows[2]["structural_continuity"] = 1.0
    boundary_rows[2]["continuity_score"] = 0.45
    boundary_rows[3]["similarity"] = 0.36
    boundary_rows[3]["lexical_similarity"] = 0.4
    boundary_rows[3]["continuity_score"] = 0.44
    boundary_rows[9]["similarity"] = 0.47
    boundary_rows[9]["continuity_score"] = 0.47
    boundary_rows[20]["similarity"] = 0.37
    boundary_rows[20]["continuity_score"] = 0.37
    boundary_rows[21]["similarity"] = 0.42
    boundary_rows[21]["continuity_score"] = 0.42
    boundary_rows[22]["similarity"] = 0.5
    boundary_rows[22]["continuity_score"] = 0.5
    _write_jsonl_rows(
        intra_thread_boundaries_artifact_path(parsed_path),
        boundary_rows,
    )
    _write_jsonl_rows(
        intra_thread_segments_artifact_path(parsed_path),
        [
            {
                "record_type": "intra_thread_segment",
                "schema_version": "0.1",
                "provider_id": "openai",
                "conversation_id": "conv-a",
                "segment_id": "segment_alpha",
                "start_index": 0,
                "end_index": 1,
                "message_ids": ["m1", "m2"],
                "message_count": 2,
                "text_sha1": "unused",
            },
            {
                "record_type": "intra_thread_segment",
                "schema_version": "0.1",
                "provider_id": "openai",
                "conversation_id": "conv-a",
                "segment_id": "segment_beta",
                "start_index": 2,
                "end_index": 85,
                "message_ids": [f"m{index}" for index in range(3, 87)],
                "message_count": 84,
                "text_sha1": "unused",
            },
        ],
    )

    result = write_intra_thread_topic_reports(parsed_path, boundary_threshold=0.43)

    report_path = intra_thread_report_artifact_path(parsed_path)
    report = report_path.read_text(encoding="utf-8")
    assert result == {"threads": 1, "reports": [report_path]}
    assert report_path.exists()
    assert "# Intra-thread Topics Report: conv-a" in report
    assert "- Boundary rows: 84" in report
    assert "- Fired boundaries: 1" in report
    assert "- Segments: 2" in report
    assert "### Segment 0" in report
    assert "- Range: `0-1`" in report
    assert "`0` `user` `m1`: alpha opening request" in report
    assert "## Fired Boundaries" in report
    assert "split_before_message_index=2" in report
    assert "- boundary: `True`" in report
    assert "## Suppressed High-Signal Candidates" in report
    assert "split_before_message_index=4" in report
    assert "structural_continuity: 1.0000" in report
    assert "split_before_message_index=5" in report
    assert "lexical_similarity: 0.4000" in report
    assert "## Near-Threshold Candidates" in report
    assert "- Boundary threshold used for diagnostics: 0.4300" in report
    assert "## Drift Diagnostics" in report
    assert "### Segment 1 Drift" in report
    assert "- Range: `2-85`" in report
    assert "- Message count: 84" in report
    assert "- Min continuity_score: 0.3700" in report
    assert "- Median continuity_score:" in report
    assert "- P10 continuity_score:" in report
    assert "#### Weakest Internal Candidates" in report
    assert "split_before_message_index=22" in report
    assert "long segment drift message 22" in report
    assert "#### Near-Threshold Internal Candidates" in report
    assert "split_before_message_index=23" in report
    assert "#### Low-Score Runs" in report
    assert "##### Low-score run 22-24" in report
    assert "- Run length: 3" in report


def test_intra_thread_report_marks_guarded_drift_segment_start(tmp_path):
    parsed_path = tmp_path / "openai" / "thread-conv-a" / "parsed.jsonl"
    messages = [
        _message_row(
            provider_id="openai",
            conversation_id="conv-a",
            message_id=f"m{index}",
            role="assistant",
            ts=100 + index,
            text=f"message {index}",
        )
        for index in range(90)
    ]
    messages[39]["text"] = "left topic"
    messages[39]["content"]["parts"] = ["left topic"]
    messages[40]["role"] = "user"
    messages[40]["text"] = "right topic"
    messages[40]["content"]["parts"] = ["right topic"]
    _write_parsed_jsonl(
        parsed_path,
        provider_id="openai",
        conversation_id="conv-a",
        messages=messages,
    )
    _write_jsonl_rows(
        intra_thread_boundaries_artifact_path(parsed_path),
        [
            {
                "record_type": "intra_thread_boundary",
                "schema_version": "0.3",
                "provider_id": "openai",
                "conversation_id": "conv-a",
                "previous_window_index": 0,
                "next_window_index": 1,
                "previous_window_message_ids": [],
                "next_window_message_ids": [],
                "similarity": 0.435,
                "lexical_similarity": 0.0,
                "structural_continuity": 0.0,
                "continuity_score": 0.435,
                "boundary": False,
                "split_after_message_index": 39,
                "split_before_message_index": 40,
            }
        ],
    )
    _write_jsonl_rows(
        intra_thread_segments_artifact_path(parsed_path),
        [
            {
                "record_type": "intra_thread_segment",
                "schema_version": "0.1",
                "provider_id": "openai",
                "conversation_id": "conv-a",
                "segment_id": "segment-a",
                "start_index": 0,
                "end_index": 39,
                "message_ids": [f"m{index}" for index in range(40)],
                "message_count": 40,
                "text_sha1": "a",
            },
            {
                "record_type": "intra_thread_segment",
                "schema_version": "0.1",
                "provider_id": "openai",
                "conversation_id": "conv-a",
                "segment_id": "segment-b",
                "start_index": 40,
                "end_index": 89,
                "message_ids": [f"m{index}" for index in range(40, 90)],
                "message_count": 50,
                "text_sha1": "b",
            },
        ],
    )

    write_intra_thread_topic_reports(parsed_path, boundary_threshold=0.43)
    report = intra_thread_report_artifact_path(parsed_path).read_text(
        encoding="utf-8"
    )

    assert "### Segment 1" in report
    assert "- Range: `40-89`" in report
    assert "- Start source: `drift_guardrail`" in report


def test_intra_thread_report_includes_segment_merge_diagnostics(tmp_path):
    parsed_path = tmp_path / "openai" / "thread-conv-a" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed_path,
        provider_id="openai",
        conversation_id="conv-a",
        messages=[
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id=f"m{index}",
                role="user" if index % 2 else "assistant",
                ts=100 + index,
                text=f"segment diagnostic message {index}",
            )
            for index in range(15)
        ],
    )
    _write_jsonl_rows(intra_thread_boundaries_artifact_path(parsed_path), [])
    segment_rows = [
        {
            "record_type": "intra_thread_segment",
            "schema_version": "0.1",
            "provider_id": "openai",
            "conversation_id": "conv-a",
            "segment_id": f"segment-{index}",
            "start_index": index * 3,
            "end_index": index * 3 + 2,
            "message_ids": [
                f"m{message_index}"
                for message_index in range(index * 3, index * 3 + 3)
            ],
            "message_count": 3,
            "text_sha1": f"sha-{index}",
        }
        for index in range(5)
    ]
    _write_jsonl_rows(intra_thread_segments_artifact_path(parsed_path), segment_rows)
    _write_jsonl_rows(
        intra_thread_segment_merge_artifact_path(parsed_path),
        [
            {
                "record_type": "intra_thread_segment_merge_group",
                "schema_version": "0.1",
                "provider_id": "openai",
                "conversation_id": "conv-a",
                "group_id": "segment_merge_alpha",
                "segment_ids": ["segment-0", "segment-4"],
                "message_ids": ["m0", "m1", "m2", "m12", "m13", "m14"],
                "centroid_embedding": [0.1, 0.2],
            },
            {
                "record_type": "intra_thread_segment_merge_group",
                "schema_version": "0.1",
                "provider_id": "openai",
                "conversation_id": "conv-a",
                "group_id": "segment_merge_beta",
                "segment_ids": ["segment-1"],
                "message_ids": ["m3", "m4", "m5"],
                "centroid_embedding": [0.3, 0.4],
            },
        ],
    )
    _write_jsonl_rows(
        intra_thread_segment_merge_diagnostics_artifact_path(parsed_path),
        [
            {
                "record_type": "intra_thread_segment_merge_pair_diagnostic",
                "schema_version": "0.1",
                "provider_id": "openai",
                "conversation_id": "conv-a",
                "left_segment_id": "segment-0",
                "right_segment_id": "segment-4",
                "left_segment_index": 0,
                "right_segment_index": 4,
                "segment_index_gap": 3,
                "similarity": 0.8123,
                "decision": "merged",
                "reason": "similarity >= 0.75",
            },
            {
                "record_type": "intra_thread_segment_merge_pair_diagnostic",
                "schema_version": "0.1",
                "provider_id": "openai",
                "conversation_id": "conv-a",
                "left_segment_id": "segment-1",
                "right_segment_id": "segment-2",
                "left_segment_index": 1,
                "right_segment_index": 2,
                "segment_index_gap": 0,
                "similarity": 0.9000,
                "decision": "blocked_adjacent_group",
                "reason": "directly adjacent segments are not merged",
            },
        ],
    )

    write_intra_thread_topic_reports(parsed_path, boundary_threshold=0.43)
    report = intra_thread_report_artifact_path(parsed_path).read_text(
        encoding="utf-8"
    )

    assert "## Segment Merge Diagnostics" in report
    assert "### Merge Summary" in report
    assert "- Merge groups: 2" in report
    assert "- Merged groups: 1" in report
    assert "#### Group `segment_merge_alpha`" in report
    assert "- Segment index gaps: `3`" in report
    assert "### Pairwise Segment Similarities" in report
    assert "- decision: `merged`" in report
    assert "- decision: `blocked_adjacent_group`" in report
    assert "### Suspicious Merge Candidates" in report
    assert "segment_index_gap: 3" in report


def test_analyze_intra_thread_topics_cli_wires_new_command(tmp_path, capsys):
    parsed_path = tmp_path / "openai" / "thread-conv-a" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed_path,
        provider_id="openai",
        conversation_id="conv-a",
        messages=[
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id=f"m{index}",
                role="user",
                ts=100 + index,
                text=f"text {index}",
            )
            for index in range(1, 6)
        ],
    )

    with patch(
        "llm_logparser.core.analyzer_intra_thread_topics.resolve_embedding_backend",
        return_value=StaticEmbeddingBackend(
            [
                [1.0, 0.0],
                [0.0, 1.0],
                [0.0, 1.0],
            ]
        ),
    ):
        main(
            [
                "--locale",
                "en-US",
                "analyze",
                "intra-thread-topics",
                "--input",
                str(parsed_path),
                "--boundary-threshold",
                "0.75",
                "--overwrite",
            ]
        )

    boundaries_path = intra_thread_boundaries_artifact_path(parsed_path)
    segments_path = intra_thread_segments_artifact_path(parsed_path)
    assert boundaries_path.exists()
    assert segments_path.exists()
    captured = capsys.readouterr()
    assert "intra-thread topics artifacts written" in captured.out


def test_analyze_intra_thread_topics_cli_report_uses_existing_artifacts(tmp_path):
    parsed_path = tmp_path / "openai" / "thread-conv-a" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed_path,
        provider_id="openai",
        conversation_id="conv-a",
        messages=[
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m1",
                role="user",
                ts=101,
                text="alpha opening request",
            ),
            _message_row(
                provider_id="openai",
                conversation_id="conv-a",
                message_id="m2",
                role="assistant",
                ts=102,
                text="alpha opening answer",
            ),
        ],
    )
    _write_jsonl_rows(
        intra_thread_boundaries_artifact_path(parsed_path),
        [],
    )
    _write_jsonl_rows(
        intra_thread_segments_artifact_path(parsed_path),
        [
            {
                "record_type": "intra_thread_segment",
                "schema_version": "0.1",
                "provider_id": "openai",
                "conversation_id": "conv-a",
                "segment_id": "segment_alpha",
                "start_index": 0,
                "end_index": 1,
                "message_ids": ["m1", "m2"],
                "message_count": 2,
                "text_sha1": "unused",
            }
        ],
    )

    with patch(
        "llm_logparser.core.analyzer_intra_thread_topics.resolve_embedding_backend",
        side_effect=AssertionError("report mode must not resolve embeddings"),
    ):
        main(
            [
                "--locale",
                "en-US",
                "analyze",
                "intra-thread-topics",
                "--input",
                str(parsed_path),
                "--boundary-threshold",
                "0.43",
                "--report",
            ]
        )

    report_path = intra_thread_report_artifact_path(parsed_path)
    assert report_path.exists()
    report = report_path.read_text(encoding="utf-8")
    assert "alpha opening request" in report
    assert "## Drift Diagnostics" in report
    assert "_No long segments above drift diagnostic threshold._" in report
    assert "## Segment Merge Diagnostics" in report
    assert (
        "_Segment merge artifact not found. Run with --merge-segments to generate diagnostics._"
        in report
    )
