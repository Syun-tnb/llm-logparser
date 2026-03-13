import json
import sys
from pathlib import Path

from llm_logparser.cli.cli import main


def _write_parsed_jsonl(
    path: Path,
    conversation_id: str,
    messages: list[dict],
    *,
    provider_id: str = "openai",
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
        for idx, message in enumerate(messages, start=1):
            text = message.get("text")
            row = {
                "record_type": "message",
                "provider_id": provider_id,
                "conversation_id": conversation_id,
                "message_id": message.get("message_id", f"m{idx}"),
                "role": message.get("role", "user"),
                "content": {"content_type": "text", "parts": []},
            }
            if "ts" in message:
                row["ts"] = message["ts"]
            if "text" in message:
                row["text"] = text
                if isinstance(text, str):
                    row["content"]["parts"] = [text]
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def test_analyze_stats_single_file_text_output(tmp_path, monkeypatch, capsys):
    parsed = tmp_path / "thread-conv-1" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-1",
        [
            {"message_id": "m1", "role": "user", "ts": 1704067200000, "text": "hello"},
            {"message_id": "m2", "role": "assistant", "ts": 1704067260000, "text": "world!"},
            {"message_id": "m3", "role": "system", "ts": 1704067290000, "text": "abc"},
        ],
    )

    monkeypatch.setattr(
        sys,
        "argv",
        ["llm-logparser", "analyze", "stats", "--input", str(parsed)],
    )
    main()

    out = capsys.readouterr().out
    assert "Threads: 1" in out
    assert "Messages: 3" in out
    assert "User messages: 1" in out
    assert "Assistant messages: 1" in out
    assert "Other roles: 1" in out
    assert "Characters total: 14" in out
    assert "Characters (user): 5" in out
    assert "Characters (assistant): 6" in out
    assert "Average characters per message: 4.67" in out
    assert "First timestamp: 2024-01-01T00:00:00Z" in out
    assert "Last timestamp: 2024-01-01T00:01:30Z" in out
    assert "Conversation span (seconds): 90" in out
    assert "  min: 3" in out
    assert "  max: 3" in out
    assert "  avg: 3.00" in out


def test_analyze_stats_directory_recursive_json_output(tmp_path, monkeypatch, capsys):
    root = tmp_path / "parsed"
    _write_parsed_jsonl(
        root / "a" / "thread-conv-a" / "parsed.jsonl",
        "conv-a",
        [
            {"message_id": "m1", "role": "user"},
            {"message_id": "m2", "role": "assistant", "text": "ok"},
        ],
    )
    _write_parsed_jsonl(
        root / "b" / "thread-conv-b" / "parsed.jsonl",
        "conv-b",
        [
            {"message_id": "m1", "role": "user", "ts": 1704067200000, "text": "abcd"},
            {"message_id": "m2", "role": "assistant", "ts": 1704067260000, "text": "xyz"},
            {"message_id": "m3", "role": "system", "text": "note"},
        ],
    )

    monkeypatch.setattr(
        sys,
        "argv",
        ["llm-logparser", "analyze", "stats", "--input", str(root), "--json"],
    )
    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["threads"] == 2
    assert payload["messages"] == 5
    assert payload["user_messages"] == 2
    assert payload["assistant_messages"] == 2
    assert payload["other_roles"] == 1
    assert payload["characters_total"] == 13
    assert payload["characters_user"] == 4
    assert payload["characters_assistant"] == 5
    assert payload["avg_chars_per_message"] == 2.6
    assert payload["first_timestamp"] == "2024-01-01T00:00:00Z"
    assert payload["last_timestamp"] == "2024-01-01T00:01:00Z"
    assert payload["conversation_span_seconds"] == 60
    assert payload["messages_per_thread_min"] == 2
    assert payload["messages_per_thread_max"] == 3
    assert payload["messages_per_thread_avg"] == 2.5

    detail_by_id = {
        detail["conversation_id"]: detail for detail in payload["threads_detail"]
    }
    assert detail_by_id["conv-a"]["message_count"] == 2
    assert detail_by_id["conv-a"]["character_count"] == 2
    assert detail_by_id["conv-a"]["first_timestamp"] is None
    assert detail_by_id["conv-a"]["last_timestamp"] is None
    assert detail_by_id["conv-a"]["conversation_span_seconds"] is None
    assert detail_by_id["conv-b"]["message_count"] == 3
    assert detail_by_id["conv-b"]["character_count"] == 11
    assert detail_by_id["conv-b"]["first_timestamp"] == "2024-01-01T00:00:00Z"
    assert detail_by_id["conv-b"]["last_timestamp"] == "2024-01-01T00:01:00Z"
    assert detail_by_id["conv-b"]["conversation_span_seconds"] == 60


def test_analyze_stats_out_writes_text_file(tmp_path, monkeypatch, capsys):
    parsed = tmp_path / "thread-conv-1" / "parsed.jsonl"
    out_path = tmp_path / "stats.txt"
    _write_parsed_jsonl(
        parsed,
        "conv-1",
        [
            {"message_id": "m1", "role": "assistant", "text": "Hi"},
        ],
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "llm-logparser",
            "analyze",
            "stats",
            "--input",
            str(parsed),
            "--out",
            str(out_path),
        ],
    )
    main()

    assert capsys.readouterr().out == ""
    written = out_path.read_text(encoding="utf-8")
    assert "Threads: 1" in written
    assert "Messages: 1" in written
    assert "First timestamp: N/A" in written
    assert "Conversation span (seconds): N/A" in written


def test_analyze_stats_json_out_writes_json_file(tmp_path, monkeypatch, capsys):
    parsed = tmp_path / "thread-conv-1" / "parsed.jsonl"
    out_path = tmp_path / "stats.json"
    _write_parsed_jsonl(
        parsed,
        "conv-1",
        [
            {"message_id": "m1", "role": "user", "ts": 1704067200000, "text": "Hi"},
            {"message_id": "m2", "role": "assistant", "ts": 1704067230000, "text": "Hello"},
        ],
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "llm-logparser",
            "analyze",
            "stats",
            "--input",
            str(parsed),
            "--json",
            "--out",
            str(out_path),
        ],
    )
    main()

    assert capsys.readouterr().out == ""
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["threads"] == 1
    assert payload["messages"] == 2
    assert payload["first_timestamp"] == "2024-01-01T00:00:00Z"
    assert payload["last_timestamp"] == "2024-01-01T00:00:30Z"
