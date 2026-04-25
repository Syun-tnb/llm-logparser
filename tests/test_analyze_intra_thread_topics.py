from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from llm_logparser.cli.cli import main
from llm_logparser.core.analyzer_intra_thread_topics import (
    AdjacentWindowBoundary,
    analyze_intra_thread_topics,
    boundary_lexical_similarity,
    boundary_structural_continuity,
    build_contiguous_segments,
    build_sliding_windows,
    detect_adjacent_boundaries,
    intra_thread_boundaries_artifact_path,
    intra_thread_segments_artifact_path,
    lexical_jaccard_similarity,
    lexical_token_set,
    reconstruct_thread_messages,
)


class StaticEmbeddingBackend:
    def __init__(self, vectors: list[list[float]], *, model_id: str = "local/test-static"):
        self.model_id = model_id
        self._vectors = vectors

    def embed(self, texts: list[str]) -> list[list[float]]:
        assert len(texts) == len(self._vectors)
        return self._vectors


def _boundary(
    *,
    conversation_id: str,
    split_before_message_index: int,
    boundary: bool = True,
) -> AdjacentWindowBoundary:
    return AdjacentWindowBoundary(
        provider_id="openai",
        conversation_id=conversation_id,
        previous_window_index=0,
        next_window_index=1,
        previous_window_message_ids=(),
        next_window_message_ids=(),
        similarity=0.0,
        lexical_similarity=0.0,
        structural_continuity=0.0,
        continuity_score=0.0,
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

    assert boundary_structural_continuity(messages, windows[0], windows[1]) == 0.85
    assert boundary_structural_continuity(messages, windows[1], windows[2]) == 0.7


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
                [0.35, 0.9367496997597597],
                [0.1225, 0.9924666241912424],
            ]
        ).embed([window.text for window in windows]),
        threshold=0.43,
    )

    assert [boundary.structural_continuity for boundary in boundaries] == [0.85, 0.7]
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
