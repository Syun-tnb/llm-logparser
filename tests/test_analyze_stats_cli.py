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
                "content": {"content_type": "text", "parts": []},
            }
            if "role" in message:
                row["role"] = message["role"]
            if "ts" in message:
                row["ts"] = message["ts"]
            if "text" in message:
                row["text"] = text
                if isinstance(text, str):
                    row["content"]["parts"] = [text]
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def _run_cli(monkeypatch, capsys, argv: list[str]) -> str:
    monkeypatch.setattr(sys, "argv", argv)
    main()
    return capsys.readouterr().out


def _iter_keys(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield key
            yield from _iter_keys(nested)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_keys(item)


def _build_threads_fixture(root: Path) -> None:
    _write_parsed_jsonl(
        root / "a" / "thread-conv-a" / "parsed.jsonl",
        "conv-a",
        [
            {"message_id": "m1", "role": "user", "ts": 1704067200000, "text": "abcdefghij"},
            {"message_id": "m2", "role": "assistant", "ts": 1704067250000, "text": "klmnopqrst"},
        ],
    )
    _write_parsed_jsonl(
        root / "b" / "thread-conv-b" / "parsed.jsonl",
        "conv-b",
        [
            {"message_id": "m1", "role": "user", "text": "aaaaa"},
            {"message_id": "m2", "role": "assistant", "text": "bbbbb"},
            {"message_id": "m3", "role": "system", "text": "ccccc"},
        ],
    )
    _write_parsed_jsonl(
        root / "c" / "thread-conv-c" / "parsed.jsonl",
        "conv-c",
        [
            {"message_id": "m1", "role": "user", "ts": 1704067200000, "text": "aa"},
            {"message_id": "m2", "role": "assistant", "ts": 1704067210000, "text": "bb"},
            {"message_id": "m3", "role": "tool", "ts": 1704067220000, "text": "cc"},
            {"message_id": "m4", "role": "system", "ts": 1704067220000, "text": "dd"},
        ],
    )
    _write_parsed_jsonl(
        root / "d" / "thread-conv-d" / "parsed.jsonl",
        "conv-d",
        [
            {"message_id": "m1", "role": "user", "ts": 1704067200000, "text": "aaaa"},
            {"message_id": "m2", "role": "assistant", "ts": 1704067210000, "text": "bbbb"},
            {"message_id": "m3", "ts": 1704067210000, "text": "cccc"},
        ],
    )


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

    out = _run_cli(
        monkeypatch,
        capsys,
        ["llm-logparser", "analyze", "stats", "--input", str(parsed)],
    )

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
    assert "Per-thread:" not in out
    assert "Other role breakdown:" not in out


def test_analyze_stats_per_thread_text_output_and_top(tmp_path, monkeypatch, capsys):
    root = tmp_path / "parsed"
    _build_threads_fixture(root)

    out = _run_cli(
        monkeypatch,
        capsys,
        [
            "llm-logparser",
            "analyze",
            "stats",
            "--input",
            str(root),
            "--per-thread",
            "--top",
            "2",
        ],
    )

    assert "Threads: 4" in out
    assert "Messages: 12" in out
    assert "Per-thread:" in out
    assert "conv-c  messages=4  chars=8  span=20" in out
    assert "conv-b  messages=3  chars=15  span=N/A" in out
    assert "conv-d  messages=3  chars=12  span=10" not in out
    assert "conv-a  messages=2  chars=20  span=50" not in out


def test_analyze_stats_sort_messages_text_uses_conversation_id_tiebreaker(
    tmp_path, monkeypatch, capsys
):
    root = tmp_path / "parsed"
    _build_threads_fixture(root)

    out = _run_cli(
        monkeypatch,
        capsys,
        [
            "llm-logparser",
            "analyze",
            "stats",
            "--input",
            str(root),
            "--per-thread",
            "--sort",
            "messages",
        ],
    )

    pos_c = out.index("conv-c  messages=4  chars=8  span=20")
    pos_b = out.index("conv-b  messages=3  chars=15  span=N/A")
    pos_d = out.index("conv-d  messages=3  chars=12  span=10")
    pos_a = out.index("conv-a  messages=2  chars=20  span=50")
    assert pos_c < pos_b < pos_d < pos_a


def test_analyze_stats_sort_chars_text(tmp_path, monkeypatch, capsys):
    root = tmp_path / "parsed"
    _build_threads_fixture(root)

    out = _run_cli(
        monkeypatch,
        capsys,
        [
            "llm-logparser",
            "analyze",
            "stats",
            "--input",
            str(root),
            "--per-thread",
            "--sort",
            "chars",
        ],
    )

    pos_a = out.index("conv-a  messages=2  chars=20  span=50")
    pos_b = out.index("conv-b  messages=3  chars=15  span=N/A")
    pos_d = out.index("conv-d  messages=3  chars=12  span=10")
    pos_c = out.index("conv-c  messages=4  chars=8  span=20")
    assert pos_a < pos_b < pos_d < pos_c


def test_analyze_stats_sort_span_text_null_last(tmp_path, monkeypatch, capsys):
    root = tmp_path / "parsed"
    _build_threads_fixture(root)

    out = _run_cli(
        monkeypatch,
        capsys,
        [
            "llm-logparser",
            "analyze",
            "stats",
            "--input",
            str(root),
            "--per-thread",
            "--sort",
            "span",
        ],
    )

    pos_a = out.index("conv-a  messages=2  chars=20  span=50")
    pos_c = out.index("conv-c  messages=4  chars=8  span=20")
    pos_d = out.index("conv-d  messages=3  chars=12  span=10")
    pos_b = out.index("conv-b  messages=3  chars=15  span=N/A")
    assert pos_a < pos_c < pos_d < pos_b


def test_analyze_stats_sort_conversation_id_text(tmp_path, monkeypatch, capsys):
    root = tmp_path / "parsed"
    _build_threads_fixture(root)

    out = _run_cli(
        monkeypatch,
        capsys,
        [
            "llm-logparser",
            "analyze",
            "stats",
            "--input",
            str(root),
            "--per-thread",
            "--sort",
            "conversation_id",
        ],
    )

    pos_a = out.index("conv-a  messages=2  chars=20  span=50")
    pos_b = out.index("conv-b  messages=3  chars=15  span=N/A")
    pos_c = out.index("conv-c  messages=4  chars=8  span=20")
    pos_d = out.index("conv-d  messages=3  chars=12  span=10")
    assert pos_a < pos_b < pos_c < pos_d


def test_analyze_stats_role_breakdown_text(tmp_path, monkeypatch, capsys):
    parsed = tmp_path / "thread-conv-1" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-1",
        [
            {"message_id": "m1", "role": "user", "text": "hello"},
            {"message_id": "m2", "role": "system", "text": "world"},
            {"message_id": "m3", "text": "abc"},
        ],
    )

    out = _run_cli(
        monkeypatch,
        capsys,
        [
            "llm-logparser",
            "analyze",
            "stats",
            "--input",
            str(parsed),
            "--include-role-breakdown",
        ],
    )

    assert "Other role breakdown:" in out
    assert "  system: 1" in out
    assert "  unknown: 1" in out


def test_analyze_stats_json_includes_enriched_threads_and_role_breakdown(
    tmp_path, monkeypatch, capsys
):
    root = tmp_path / "parsed"
    _build_threads_fixture(root)

    out = _run_cli(
        monkeypatch,
        capsys,
        [
            "llm-logparser",
            "analyze",
            "stats",
            "--input",
            str(root),
            "--json",
            "--sort",
            "conversation_id",
        ],
    )

    payload = json.loads(out)
    assert payload["threads"] == 4
    assert payload["messages"] == 12
    assert payload["user_messages"] == 4
    assert payload["assistant_messages"] == 4
    assert payload["other_roles"] == 4
    assert payload["other_role_breakdown"] == {
        "system": 2,
        "tool": 1,
        "unknown": 1,
    }
    assert [detail["conversation_id"] for detail in payload["threads_detail"]] == [
        "conv-a",
        "conv-b",
        "conv-c",
        "conv-d",
    ]

    conv_b = payload["threads_detail"][1]
    assert conv_b["message_count"] == 3
    assert conv_b["character_count"] == 15
    assert conv_b["first_timestamp"] is None
    assert conv_b["last_timestamp"] is None
    assert conv_b["conversation_span_seconds"] is None
    assert conv_b["user_messages"] == 1
    assert conv_b["assistant_messages"] == 1
    assert conv_b["other_roles"] == 1
    assert conv_b["characters_user"] == 5
    assert conv_b["characters_assistant"] == 5


def test_analyze_stats_json_top_limits_threads_detail_not_global_aggregates(
    tmp_path, monkeypatch, capsys
):
    root = tmp_path / "parsed"
    _build_threads_fixture(root)

    out = _run_cli(
        monkeypatch,
        capsys,
        [
            "llm-logparser",
            "analyze",
            "stats",
            "--input",
            str(root),
            "--json",
            "--sort",
            "chars",
            "--top",
            "2",
        ],
    )

    payload = json.loads(out)
    assert payload["threads"] == 4
    assert payload["messages"] == 12
    assert payload["characters_total"] == 55
    assert [detail["conversation_id"] for detail in payload["threads_detail"]] == [
        "conv-a",
        "conv-b",
    ]


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

    _run_cli(
        monkeypatch,
        capsys,
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

    written = out_path.read_text(encoding="utf-8")
    assert written.endswith("\n")
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

    _run_cli(
        monkeypatch,
        capsys,
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

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["threads"] == 1
    assert payload["messages"] == 2
    assert payload["other_role_breakdown"] == {}
    assert payload["threads_detail"][0]["conversation_id"] == "conv-1"
    assert payload["threads_detail"][0]["first_timestamp"] == "2024-01-01T00:00:00Z"
    assert payload["threads_detail"][0]["last_timestamp"] == "2024-01-01T00:00:30Z"


def test_analyze_stats_json_output_is_deterministic(tmp_path, monkeypatch, capsys):
    parsed = tmp_path / "thread-conv-deterministic" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-deterministic",
        [
            {"message_id": "m1", "role": "user", "ts": 1704067200000, "text": "hello"},
            {"message_id": "m2", "role": "assistant", "ts": 1704067260000, "text": "world"},
            {"message_id": "m3", "role": "tool", "ts": 1704067290000, "text": ""},
        ],
    )

    argv = [
        "llm-logparser",
        "analyze",
        "stats",
        "--input",
        str(parsed),
        "--json",
        "--sort",
        "conversation_id",
    ]
    first = json.loads(_run_cli(monkeypatch, capsys, argv))
    second = json.loads(_run_cli(monkeypatch, capsys, argv))

    assert first == second

    keys = set(_iter_keys(first))
    assert "generated_at" not in keys
    assert "exported_at" not in keys
    assert "random" not in keys
    assert "seed" not in keys
