import json
from pathlib import Path

from llm_logparser.core import parser as parser_module
from llm_logparser.core.message_windows import (
    build_message_window_artifact,
    iter_message_windows_from_rows,
    resolve_message_window_stride,
)
from llm_logparser.core.schema_validation import load_message_windows_validator


def _canonical_message(
    conversation_id: str,
    message_id: str,
    role: str | None,
    ts: int,
    text: str,
    *,
    provider_id: str = "fake",
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


def _fake_adapter(_raw):
    return [
        {
            "conversation_id": "conv-1",
            "message_id": "m3",
            "role": "assistant",
            "ts": 1704067203000,
            "text": "third",
            "content": {"content_type": "text", "parts": ["third"]},
        },
        {
            "conversation_id": "conv-1",
            "message_id": "m1",
            "role": "user",
            "ts": 1704067201000,
            "text": "first",
            "content": {"content_type": "text", "parts": ["first"]},
        },
        {
            "conversation_id": "conv-1",
            "message_id": "m4",
            "role": "tool",
            "ts": 1704067204000,
            "text": "tool-output",
            "content": {"content_type": "text", "parts": ["tool-output"]},
        },
        {
            "conversation_id": "conv-1",
            "message_id": "m2",
            "role": "system",
            "ts": 1704067202000,
            "text": "second",
            "content": {"content_type": "text", "parts": ["second"]},
        },
        {
            "conversation_id": "conv-1",
            "message_id": "m5",
            "role": "assistant",
            "ts": 1704067205000,
            "text": "fifth",
            "content": {"content_type": "text", "parts": ["fifth"]},
        },
    ]


def test_iter_message_windows_from_rows_is_deterministic_and_role_aware():
    rows = [
        _canonical_message("conv-1", "m1", "user", 1704067201000, "first"),
        _canonical_message("conv-1", "m2", "system", 1704067202000, "second"),
        _canonical_message("conv-1", "m3", "assistant", 1704067203000, "third"),
        _canonical_message("conv-1", "m4", "tool", 1704067204000, "tool-output"),
        _canonical_message("conv-1", "m5", None, 1704067205000, "fifth"),
    ]

    first = list(iter_message_windows_from_rows(rows, window_size=3))
    second = list(iter_message_windows_from_rows(rows, window_size=3))

    assert first == second
    assert first == [
        build_message_window_artifact(
            rows[:3],
            window_index=1,
            window_size=3,
            window_stride=3,
        ),
        build_message_window_artifact(
            rows[3:],
            window_index=2,
            window_size=3,
            window_stride=3,
        ),
    ]
    assert first[0]["message_ids"] == ["m1", "m2", "m3"]
    assert first[0]["roles"] == ["user", "system", "assistant"]
    assert first[1]["message_ids"] == ["m4", "m5"]
    assert first[1]["roles"] == ["tool", "unknown"]
    assert first[0]["window_size"] == 3
    assert first[0]["window_stride"] == 3
    assert "system: second" in first[0]["text"]
    assert "tool: tool-output" in first[1]["text"]
    assert "unknown: fifth" in first[1]["text"]


def test_iter_message_windows_from_rows_supports_sliding_stride():
    rows = [
        _canonical_message("conv-1", "m1", "user", 1, "one"),
        _canonical_message("conv-1", "m2", "assistant", 2, "two"),
        _canonical_message("conv-1", "m3", "user", 3, "three"),
        _canonical_message("conv-1", "m4", "assistant", 4, "four"),
        _canonical_message("conv-1", "m5", "user", 5, "five"),
    ]

    windows = list(
        iter_message_windows_from_rows(
            rows,
            window_size=3,
            window_stride=2,
        )
    )

    assert [window["window_id"] for window in windows] == [
        "window-0001",
        "window-0002",
        "window-0003",
    ]
    assert [window["message_ids"] for window in windows] == [
        ["m1", "m2", "m3"],
        ["m3", "m4", "m5"],
        ["m5"],
    ]
    assert [window["roles"] for window in windows] == [
        ["user", "assistant", "user"],
        ["user", "assistant", "user"],
        ["user"],
    ]
    assert all(window["window_size"] == 3 for window in windows)
    assert all(window["window_stride"] == 2 for window in windows)


def test_iter_message_windows_counts_change_with_size_and_stride():
    rows = [
        _canonical_message("conv-1", f"m{index}", "user", index, f"text-{index}")
        for index in range(1, 7)
    ]

    non_overlapping = list(
        iter_message_windows_from_rows(rows, window_size=4, window_stride=4)
    )
    sliding = list(iter_message_windows_from_rows(rows, window_size=4, window_stride=2))
    fine_stride = list(iter_message_windows_from_rows(rows, window_size=4, window_stride=1))

    assert len(non_overlapping) == 2
    assert len(sliding) == 3
    assert len(fine_stride) == 6


def test_resolve_message_window_stride_defaults_to_window_size():
    assert resolve_message_window_stride(window_size=4, window_stride=None) == 4
    assert resolve_message_window_stride(window_size=4, window_stride=2) == 2


def test_message_window_artifact_matches_schema():
    rows = [
        _canonical_message("conv-1", "m1", "user", 1704067201000, "first"),
        _canonical_message("conv-1", "m2", "assistant", 1704067202000, "second"),
    ]

    artifact = build_message_window_artifact(
        rows,
        window_index=1,
        window_size=2,
        window_stride=2,
    )
    validator = load_message_windows_validator()

    assert list(validator.iter_errors(artifact)) == []
    assert artifact["record_type"] == "message_window"
    assert artifact["schema_version"] == "1.0"


def test_message_window_schema_rejects_malformed_row():
    rows = [
        _canonical_message("conv-1", "m1", "user", 1704067201000, "first"),
        _canonical_message("conv-1", "m2", "assistant", 1704067202000, "second"),
    ]

    artifact = build_message_window_artifact(
        rows,
        window_index=1,
        window_size=2,
        window_stride=2,
    )
    artifact["message_ids"] = "m1,m2"

    validator = load_message_windows_validator()
    errors = list(validator.iter_errors(artifact))

    assert errors
    assert any(error.validator == "type" for error in errors)


def test_parse_writes_message_windows_jsonl_next_to_parsed_jsonl(monkeypatch, tmp_path):
    monkeypatch.setattr(
        parser_module,
        "load_adapter",
        lambda provider: (_fake_adapter, {}, {}),
    )
    input_path = tmp_path / "input.json"
    input_path.write_text('{"source":"fixture"}\n', encoding="utf-8")

    stats = parser_module.parse_to_jsonl(
        "fake",
        input_path,
        tmp_path,
        dry_run=False,
        fail_fast=True,
    )

    assert stats["threads"] == 1

    thread_dir = tmp_path / "fake" / "thread-conv-1"
    parsed_path = thread_dir / "parsed.jsonl"
    windows_path = thread_dir / "message_windows.jsonl"

    assert parsed_path.exists()
    assert windows_path.exists()

    windows = [
        json.loads(line)
        for line in windows_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert windows[0]["message_ids"] == ["m1", "m2", "m3", "m4"]
    assert windows[0]["roles"] == ["user", "system", "assistant", "tool"]
    assert windows[0]["schema_version"] == "1.0"
    assert windows[0]["window_size"] == 4
    assert windows[0]["window_stride"] == 4
    assert windows[1]["message_ids"] == ["m5"]
    assert windows[1]["roles"] == ["assistant"]
    assert windows[0]["ts_start"] == 1704067201000
    assert windows[0]["ts_end"] == 1704067204000

    canonical_rows = [
        _canonical_message("conv-1", "m1", "user", 1704067201000, "first"),
        _canonical_message("conv-1", "m2", "system", 1704067202000, "second"),
        _canonical_message("conv-1", "m3", "assistant", 1704067203000, "third"),
        _canonical_message("conv-1", "m4", "tool", 1704067204000, "tool-output"),
        _canonical_message("conv-1", "m5", "assistant", 1704067205000, "fifth"),
    ]
    assert windows == list(iter_message_windows_from_rows(canonical_rows))


def test_parse_writes_sliding_message_windows_when_configured(monkeypatch, tmp_path):
    monkeypatch.setattr(
        parser_module,
        "load_adapter",
        lambda provider: (_fake_adapter, {}, {}),
    )
    input_path = tmp_path / "input.json"
    input_path.write_text('{"source":"fixture"}\n', encoding="utf-8")

    parser_module.parse_to_jsonl(
        "fake",
        input_path,
        tmp_path,
        dry_run=False,
        fail_fast=True,
        message_window_size=4,
        message_window_stride=2,
    )

    windows_path = tmp_path / "fake" / "thread-conv-1" / "message_windows.jsonl"
    windows = [
        json.loads(line)
        for line in windows_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert [window["message_ids"] for window in windows] == [
        ["m1", "m2", "m3", "m4"],
        ["m3", "m4", "m5"],
        ["m5"],
    ]
    assert [window["window_id"] for window in windows] == [
        "window-0001",
        "window-0002",
        "window-0003",
    ]


def test_parse_dry_run_does_not_write_message_windows_jsonl(monkeypatch, tmp_path):
    monkeypatch.setattr(
        parser_module,
        "load_adapter",
        lambda provider: (_fake_adapter, {}, {}),
    )
    input_path = tmp_path / "input.json"
    input_path.write_text('{"source":"fixture"}\n', encoding="utf-8")

    stats = parser_module.parse_to_jsonl(
        "fake",
        input_path,
        tmp_path,
        dry_run=True,
        fail_fast=True,
    )

    assert stats["threads"] == 1
    thread_dir = tmp_path / "fake" / "thread-conv-1"
    assert not (thread_dir / "parsed.jsonl").exists()
    assert not (thread_dir / "message_windows.jsonl").exists()
