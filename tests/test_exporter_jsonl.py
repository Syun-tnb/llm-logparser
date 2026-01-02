import json
from pathlib import Path

from llm_logparser.core.parser import parse_to_jsonl


def test_parsed_jsonl_structure(tmp_path):
    fixture = Path("tests/fixtures/openai_sample.json")
    stats = parse_to_jsonl(
        "openai",
        fixture,
        tmp_path,
        dry_run=False,
        fail_fast=True,
    )
    assert stats["threads"] == 1

    conv_id = "68b3eea1-1fc4-832c-878a-23896288675a"
    parsed = tmp_path / "openai" / f"thread-{conv_id}" / "parsed.jsonl"
    lines = parsed.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 2

    thread = json.loads(lines[0])
    assert thread["record_type"] == "thread"
    assert thread["conversation_id"] == conv_id

    message = json.loads(lines[1])
    assert message["record_type"] == "message"
    assert message["conversation_id"] == conv_id
