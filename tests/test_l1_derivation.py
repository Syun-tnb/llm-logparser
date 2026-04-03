import json
from pathlib import Path

import pytest

from llm_logparser.core.l1_derivation import (
    UNKNOWN_ROLE,
    derive_thread_metrics,
    derive_thread_metrics_from_rows,
    discover_parsed_jsonl,
    iter_input_message_records,
    message_character_count,
    message_role,
    message_text,
    normalize_role_value,
    resolve_message_text,
    to_iso_utc,
    ts_to_seconds,
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


def test_message_helpers_handle_partial_rows_without_exceptions():
    assert message_text({}) == ""
    assert message_text({"text": None}) == ""
    assert message_text({"text": "hello"}) == "hello"
    assert message_text({"content": {"parts": ["alpha", "beta"]}}) == "alpha\nbeta"
    assert message_character_count({}) == 0
    assert message_character_count({"text": None}) == 0
    assert message_character_count({"text": "hello"}) == 5
    assert message_role({}) is None
    assert message_role({"role": ""}) is None
    assert message_role({"role": "User"}) == "User"
    assert normalize_role_value("User") == "user"
    assert normalize_role_value(" assistant ") == "assistant"
    assert normalize_role_value("moderator") == UNKNOWN_ROLE
    assert normalize_role_value("") == UNKNOWN_ROLE
    assert normalize_role_value(None) == UNKNOWN_ROLE


def test_resolve_message_text_prefers_text_then_legacy_content_parts():
    assert resolve_message_text({"text": "hello", "content": {"parts": ["ignored"]}}) == (
        "hello",
        "text",
    )
    assert resolve_message_text({"content": {"parts": ["alpha", "beta"]}}) == (
        "alpha\nbeta",
        "content.parts",
    )
    assert resolve_message_text({"content": ["provider-native"]}) == ("", "empty")


def test_message_role_preserves_raw_role_while_normalization_is_canonical():
    row = {"role": "User"}

    assert message_role(row) == "User"
    assert normalize_role_value(row["role"]) == "user"


def test_ts_conversion_utilities_handle_seconds_milliseconds_and_invalid_values():
    assert ts_to_seconds(1_704_067_200_000) == 1_704_067_200.0
    assert ts_to_seconds(1_704_067_200) == 1_704_067_200.0
    assert ts_to_seconds("1704067200") is None
    assert to_iso_utc(1_704_067_200.0) == "2024-01-01T00:00:00Z"
    assert to_iso_utc(None) is None


def test_derive_thread_metrics_from_rows_uses_normalized_roles_for_counters_and_breakdown():
    metrics = derive_thread_metrics_from_rows(
        [
            {
                "record_type": "thread",
                "conversation_id": "conv-mixed",
            },
            {
                "record_type": "message",
                "conversation_id": "conv-mixed",
                "role": " USER ",
                "text": "hello",
                "ts": 1704067200000,
            },
            {
                "record_type": "message",
                "conversation_id": "conv-mixed",
                "role": "moderator",
                "text": None,
            },
            {
                "record_type": "message",
                "conversation_id": "conv-mixed",
                "role": "Assistant",
                "text": "ok",
                "ts": 1704067210000,
            },
            {
                "record_type": "message",
                "conversation_id": "conv-mixed",
                "role": "model",
                "text": "",
            },
            {
                "record_type": "message",
                "conversation_id": "conv-mixed",
                "role": "tool",
                "text": "tool output",
            },
        ]
    )

    assert metrics.conversation_id == "conv-mixed"
    assert metrics.message_count == 5
    assert metrics.user_messages == 1
    assert metrics.assistant_messages == 1
    assert metrics.other_roles == 3
    assert metrics.character_count == 18
    assert metrics.other_role_breakdown == {"tool": 1, "unknown": 2}
    assert metrics.first_ts == 1704067200.0
    assert metrics.last_ts == 1704067210.0
