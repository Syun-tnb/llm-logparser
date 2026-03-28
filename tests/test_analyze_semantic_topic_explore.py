from __future__ import annotations

import json
from pathlib import Path

from llm_logparser.cli.cli import main
from llm_logparser.core.analyzer_semantic_topic_explore import (
    build_semantic_topic_explore_payload,
    render_semantic_topic_explore,
)
from llm_logparser.core.analyzer_semantic_topics import write_semantic_topics_artifacts


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def _message_window_row(
    conversation_id: str,
    window_id: str,
    *,
    message_ids: list[str],
    roles: list[str],
    text: str,
    ts_start: int,
    ts_end: int,
) -> dict:
    return {
        "record_type": "message_window",
        "schema_version": "1.0",
        "provider_id": "openai",
        "conversation_id": conversation_id,
        "window_id": window_id,
        "message_ids": message_ids,
        "roles": roles,
        "message_count": len(roles),
        "char_count": len(text),
        "ts_start": ts_start,
        "ts_end": ts_end,
        "text": text,
    }


def _window_cluster_row(
    conversation_id: str,
    window_id: str,
    *,
    cluster_id: str,
    cluster_size: int,
) -> dict:
    return {
        "record_type": "window_cluster_member",
        "schema_version": "0.1",
        "provider_id": "openai",
        "conversation_id": conversation_id,
        "window_id": window_id,
        "cluster_id": cluster_id,
        "cluster_size": cluster_size,
        "edge_policy": "mutual-only",
    }


def _write_explore_fixture(root: Path) -> None:
    thread_a = root / "thread-conv-a"
    thread_b = root / "thread-conv-b"
    thread_c = root / "thread-conv-c"

    _write_jsonl(
        thread_a / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-a",
                "window-0001",
                message_ids=["a-1", "a-2"],
                roles=["user", "assistant"],
                text=(
                    "user: Draft the production migration checklist\n\n"
                    "assistant: Include schema audit and rollback steps"
                ),
                ts_start=100,
                ts_end=120,
            ),
            _message_window_row(
                "conv-a",
                "window-0002",
                message_ids=["a-3"],
                roles=["user"],
                text="user: Capture monitoring gates for rollout readiness",
                ts_start=130,
                ts_end=140,
            ),
        ],
    )
    _write_jsonl(
        thread_b / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-b",
                "window-0001",
                message_ids=["b-1", "b-2"],
                roles=["assistant", "user"],
                text=(
                    "assistant: Review launch risk controls\n\n"
                    "user: Add deployment rollback checks"
                ),
                ts_start=150,
                ts_end=170,
            )
        ],
    )
    _write_jsonl(
        thread_c / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-c",
                "window-0001",
                message_ids=["c-1"],
                roles=["user"],
                text="user: Plan lunch options for next week",
                ts_start=200,
                ts_end=205,
            ),
            _message_window_row(
                "conv-c",
                "window-0002",
                message_ids=["c-2"],
                roles=["assistant"],
                text="assistant: Compare ramen shops and cafe seating",
                ts_start=206,
                ts_end=210,
            ),
        ],
    )

    _write_jsonl(
        thread_a / "window_clusters.jsonl",
        [
            _window_cluster_row(
                "conv-a",
                "window-0001",
                cluster_id="cluster_000001",
                cluster_size=3,
            ),
            _window_cluster_row(
                "conv-a",
                "window-0002",
                cluster_id="cluster_000001",
                cluster_size=3,
            ),
        ],
    )
    _write_jsonl(
        thread_b / "window_clusters.jsonl",
        [
            _window_cluster_row(
                "conv-b",
                "window-0001",
                cluster_id="cluster_000001",
                cluster_size=3,
            )
        ],
    )
    _write_jsonl(
        thread_c / "window_clusters.jsonl",
        [
            _window_cluster_row(
                "conv-c",
                "window-0001",
                cluster_id="cluster_000002",
                cluster_size=2,
            ),
            _window_cluster_row(
                "conv-c",
                "window-0002",
                cluster_id="cluster_000002",
                cluster_size=2,
            ),
        ],
    )


def _build_topic_artifacts(root: Path) -> dict:
    result = write_semantic_topics_artifacts(root)
    return json.loads(Path(result["topics_path"]).read_text(encoding="utf-8"))


def test_semantic_topic_explore_topic_list_output(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_explore_fixture(root)
    topics_payload = _build_topic_artifacts(root)

    rendered = render_semantic_topic_explore(input_root=root)

    first_topic_id = topics_payload["topics"][0]["topic_id"]
    assert first_topic_id in rendered
    assert "messages=5" in rendered
    assert "conversations=2" in rendered
    assert "100 -> 170" in rendered
    assert "(unlabeled)" in rendered


def test_semantic_topic_explore_topic_detail_view(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_explore_fixture(root)
    topics_payload = _build_topic_artifacts(root)
    topic_id = next(
        topic["topic_id"]
        for topic in topics_payload["topics"]
        if topic["cluster_ids"] == ["cluster_000001"]
    )

    rendered = render_semantic_topic_explore(
        input_root=root,
        topic_id=topic_id,
    )

    assert f"Topic {topic_id}" in rendered
    assert "Summary: (none)" in rendered
    assert "Stats: clusters=1 windows=3 messages=5" in rendered
    assert "Conversations: conv-a, conv-b" in rendered
    assert "Timeline:" in rendered
    assert "- 100 | conv-a / window-0001" in rendered
    assert '- 150 | conv-b / window-0001 | "assistant: Review launch risk controls user: Add deployment rollback checks"' in rendered


def test_semantic_topic_explore_message_reverse_lookup(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_explore_fixture(root)
    topics_payload = _build_topic_artifacts(root)
    topic_id = next(
        topic["topic_id"]
        for topic in topics_payload["topics"]
        if topic["cluster_ids"] == ["cluster_000001"]
    )

    payload = build_semantic_topic_explore_payload(
        input_root=root,
        message_id="a-1",
    )

    assert payload["view"] == "message-lookup"
    assert payload["message_id"] == "a-1"
    assert payload["topics"] == [
        {
            "topic_id": topic_id,
            "label": None,
            "summary": None,
        }
    ]


def test_semantic_topic_explore_conversation_grouping(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_explore_fixture(root)
    _build_topic_artifacts(root)

    payload = build_semantic_topic_explore_payload(
        input_root=root,
        conversation_id="conv-a",
    )

    assert payload["view"] == "conversation"
    assert payload["conversation_id"] == "conv-a"
    assert len(payload["topics"]) == 1
    assert payload["topics"][0]["message_count"] == 3
    assert payload["topics"][0]["first_seen"] == 100
    assert payload["topics"][0]["last_seen"] == 140


def test_semantic_topic_explore_json_output_correctness(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_explore_fixture(root)
    topics_payload = _build_topic_artifacts(root)
    topic_id = next(
        topic["topic_id"]
        for topic in topics_payload["topics"]
        if topic["cluster_ids"] == ["cluster_000002"]
    )

    rendered = render_semantic_topic_explore(
        input_root=root,
        topic_id=topic_id,
        json_output=True,
    )
    payload = json.loads(rendered)

    assert payload["view"] == "topic-detail"
    assert payload["topic"]["topic_id"] == topic_id
    assert payload["topic"]["message_count"] == 2
    assert len(payload["topic"]["timeline"]) == 2


def test_analyze_semantic_topic_explore_cli_happy_path(tmp_path, capsys):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_explore_fixture(root)
    _build_topic_artifacts(root)

    main(
        [
            "--locale",
            "en-US",
            "analyze",
            "semantic-topic-explore",
            "--input",
            str(root),
            "--conversation-id",
            "conv-a",
        ]
    )

    output = capsys.readouterr().out
    assert "Conversation conv-a" in output
    assert "messages=3" in output
