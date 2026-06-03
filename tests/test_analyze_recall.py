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
                text="Plan the index foundation",
            ),
            _message(
                conversation_id="conv-a",
                message_id="a2",
                role="assistant",
                ts=1704067202000,
                text="The recall index should stay read only",
            ),
            _message(
                conversation_id="conv-a",
                message_id="a3",
                role="user",
                ts=1704067203000,
                text="Confirm the implementation constraints",
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
                role="user",
                ts=1704067300000,
                text="Conversation boundary setup",
            ),
            _message(
                conversation_id="conv-b",
                message_id="b2",
                role="assistant",
                ts=1704067301000,
                text="Recall search can filter by conversation",
            ),
            _message(
                conversation_id="conv-b",
                message_id="b3",
                role="tool",
                ts=1704067302000,
                text="Context tool output should remain visible",
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
            "index",
        ]
    )

    output = capsys.readouterr().out
    assert "openai/conv-a/a1" in output
    assert "role=user" in output
    assert "Plan the index foundation" in output
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
    assert payload["schema_version"] == "0.3"
    assert payload["context_before"] == 0
    assert payload["context_after"] == 0
    assert payload["bookends"] == 0
    assert [row["message_id"] for row in payload["results"]] == ["b2", "a2"]
    assert "rowid" not in first.lower()
    assert set(payload["results"][0]) == {
        "anchor",
        "bookend_end",
        "bookend_start",
        "context_after",
        "context_before",
        "provider_id",
        "conversation_id",
        "message_id",
        "role",
        "ts",
        "text",
    }
    assert payload["results"][0]["anchor"]["message_id"] == payload["results"][0]["message_id"]
    assert payload["results"][0]["context_before"] == []
    assert payload["results"][0]["context_after"] == []
    assert payload["results"][0]["bookend_start"] == []
    assert payload["results"][0]["bookend_end"] == []


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
    assert [row["message_id"] for row in payload["results"]] == ["b2", "a2"]


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
    assert [row["message_id"] for row in payload["results"]] == ["b2", "a2"]
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
    assert [row["message_id"] for row in payload["results"]] == ["b2"]
    assert {row["conversation_id"] for row in payload["results"]} == {"conv-b"}


def test_analyze_recall_context_before_includes_previous_same_conversation_message(
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
            "read",
            "--context-before",
            "1",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    result = payload["results"][0]
    assert result["message_id"] == "a2"
    assert [row["message_id"] for row in result["context_before"]] == ["a1"]
    assert result["context_after"] == []


def test_analyze_recall_context_after_includes_next_same_conversation_message(
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
            "read",
            "--context-after",
            "1",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    result = payload["results"][0]
    assert result["message_id"] == "a2"
    assert result["context_before"] == []
    assert [row["message_id"] for row in result["context_after"]] == ["a3"]


def test_analyze_recall_context_does_not_cross_conversation_boundaries(
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
            "boundary",
            "--context-before",
            "1",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    result = payload["results"][0]
    assert result["message_id"] == "b1"
    assert result["conversation_id"] == "conv-b"
    assert result["context_before"] == []


def test_analyze_recall_role_filter_applies_only_to_anchor_not_context(
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
            "recall",
            "--role",
            "assistant",
            "--context-after",
            "1",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    b_result = next(row for row in payload["results"] if row["message_id"] == "b2")
    assert b_result["role"] == "assistant"
    assert [row["message_id"] for row in b_result["context_after"]] == ["b3"]
    assert [row["role"] for row in b_result["context_after"]] == ["tool"]


def test_analyze_recall_text_output_renders_anchor_and_context_compactly(
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
            "read",
            "--context-before",
            "1",
            "--context-after",
            "1",
        ]
    )

    output = capsys.readouterr().out
    assert "openai/conv-a/a2" in output
    assert "context_before:" in output
    assert "a1 role=user" in output
    assert "context_after:" in output
    assert "a3 role=user" in output
    assert "rowid" not in output.lower()


def test_analyze_recall_bookends_include_first_and_last_same_conversation_messages(
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
            "read",
            "--bookends",
            "1",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    result = payload["results"][0]
    assert result["message_id"] == "a2"
    assert [row["message_id"] for row in result["bookend_start"]] == ["a1"]
    assert [row["message_id"] for row in result["bookend_end"]] == ["a3"]


def test_analyze_recall_bookends_do_not_cross_conversation_boundaries(
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
            "boundary",
            "--bookends",
            "1",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    result = payload["results"][0]
    assert result["conversation_id"] == "conv-b"
    bookend_ids = [
        row["conversation_id"]
        for row in [*result["bookend_start"], *result["bookend_end"]]
    ]
    assert set(bookend_ids) <= {"conv-b"}


def test_analyze_recall_role_filter_applies_only_to_anchor_not_bookends(
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
            "recall",
            "--role",
            "assistant",
            "--bookends",
            "1",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    b_result = next(row for row in payload["results"] if row["message_id"] == "b2")
    assert b_result["role"] == "assistant"
    assert [row["role"] for row in b_result["bookend_start"]] == ["user"]
    assert [row["role"] for row in b_result["bookend_end"]] == ["tool"]


def test_analyze_recall_bookends_dedupe_anchor_and_context_messages(
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
            "read",
            "--context-before",
            "1",
            "--context-after",
            "1",
            "--bookends",
            "2",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    result = payload["results"][0]
    assert result["message_id"] == "a2"
    assert [row["message_id"] for row in result["context_before"]] == ["a1"]
    assert [row["message_id"] for row in result["context_after"]] == ["a3"]
    assert result["bookend_start"] == []
    assert result["bookend_end"] == []


def test_analyze_recall_text_output_renders_bookends_compactly(
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
            "read",
            "--bookends",
            "1",
        ]
    )

    output = capsys.readouterr().out
    assert "openai/conv-a/a2" in output
    assert "bookend_start:" in output
    assert "a1 role=user" in output
    assert "bookend_end:" in output
    assert "a3 role=user" in output
    assert "rowid" not in output.lower()


def test_analyze_recall_negative_bookends_fail_clearly(
    tmp_path: Path,
    caplog,
):
    provider_root = _build_provider_root(tmp_path)
    caplog.set_level(logging.ERROR)

    with pytest.raises(SystemExit) as exc:
        main(
            [
                "analyze",
                "recall",
                "--input",
                str(provider_root),
                "--query",
                "read",
                "--bookends",
                "-1",
            ]
        )

    assert exc.value.code == 2
    assert "bookends must be >= 0" in caplog.text


def test_analyze_recall_negative_context_values_fail_clearly(
    tmp_path: Path,
    caplog,
):
    provider_root = _build_provider_root(tmp_path)
    caplog.set_level(logging.ERROR)

    with pytest.raises(SystemExit) as exc:
        main(
            [
                "analyze",
                "recall",
                "--input",
                str(provider_root),
                "--query",
                "read",
                "--context-before",
                "-1",
            ]
        )

    assert exc.value.code == 2
    assert "context_before must be >= 0" in caplog.text
