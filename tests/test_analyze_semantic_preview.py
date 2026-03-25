from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from llm_logparser.cli.cli import main
from llm_logparser.core.analyzer_semantic_preview import render_semantic_preview


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
        "schema_version": "1.0",
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
                    "user: Draft the migration checklist\n\n"
                    "assistant: Start with schema audit\n\n"
                    "user: Also capture rollout risks"
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
                    "user: Build a deployment checklist\n\n"
                    "assistant: Include rollback steps and monitoring"
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
    assert "### Window: window-0003 (conv-a)" in output
    assert "[Meta]" in output
    assert "Turns: 2" in output
    assert "Messages: 3" in output
    assert "[Content]" in output
    assert "[User] Draft the migration checklist" in output
    assert "[Assistant] Start with schema audit" in output
    assert "### Neighbors" in output
    assert "(1) score: 0.8612" in output
    assert "conv-b / window-0001" in output
    assert "[Assistant] Include rollback steps and monitoring" in output


def test_render_semantic_preview_no_neighbors_found(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai" / "thread-conv-a"
    _write_jsonl(
        root / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-a",
                "window-0001",
                roles=["assistant"],
                text="assistant: Standalone summary",
            )
        ],
    )

    rendered = render_semantic_preview(
        input_root=tmp_path / "artifacts" / "output" / "openai",
        conversation_id="conv-a",
        window_id="window-0001",
        include_text=False,
    )

    assert "### Neighbors" in rendered
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
                text="user: " + ("x" * 50),
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
    assert "[User] xxxxxxxxxx" in rendered
    assert "..." in rendered


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
                text="user: hello",
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
