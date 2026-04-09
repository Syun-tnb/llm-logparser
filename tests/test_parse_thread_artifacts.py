import json
from pathlib import Path

from llm_logparser.core import parser as parser_module
from llm_logparser.core.l1_derivation import (
    build_thread_stats_artifact,
    derive_thread_metrics,
)
from llm_logparser.core.parser import parse_to_jsonl
from llm_logparser.core.utils import shorten_id


def test_parse_writes_thread_stats_artifact_next_to_parsed_jsonl(tmp_path):
    fixture = Path("tests/fixtures/openai_sample.json")

    stats = parse_to_jsonl(
        "openai",
        fixture,
        tmp_path,
        dry_run=False,
        fail_fast=True,
    )

    assert stats["threads"] == 1

    raw_conv_id = "68b3eea1-1fc4-832c-878a-23896288675a"
    conv_id = shorten_id(raw_conv_id)
    thread_dir = tmp_path / "openai" / f"thread-{conv_id}"
    parsed_path = thread_dir / "parsed.jsonl"
    thread_stats_path = thread_dir / "thread_stats.json"

    assert parsed_path.exists()
    assert thread_stats_path.exists()

    payload = json.loads(thread_stats_path.read_text(encoding="utf-8"))
    expected = build_thread_stats_artifact(
        derive_thread_metrics(parsed_path),
        provider_id="openai",
    )

    assert payload == expected
    assert payload["artifact_type"] == "thread_stats"
    assert payload["schema_version"] == "1.0"
    assert payload["conversation_id"] == conv_id


def test_parse_dry_run_does_not_write_thread_stats_artifact(tmp_path):
    fixture = Path("tests/fixtures/openai_sample.json")
    raw_conv_id = "68b3eea1-1fc4-832c-878a-23896288675a"
    conv_id = shorten_id(raw_conv_id)

    stats = parse_to_jsonl(
        "openai",
        fixture,
        tmp_path,
        dry_run=True,
        fail_fast=True,
    )

    assert stats["threads"] == 1
    thread_dir = tmp_path / "openai" / f"thread-{conv_id}"
    assert not (thread_dir / "parsed.jsonl").exists()
    assert not (thread_dir / "thread_stats.json").exists()


def _adapter_without_record_type(_raw, *, source=None, logger=None):
    del source, logger
    return [
        {
            "conversation_id": "conv-validate-schema",
            "message_id": "m1",
            "parent_id": "root",
            "role": "user",
            "ts": 1730000001000,
            "content": {"content_type": "text", "parts": ["hello"]},
            "text": "hello",
        }
    ]


def test_parse_schema_validation_runs_on_canonical_row(monkeypatch, tmp_path):
    monkeypatch.setattr(
        parser_module,
        "load_adapter",
        lambda provider: (_adapter_without_record_type, {}, {}),
    )
    input_path = tmp_path / "input.json"
    input_path.write_text('{"source":"fixture"}\n', encoding="utf-8")

    stats = parser_module.parse_to_jsonl(
        "fake",
        input_path,
        tmp_path,
        dry_run=False,
        fail_fast=True,
        validate_schema=True,
    )

    assert stats["threads"] == 1
    assert stats["messages"] == 1

    thread_dir = tmp_path / "fake" / "thread-conv-validate-schema"
    parsed_path = thread_dir / "parsed.jsonl"
    thread_stats_path = thread_dir / "thread_stats.json"
    windows_path = thread_dir / "message_windows.jsonl"

    parsed_rows = [
        json.loads(line)
        for line in parsed_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(parsed_rows) == 2
    assert parsed_rows[1]["record_type"] == "message"
    assert parsed_rows[1]["provider_id"] == "fake"

    thread_stats = json.loads(thread_stats_path.read_text(encoding="utf-8"))
    assert thread_stats["message_count"] == 1

    window_rows = [
        json.loads(line)
        for line in windows_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(window_rows) == 1
