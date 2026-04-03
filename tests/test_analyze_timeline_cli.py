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
            row = {
                "record_type": "message",
                "provider_id": provider_id,
                "conversation_id": conversation_id,
                "message_id": message.get("message_id", f"m{idx}"),
                "content": {"content_type": "text", "parts": []},
            }
            if "role" in message:
                row["role"] = message["role"]
            if "ts" in message:
                row["ts"] = message["ts"]
            if "text" in message:
                row["text"] = message["text"]
                if isinstance(message["text"], str):
                    row["content"]["parts"] = [message["text"]]
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def _run_cli(monkeypatch, capsys, argv: list[str]) -> str:
    monkeypatch.setattr(sys, "argv", argv)
    main()
    return capsys.readouterr().out


def test_analyze_timeline_single_file_day_text_output(tmp_path, monkeypatch, capsys):
    parsed = tmp_path / "thread-conv-1" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-1",
        [
            {"role": "user", "ts": 1704067200000, "text": "hello"},
            {"role": "assistant", "ts": 1704067260000, "text": "world"},
            {"role": "system", "ts": 1704153600000, "text": "abc"},
            {"role": "assistant", "text": "ignored"},
        ],
    )

    out = _run_cli(
        monkeypatch,
        capsys,
        ["llm-logparser", "analyze", "timeline", "--input", str(parsed)],
    )

    assert "Timeline (bucket: day)" in out
    assert "2024-01-01" in out
    assert "  messages: 2" in out
    assert "  user: 1" in out
    assert "  assistant: 1" in out
    assert "  other: 0" in out
    assert "  characters: 10" in out
    assert "2024-01-02" in out
    assert "  messages: 1" in out
    assert "  user: 0" in out
    assert "  assistant: 0" in out
    assert "  other: 1" in out
    assert "  characters: 3" in out


def test_analyze_timeline_directory_aggregates_multiple_threads(tmp_path, monkeypatch, capsys):
    root = tmp_path / "parsed"
    _write_parsed_jsonl(
        root / "a" / "thread-conv-a" / "parsed.jsonl",
        "conv-a",
        [
            {"role": "user", "ts": 1704067200000, "text": "hi"},
        ],
    )
    _write_parsed_jsonl(
        root / "b" / "thread-conv-b" / "parsed.jsonl",
        "conv-b",
        [
            {"role": "assistant", "ts": 1704067300000, "text": "hey"},
            {"role": "tool", "ts": 1704153600000, "text": "abcd"},
        ],
    )

    out = _run_cli(
        monkeypatch,
        capsys,
        ["llm-logparser", "analyze", "timeline", "--input", str(root)],
    )

    assert "2024-01-01" in out
    assert "  messages: 2" in out
    assert "  user: 1" in out
    assert "  assistant: 1" in out
    assert "  other: 0" in out
    assert "  characters: 5" in out
    assert "2024-01-02" in out
    assert "  messages: 1" in out
    assert "  other: 1" in out
    assert "  characters: 4" in out


def test_analyze_timeline_json_output(tmp_path, monkeypatch, capsys):
    parsed = tmp_path / "thread-conv-1" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-1",
        [
            {"role": "user", "ts": 1704067200000, "text": "hi"},
            {"role": "assistant", "ts": 1704067260000, "text": "there"},
            {"role": "system", "ts": 1704153600000, "text": "abc"},
        ],
    )

    out = _run_cli(
        monkeypatch,
        capsys,
        [
            "llm-logparser",
            "analyze",
            "timeline",
            "--input",
            str(parsed),
            "--bucket",
            "day",
            "--json",
        ],
    )

    payload = json.loads(out)
    assert payload["bucket"] == "day"
    assert payload["timeline"] == [
        {
            "bucket_start": "2024-01-01T00:00:00Z",
            "message_count": 2,
            "user_messages": 1,
            "assistant_messages": 1,
            "other_roles": 0,
            "characters_total": 7,
        },
        {
            "bucket_start": "2024-01-02T00:00:00Z",
            "message_count": 1,
            "user_messages": 0,
            "assistant_messages": 0,
            "other_roles": 1,
            "characters_total": 3,
        },
    ]


def test_analyze_timeline_json_is_identical_across_locales(tmp_path, monkeypatch, capsys):
    parsed = tmp_path / "thread-conv-cross-locale" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-cross-locale",
        [
            {"role": "user", "ts": 1704067200000, "text": "hello"},
            {"role": "assistant", "ts": 1704067260000, "text": "world"},
        ],
    )

    en_output = _run_cli(
        monkeypatch,
        capsys,
        [
            "llm-logparser",
            "--locale",
            "en-US",
            "analyze",
            "timeline",
            "--input",
            str(parsed),
            "--json",
        ],
    )
    ja_output = _run_cli(
        monkeypatch,
        capsys,
        [
            "llm-logparser",
            "--locale",
            "ja-JP",
            "analyze",
            "timeline",
            "--input",
            str(parsed),
            "--json",
        ],
    )

    assert en_output == ja_output


def test_analyze_timeline_hour_bucket_json_output(tmp_path, monkeypatch, capsys):
    parsed = tmp_path / "thread-conv-1" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-1",
        [
            {"role": "user", "ts": 1704067200000, "text": "hi"},
            {"role": "assistant", "ts": 1704070800000, "text": "hello"},
        ],
    )

    out = _run_cli(
        monkeypatch,
        capsys,
        [
            "llm-logparser",
            "analyze",
            "timeline",
            "--input",
            str(parsed),
            "--bucket",
            "hour",
            "--json",
        ],
    )

    payload = json.loads(out)
    assert payload["timeline"] == [
        {
            "bucket_start": "2024-01-01T00:00:00Z",
            "message_count": 1,
            "user_messages": 1,
            "assistant_messages": 0,
            "other_roles": 0,
            "characters_total": 2,
        },
        {
            "bucket_start": "2024-01-01T01:00:00Z",
            "message_count": 1,
            "user_messages": 0,
            "assistant_messages": 1,
            "other_roles": 0,
            "characters_total": 5,
        },
    ]


def test_analyze_timeline_out_writes_file(tmp_path, monkeypatch, capsys):
    parsed = tmp_path / "thread-conv-1" / "parsed.jsonl"
    out_path = tmp_path / "timeline.txt"
    _write_parsed_jsonl(
        parsed,
        "conv-1",
        [
            {"role": "assistant", "ts": 1704067200000, "text": "Hi"},
        ],
    )

    _run_cli(
        monkeypatch,
        capsys,
        [
            "llm-logparser",
            "analyze",
            "timeline",
            "--input",
            str(parsed),
            "--out",
            str(out_path),
        ],
    )

    written = out_path.read_text(encoding="utf-8")
    assert written.endswith("\n")
    assert "Timeline (bucket: day)" in written
    assert "2024-01-01" in written
    assert "  assistant: 1" in written
