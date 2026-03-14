import json
import sqlite3
import sys
from pathlib import Path

import pytest

from llm_logparser.cli.cli import main
from llm_logparser.core.l1_derivation import ThreadMetrics, build_thread_stats_artifact
from llm_logparser.core.message_windows import render_message_windows_jsonl
from llm_logparser.l2_sqlite import build_analysis_db


def _canonical_message(
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


def _write_thread_artifacts(
    provider_dir: Path,
    conversation_id: str,
    messages: list[dict],
    *,
    include_thread_stats: bool = True,
    include_windows: bool = True,
) -> Path:
    thread_dir = provider_dir / f"thread-{conversation_id}"
    thread_dir.mkdir(parents=True, exist_ok=True)

    parsed_path = thread_dir / "parsed.jsonl"
    with parsed_path.open("w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "record_type": "thread",
                    "provider_id": messages[0]["provider_id"],
                    "conversation_id": conversation_id,
                    "message_count": len(messages),
                },
                ensure_ascii=True,
            )
            + "\n"
        )
        for row in messages:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")

    if include_thread_stats:
        metrics = ThreadMetrics(conversation_id=conversation_id)
        for row in messages:
            metrics.add_message(row)
        (thread_dir / "thread_stats.json").write_text(
            json.dumps(
                build_thread_stats_artifact(
                    metrics,
                    provider_id=messages[0]["provider_id"],
                ),
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )

    if include_windows:
        (thread_dir / "message_windows.jsonl").write_text(
            render_message_windows_jsonl(messages),
            encoding="utf-8",
        )

    return thread_dir


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _dump_db(db_path: Path) -> dict[str, list[tuple]]:
    conn = sqlite3.connect(db_path)
    try:
        return {
            "threads": conn.execute(
                """
                SELECT provider_id, conversation_id, message_count, user_messages,
                       assistant_messages, other_roles, characters_total,
                       first_timestamp, last_timestamp
                FROM threads
                ORDER BY conversation_id
                """
            ).fetchall(),
            "messages": conn.execute(
                """
                SELECT provider_id, conversation_id, message_id, role, ts, char_count, text
                FROM messages
                ORDER BY conversation_id, ts, message_id
                """
            ).fetchall(),
            "message_windows": conn.execute(
                """
                SELECT provider_id, conversation_id, window_id, message_count,
                       char_count, ts_start, ts_end, text
                FROM message_windows
                ORDER BY conversation_id, window_id
                """
            ).fetchall(),
        }
    finally:
        conn.close()


def _build_fixture_root(tmp_path: Path, *, include_missing_artifacts: bool = False) -> Path:
    provider_dir = tmp_path / "openai"
    provider_dir.mkdir(parents=True, exist_ok=True)

    thread_a_messages = [
        _canonical_message("openai", "conv-a", "m1", "user", 1704067201000, "hello"),
        _canonical_message("openai", "conv-a", "m2", "assistant", 1704067202000, "world"),
        _canonical_message("openai", "conv-a", "m3", "tool", 1704067203000, "tool-output"),
    ]
    thread_b_messages = [
        _canonical_message("openai", "conv-b", "m1", "system", 1704067301000, "policy"),
        _canonical_message("openai", "conv-b", "m2", "assistant", 1704067302000, "answer"),
    ]

    _write_thread_artifacts(provider_dir, "conv-a", thread_a_messages)
    _write_thread_artifacts(
        provider_dir,
        "conv-b",
        thread_b_messages,
        include_thread_stats=not include_missing_artifacts,
        include_windows=not include_missing_artifacts,
    )

    return tmp_path


def test_l2_sqlite_cli_builds_database_from_fixture_threads(tmp_path, monkeypatch, capsys):
    root = _build_fixture_root(tmp_path)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "llm-logparser",
            "analyze",
            "sqlite-build",
            "--input",
            str(root),
            "--provider",
            "openai",
        ],
    )
    main()
    capsys.readouterr()

    db_path = root / "openai" / "analysis.db"
    assert db_path.exists()

    conn = sqlite3.connect(db_path)
    try:
        assert _table_count(conn, "threads") == 2
        assert _table_count(conn, "messages") == 5
        assert _table_count(conn, "message_windows") == 2
    finally:
        conn.close()


def test_l2_sqlite_builder_is_deterministic(tmp_path):
    root = _build_fixture_root(tmp_path)

    first = build_analysis_db(root, "openai")
    first_dump = _dump_db(first["db_path"])

    second = build_analysis_db(root, "openai", overwrite=True)
    second_dump = _dump_db(second["db_path"])

    assert first_dump == second_dump


def test_l2_sqlite_builder_overwrite_behavior(tmp_path):
    root = _build_fixture_root(tmp_path)

    build_analysis_db(root, "openai")

    with pytest.raises(FileExistsError, match="analysis\\.db already exists"):
        build_analysis_db(root, "openai")

    rebuilt = build_analysis_db(root, "openai", overwrite=True)
    assert rebuilt["db_path"] == root / "openai" / "analysis.db"


def test_l2_sqlite_builder_tolerates_missing_artifacts(tmp_path):
    root = _build_fixture_root(tmp_path, include_missing_artifacts=True)

    result = build_analysis_db(root, "openai")
    conn = sqlite3.connect(result["db_path"])
    try:
        assert _table_count(conn, "threads") == 1
        assert _table_count(conn, "messages") == 5
        assert _table_count(conn, "message_windows") == 1
    finally:
        conn.close()


def test_l2_sqlite_builder_raises_on_invalid_json(tmp_path):
    root = _build_fixture_root(tmp_path)
    windows_path = root / "openai" / "thread-conv-a" / "message_windows.jsonl"
    windows_path.write_text("{bad json}\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"invalid JSON .*message_windows\.jsonl:1"):
        build_analysis_db(root, "openai")
