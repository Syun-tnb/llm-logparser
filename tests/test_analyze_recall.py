from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from llm_logparser.cli.cli import main
from llm_logparser.l2_sqlite import build_analysis_db


def _message(
    *,
    conversation_id: str,
    message_id: str,
    role: str,
    ts: int,
    text: str,
) -> dict:
    return {
        "record_type": "message",
        "provider_id": "openai",
        "conversation_id": conversation_id,
        "message_id": message_id,
        "role": role,
        "ts": ts,
        "text": text,
        "content": {"content_type": "text", "parts": [text]},
    }


def _write_parsed_thread(provider_root: Path, conversation_id: str, messages: list[dict]) -> None:
    thread_dir = provider_root / f"thread-{conversation_id}"
    thread_dir.mkdir(parents=True, exist_ok=True)
    parsed_path = thread_dir / "parsed.jsonl"
    rows = [
        {
            "record_type": "thread",
            "provider_id": "openai",
            "conversation_id": conversation_id,
            "message_count": len(messages),
        },
        *messages,
    ]
    with parsed_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _build_provider_root(tmp_path: Path) -> Path:
    provider_root = tmp_path / "openai"
    provider_root.mkdir(parents=True, exist_ok=True)
    _write_parsed_thread(
        provider_root,
        "conv-a",
        [
            _message(
                conversation_id="conv-a",
                message_id="a1",
                role="user",
                ts=1704067201000,
                text="Plan the recall index foundation",
            ),
            _message(
                conversation_id="conv-a",
                message_id="a2",
                role="assistant",
                ts=1704067202000,
                text="The recall index should stay read only",
            ),
        ],
    )
    _write_parsed_thread(
        provider_root,
        "conv-b",
        [
            _message(
                conversation_id="conv-b",
                message_id="b1",
                role="assistant",
                ts=1704067301000,
                text="Recall search can filter by conversation",
            ),
            _message(
                conversation_id="conv-b",
                message_id="b2",
                role="user",
                ts=1704067302000,
                text="Unrelated export question",
            ),
        ],
    )
    build_analysis_db(tmp_path, "openai")
    return provider_root


def test_analyze_recall_missing_analysis_db_produces_clear_error(
    tmp_path: Path,
    caplog,
):
    provider_root = tmp_path / "openai"
    provider_root.mkdir(parents=True)
    caplog.set_level(logging.ERROR)

    with pytest.raises(SystemExit) as exc:
        main(
            [
                "analyze",
                "recall",
                "--input",
                str(provider_root),
                "--query",
                "recall",
            ]
        )

    assert exc.value.code == 2
    assert "analysis.db not found" in caplog.text
    assert "llm-logparser analyze sqlite-build" in caplog.text


def test_analyze_recall_basic_search_outputs_canonical_identity(
    tmp_path: Path,
    capsys,
):
    provider_root = _build_provider_root(tmp_path)

    main(
        [
            "analyze",
            "recall",
            "--input",
            str(provider_root),
            "--query",
            "foundation",
        ]
    )

    output = capsys.readouterr().out
    assert "openai/conv-a/a1" in output
    assert "role=user" in output
    assert "Plan the recall index foundation" in output
    assert "rowid" not in output.lower()


def test_analyze_recall_json_output_is_deterministic_and_excludes_rowid(
    tmp_path: Path,
    capsys,
):
    provider_root = _build_provider_root(tmp_path)
    argv = [
        "analyze",
        "recall",
        "--input",
        str(provider_root),
        "--query",
        "recall",
        "--json",
    ]

    main(argv)
    first = capsys.readouterr().out
    main(argv)
    second = capsys.readouterr().out

    assert first == second
    payload = json.loads(first)
    assert payload["artifact_type"] == "recall_results"
    assert payload["schema_version"] == "0.1"
    assert [row["message_id"] for row in payload["results"]] == ["a1", "b1", "a2"]
    assert "rowid" not in first.lower()
    assert set(payload["results"][0]) == {
        "provider_id",
        "conversation_id",
        "message_id",
        "role",
        "ts",
        "text",
    }


def test_analyze_recall_limit_works(tmp_path: Path, capsys):
    provider_root = _build_provider_root(tmp_path)

    main(
        [
            "analyze",
            "recall",
            "--input",
            str(provider_root),
            "--query",
            "recall",
            "--limit",
            "2",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert len(payload["results"]) == 2
    assert [row["message_id"] for row in payload["results"]] == ["a1", "b1"]


def test_analyze_recall_role_filter_works(tmp_path: Path, capsys):
    provider_root = _build_provider_root(tmp_path)

    main(
        [
            "analyze",
            "recall",
            "--input",
            str(provider_root),
            "--query",
            "recall",
            "--role",
            "assistant",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert [row["message_id"] for row in payload["results"]] == ["b1", "a2"]
    assert {row["role"] for row in payload["results"]} == {"assistant"}


def test_analyze_recall_conversation_filter_works(tmp_path: Path, capsys):
    provider_root = _build_provider_root(tmp_path)

    main(
        [
            "analyze",
            "recall",
            "--input",
            str(provider_root),
            "--query",
            "recall",
            "--conversation-id",
            "conv-b",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert [row["message_id"] for row in payload["results"]] == ["b1"]
    assert {row["conversation_id"] for row in payload["results"]} == {"conv-b"}
