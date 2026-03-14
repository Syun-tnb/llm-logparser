import json
from pathlib import Path

import pytest

from llm_logparser.core.l1_derivation import (
    derive_thread_metrics,
    discover_parsed_jsonl,
    iter_input_message_records,
)


def _write_parsed_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def test_discover_parsed_jsonl_accepts_single_file(tmp_path):
    parsed = tmp_path / "thread-conv-1" / "parsed.jsonl"
    _write_parsed_jsonl(parsed, [])

    assert discover_parsed_jsonl(parsed) == [parsed]


def test_iter_input_message_records_filters_non_message_rows(tmp_path):
    root = tmp_path / "parsed"
    _write_parsed_jsonl(
        root / "b" / "thread-conv-b" / "parsed.jsonl",
        [
            {
                "record_type": "thread",
                "provider_id": "openai",
                "conversation_id": "conv-b",
                "message_count": 1,
            },
            {
                "record_type": "message",
                "provider_id": "openai",
                "conversation_id": "conv-b",
                "message_id": "m1",
                "role": "assistant",
                "ts": 1704067200000,
                "text": "hello",
            },
        ],
    )
    _write_parsed_jsonl(
        root / "a" / "thread-conv-a" / "parsed.jsonl",
        [
            {
                "record_type": "thread",
                "provider_id": "openai",
                "conversation_id": "conv-a",
                "message_count": 1,
            },
            {
                "record_type": "message",
                "provider_id": "openai",
                "conversation_id": "conv-a",
                "message_id": "m1",
                "role": "user",
                "ts": 1704067201000,
                "text": "hi",
            },
        ],
    )

    rows = list(iter_input_message_records(root))

    assert [row["conversation_id"] for row in rows] == ["conv-a", "conv-b"]
    assert all(row["record_type"] == "message" for row in rows)


def test_derive_thread_metrics_returns_zero_message_thread_detail(tmp_path):
    parsed = tmp_path / "thread-conv-1" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        [
            {
                "record_type": "thread",
                "provider_id": "openai",
                "conversation_id": "conv-1",
                "message_count": 0,
            }
        ],
    )

    metrics = derive_thread_metrics(parsed)

    assert metrics.to_detail() == {
        "conversation_id": "conv-1",
        "message_count": 0,
        "character_count": 0,
        "first_timestamp": None,
        "last_timestamp": None,
        "conversation_span_seconds": None,
        "user_messages": 0,
        "assistant_messages": 0,
        "other_roles": 0,
        "characters_user": 0,
        "characters_assistant": 0,
    }


def test_derive_thread_metrics_invalid_json_includes_path_and_line(tmp_path):
    parsed = tmp_path / "thread-conv-1" / "parsed.jsonl"
    parsed.parent.mkdir(parents=True, exist_ok=True)
    parsed.write_text("{bad json}\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"invalid JSON .*parsed\.jsonl:1"):
        derive_thread_metrics(parsed)
