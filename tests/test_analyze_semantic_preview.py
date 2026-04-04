from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from llm_logparser.cli.cli import main
from llm_logparser.core.analyzer_semantic_preview import (
    WindowClusterMember,
    WindowPreviewRecord,
    render_semantic_preview,
    select_representative_cluster_windows,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def _message_window_row(
    conversation_id: str,
    window_id: str,
    *,
    roles: list[str],
    text: str,
    message_count: int | None = None,
    char_count: int | None = None,
) -> dict:
    return {
        "record_type": "message_window",
        "schema_version": "2.0",
        "provider_id": "openai",
        "conversation_id": conversation_id,
        "window_id": window_id,
        "message_ids": [f"{window_id}-m{index}" for index, _ in enumerate(roles, start=1)],
        "roles": roles,
        "message_count": message_count if message_count is not None else len(roles),
        "char_count": char_count if char_count is not None else len(text),
        "ts_start": 1,
        "ts_end": 2,
        "text": text,
    }


def _window_neighbors_row(
    conversation_id: str,
    window_id: str,
    *,
    neighbors: list[dict],
) -> dict:
    return {
        "record_type": "window_neighbors",
        "schema_version": "0.1",
        "provider_id": "openai",
        "conversation_id": conversation_id,
        "window_id": window_id,
        "embedding_model": "local/test-backend",
        "neighbor_count": len(neighbors),
        "neighbors": neighbors,
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


def _write_cluster_fixture(root: Path) -> None:
    thread_a = root / "thread-conv-a"
    thread_b = root / "thread-conv-b"
    thread_c = root / "thread-conv-c"

    _write_jsonl(
        thread_a / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-a",
                "window-0001",
                roles=["user"],
                text="Draft migration runbook for the production rollout",
            ),
            _message_window_row(
                "conv-a",
                "window-0002",
                roles=["assistant"],
                text="Summarize the risk checklist for launch readiness",
            ),
            _message_window_row(
                "conv-a",
                "window-0003",
                roles=["user"],
                text="Completely unrelated singleton note about lunch plans",
            ),
        ],
    )
    _write_jsonl(
        thread_b / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-b",
                "window-0001",
                roles=["assistant"],
                text="Build rollback steps and migration safety checks",
            ),
            _message_window_row(
                "conv-b",
                "window-0002",
                roles=["user"],
                text="Another isolated note that should stay outside the large cluster",
            ),
        ],
    )
    _write_jsonl(
        thread_c / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-c",
                "window-0001",
                roles=["user"],
                text="Capture launch checklist follow-ups and audit reminders",
            )
        ],
    )
    _write_jsonl(
        thread_a / "window_clusters.jsonl",
        [
            _window_cluster_row(
                "conv-a",
                "window-0001",
                cluster_id="cluster_000001",
                cluster_size=4,
            ),
            _window_cluster_row(
                "conv-a",
                "window-0002",
                cluster_id="cluster_000001",
                cluster_size=4,
            ),
            _window_cluster_row(
                "conv-a",
                "window-0003",
                cluster_id="cluster_000002",
                cluster_size=1,
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
                cluster_size=4,
            ),
            _window_cluster_row(
                "conv-b",
                "window-0002",
                cluster_id="cluster_000003",
                cluster_size=1,
            ),
        ],
    )
    _write_jsonl(
        thread_c / "window_clusters.jsonl",
        [
            _window_cluster_row(
                "conv-c",
                "window-0001",
                cluster_id="cluster_000001",
                cluster_size=4,
            )
        ],
    )
    _write_jsonl(
        thread_a / "window_neighbors.jsonl",
        [
            _window_neighbors_row(
                "conv-a",
                "window-0001",
                neighbors=[
                    {
                        "provider_id": "openai",
                        "conversation_id": "conv-b",
                        "window_id": "window-0001",
                        "score": 0.91,
                    },
                    {
                        "provider_id": "openai",
                        "conversation_id": "conv-a",
                        "window_id": "window-0002",
                        "score": 0.89,
                    },
                ],
            ),
            _window_neighbors_row(
                "conv-a",
                "window-0002",
                neighbors=[
                    {
                        "provider_id": "openai",
                        "conversation_id": "conv-c",
                        "window_id": "window-0001",
                        "score": 0.88,
                    }
                ],
            ),
        ],
    )
    _write_jsonl(
        thread_b / "window_neighbors.jsonl",
        [
            _window_neighbors_row(
                "conv-b",
                "window-0001",
                neighbors=[
                    {
                        "provider_id": "openai",
                        "conversation_id": "conv-a",
                        "window_id": "window-0001",
                        "score": 0.91,
                    },
                    {
                        "provider_id": "openai",
                        "conversation_id": "conv-c",
                        "window_id": "window-0001",
                        "score": 0.87,
                    },
                ],
            )
        ],
    )
    _write_jsonl(
        thread_c / "window_neighbors.jsonl",
        [
            _window_neighbors_row(
                "conv-c",
                "window-0001",
                neighbors=[
                    {
                        "provider_id": "openai",
                        "conversation_id": "conv-b",
                        "window_id": "window-0001",
                        "score": 0.87,
                    }
                ],
            )
        ],
    )


def test_render_semantic_preview_cli_happy_path(tmp_path, capsys):
    root = tmp_path / "artifacts" / "output" / "openai"
    thread_a = root / "thread-conv-a"
    thread_b = root / "thread-conv-b"

    _write_jsonl(
        thread_a / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-a",
                "window-0003",
                roles=["user", "assistant", "user"],
                text=(
                    "Draft the migration checklist\n\n"
                    "Start with schema audit\n\n"
                    "Also capture rollout risks"
                ),
            )
        ],
    )
    _write_jsonl(
        thread_b / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-b",
                "window-0001",
                roles=["user", "assistant"],
                text=(
                    "Build a deployment checklist\n\n"
                    "Include rollback steps and monitoring"
                ),
            )
        ],
    )
    _write_jsonl(
        thread_a / "window_neighbors.jsonl",
        [
            _window_neighbors_row(
                "conv-a",
                "window-0003",
                neighbors=[
                    {
                        "provider_id": "openai",
                        "conversation_id": "conv-b",
                        "window_id": "window-0001",
                        "score": 0.8612,
                    }
                ],
            )
        ],
    )

    main(
        [
            "--locale",
            "en-US",
            "analyze",
            "semantic-preview",
            "--input",
            str(root),
            "--thread",
            "conv-a",
            "--window",
            "window-0003",
            "--top-k",
            "1",
        ]
    )

    output = capsys.readouterr().out
    assert "=== Target Window ===" in output
    assert "[window-0003]" in output
    assert "[Meta]" in output
    assert "Turns: 2" in output
    assert "Messages: 3" in output
    assert "Turn 1" in output
    assert "U: Draft the migration checklist" in output
    assert "A: Start with schema audit" in output
    assert "Turn 2" in output
    assert "U: Also capture rollout risks" in output
    assert "=== Top Neighbors ===" in output
    assert "#1 (0.86 👍 very similar)" in output
    assert "[window-0001]" in output
    assert "A: Include rollback steps and monitoring" in output
    assert "NEXT" in output
    assert "thread: conv-b" in output
    assert "window: window-0001" in output


def test_render_semantic_preview_no_neighbors_found(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai" / "thread-conv-a"
    _write_jsonl(
        root / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-a",
                "window-0001",
                roles=["assistant"],
                text="Standalone summary",
            )
        ],
    )

    rendered = render_semantic_preview(
        input_root=tmp_path / "artifacts" / "output" / "openai",
        conversation_id="conv-a",
        window_id="window-0001",
        include_text=False,
    )

    assert "=== Top Neighbors ===" in rendered
    assert "No neighbors found" in rendered


def test_render_semantic_preview_truncates_and_hides_meta(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai" / "thread-conv-a"
    _write_jsonl(
        root / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-a",
                "window-0001",
                roles=["user"],
                text="x" * 50,
            )
        ],
    )

    rendered = render_semantic_preview(
        input_root=tmp_path / "artifacts" / "output" / "openai",
        conversation_id="conv-a",
        window_id="window-0001",
        max_chars=20,
        show_meta=False,
    )

    assert "[Meta]" not in rendered
    assert "Turn 1" in rendered
    assert "U: xxxxxxx..." in rendered
    assert "..." in rendered


def test_render_semantic_preview_turn_grouping_and_similarity_labels(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    thread_a = root / "thread-conv-a"
    thread_b = root / "thread-conv-b"
    thread_c = root / "thread-conv-c"

    _write_jsonl(
        thread_a / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-a",
                "window-0001",
                roles=["user", "assistant", "user"],
                text="alpha\n\nbeta\n\ngamma",
            )
        ],
    )
    _write_jsonl(
        thread_b / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-b",
                "window-0002",
                roles=["user", "assistant"],
                text="one\n\ntwo",
            )
        ],
    )
    _write_jsonl(
        thread_c / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-c",
                "window-0003",
                roles=["user"],
                text="three",
            )
        ],
    )
    _write_jsonl(
        thread_a / "window_neighbors.jsonl",
        [
            _window_neighbors_row(
                "conv-a",
                "window-0001",
                neighbors=[
                    {
                        "provider_id": "openai",
                        "conversation_id": "conv-b",
                        "window_id": "window-0002",
                        "score": 0.9101,
                    },
                    {
                        "provider_id": "openai",
                        "conversation_id": "conv-c",
                        "window_id": "window-0003",
                        "score": 0.6400,
                    },
                ],
            )
        ],
    )

    rendered = render_semantic_preview(
        input_root=root,
        conversation_id="conv-a",
        window_id="window-0001",
    )

    assert "Turn 1\nU: alpha\nA: beta" in rendered
    assert "Turn 2\nU: gamma" in rendered
    assert "#1 (0.91 🔥 almost same)" in rendered
    assert "#2 (0.64 ... weak)" in rendered
    assert rendered.rstrip().endswith(
        "NEXT\nthread: conv-b\nwindow: window-0002"
    )


def test_render_semantic_preview_cluster_list_view(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_cluster_fixture(root)

    rendered = render_semantic_preview(input_root=root)

    assert "Cluster cluster_000001" in rendered
    assert "size: 4" in rendered
    assert "threads: 3" in rendered
    assert "cross-thread: yes" in rendered
    assert "Representative:" in rendered
    assert '[conv-a / window-0001] "Draft migration runbook for the production rollout"' in rendered
    assert '[conv-b / window-0001] "Build rollback steps and migration safety checks"' in rendered
    assert '[conv-c / window-0001] "Capture launch checklist follow-ups and audit reminders"' in rendered


def test_representative_selection_is_independent_of_excerpt_truncation():
    members = [
        WindowClusterMember(
            provider_id="openai",
            conversation_id="conv-a",
            window_id="window-0001",
            cluster_id="cluster_000001",
            cluster_size=2,
            edge_policy="mutual-only",
        ),
        WindowClusterMember(
            provider_id="openai",
            conversation_id="conv-b",
            window_id="window-0001",
            cluster_id="cluster_000001",
            cluster_size=2,
            edge_policy="mutual-only",
        ),
    ]
    windows = {
        ("conv-a", "window-0001"): WindowPreviewRecord(
            provider_id="openai",
            conversation_id="conv-a",
            window_id="window-0001",
            message_ids=("m1",),
            message_count=1,
            char_count=64,
            ts_start=1,
            ts_end=2,
            roles=("user",),
            text="Shared prefix for preview A with a distinct semantic suffix",
        ),
        ("conv-b", "window-0001"): WindowPreviewRecord(
            provider_id="openai",
            conversation_id="conv-b",
            window_id="window-0001",
            message_ids=("m2",),
            message_count=1,
            char_count=64,
            ts_start=3,
            ts_end=4,
            roles=("assistant",),
            text="Shared prefix for preview B with another distinct ending",
        ),
    }

    short = select_representative_cluster_windows(
        members=members,
        windows=windows,
        neighbor_index=None,
        window_cap=2,
        max_window_chars=20,
    )
    long = select_representative_cluster_windows(
        members=members,
        windows=windows,
        neighbor_index=None,
        window_cap=2,
        max_window_chars=200,
    )

    assert [(row["conversation_id"], row["window_id"]) for row in short] == [
        ("conv-a", "window-0001"),
        ("conv-b", "window-0001"),
    ]
    assert [(row["conversation_id"], row["window_id"]) for row in long] == [
        ("conv-a", "window-0001"),
        ("conv-b", "window-0001"),
    ]


def test_render_semantic_preview_cli_cluster_list_default_view(tmp_path, capsys):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_cluster_fixture(root)

    main(
        [
            "--locale",
            "en-US",
            "analyze",
            "semantic-preview",
            "--input",
            str(root),
            "--top-clusters",
            "1",
        ]
    )

    output = capsys.readouterr().out
    assert "Cluster cluster_000001" in output
    assert "threads: 3" in output


def test_render_semantic_preview_cluster_detail_view_with_neighbors(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_cluster_fixture(root)

    rendered = render_semantic_preview(
        input_root=root,
        cluster_id="cluster_000001",
        top_k=2,
    )

    assert "Cluster cluster_000001" in rendered
    assert "Members:" in rendered
    assert "cluster-neighbors: 2 (same-thread 1, cross-thread 1)" in rendered
    assert "neighbor-scores:" in rendered
    assert "[conv-b / window-0001] 0.91 (cross-thread)" in rendered
    assert "[conv-a / window-0002] 0.89 (same-thread)" in rendered


def test_render_semantic_preview_conversation_view_and_filters(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_cluster_fixture(root)

    rendered = render_semantic_preview(
        input_root=root,
        conversation_id="conv-a",
        min_cluster_size=2,
        cross_thread_only=True,
    )

    assert "Conversation conv-a" in rendered
    assert "clusters: 1" in rendered
    assert "Cluster cluster_000001" in rendered
    assert "Cross-thread connections:" in rendered
    assert "- conv-b: 1 links, strongest 0.91" in rendered
    assert "- conv-c: 1 links, strongest 0.88" in rendered
    assert "cluster_000002" not in rendered


def test_render_semantic_preview_cluster_detail_without_optional_neighbors(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_cluster_fixture(root)
    for path in root.rglob("window_neighbors.jsonl"):
        path.unlink()

    rendered = render_semantic_preview(
        input_root=root,
        cluster_id="cluster_000001",
    )

    assert "Cluster cluster_000001" in rendered
    assert "Members:" in rendered
    assert "neighbor-scores:" not in rendered
    assert "cluster-neighbors:" not in rendered


def test_render_semantic_preview_json_output(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_cluster_fixture(root)

    rendered = render_semantic_preview(
        input_root=root,
        json_output=True,
        top_clusters=1,
    )
    payload = json.loads(rendered)

    assert payload["view"] == "cluster_list"
    assert len(payload["clusters"]) == 1
    assert payload["clusters"][0]["cluster_id"] == "cluster_000001"
    assert payload["clusters"][0]["cross_thread"] is True
    assert payload["clusters"][0]["distinct_conversations"] == 3


def test_semantic_preview_cli_errors_when_window_missing(tmp_path, caplog):
    caplog.set_level(logging.ERROR)
    root = tmp_path / "artifacts" / "output" / "openai" / "thread-conv-a"
    _write_jsonl(
        root / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-a",
                "window-0001",
                roles=["user"],
                text="hello",
            )
        ],
    )

    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--locale",
                "en-US",
                "analyze",
                "semantic-preview",
                "--input",
                str(tmp_path / "artifacts" / "output" / "openai"),
                "--thread",
                "conv-a",
                "--window",
                "window-9999",
            ]
        )

    assert exc.value.code == 2
    assert "window not found: conversation_id=conv-a window_id=window-9999" in caplog.text
