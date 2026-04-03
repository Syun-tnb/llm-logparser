import json
from pathlib import Path

from llm_logparser.core.parser import parse_to_jsonl
from llm_logparser.core.providers.anthropic.adapter import adapter as anthropic_adapter
from llm_logparser.core.utils import shorten_id


def _load_fixture() -> dict:
    fixture = Path("tests/fixtures/claude_sample.json")
    data = json.loads(fixture.read_text(encoding="utf-8"))
    return data[0]


def test_anthropic_adapter_maps_claude_chat_messages():
    raw = _load_fixture()

    messages = list(anthropic_adapter(raw))

    assert len(messages) == 3

    user_msg, assistant_msg, tool_only_msg = messages
    assert user_msg["conversation_id"] == shorten_id(raw["uuid"])
    assert user_msg["conv_id"] == shorten_id(raw["uuid"])
    assert user_msg["message_id"] == shorten_id(raw["chat_messages"][0]["uuid"])
    assert user_msg["id"] == shorten_id(raw["chat_messages"][0]["uuid"])
    assert user_msg["role"] == "user"
    assert user_msg.get("parent_id") is None
    assert user_msg["created_at"] == raw["chat_messages"][0]["created_at"]
    assert type(user_msg) is dict
    assert user_msg["content"] == raw["chat_messages"][0]["content"]
    assert user_msg["text"] == "Claudeは会話ログを出力する方法ある？"

    assert assistant_msg["message_id"] == shorten_id(raw["chat_messages"][1]["uuid"])
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["thread_title"] == raw["name"]
    assert assistant_msg["content"] == raw["chat_messages"][1]["content"]
    assert assistant_msg["text"] == "はい、Claude.aiのウェブインターフェースでは会話ログを出力する方法があります。"
    assert tool_only_msg["content"] == raw["chat_messages"][2]["content"]
    assert tool_only_msg["text"] == raw["chat_messages"][2]["text"]


def test_anthropic_adapter_keeps_messages_without_text_blocks():
    raw = _load_fixture()

    messages = list(anthropic_adapter(raw))
    message_ids = {msg["message_id"] for msg in messages}

    kept_message_id = shorten_id(raw["chat_messages"][2]["uuid"])
    assert kept_message_id in message_ids


def test_parse_to_jsonl_supports_anthropic_provider(tmp_path):
    fixture = Path("tests/fixtures/claude_sample.json")

    stats = parse_to_jsonl("anthropic", fixture, tmp_path, dry_run=False, fail_fast=True, validate_schema=True)

    assert stats["threads"] == 1
    assert stats["messages"] == 3

    conv_id = shorten_id("16b9da4c-4b08-4bf4-85a8-911b04434241")
    parsed_path = tmp_path / "anthropic" / f"thread-{conv_id}" / "parsed.jsonl"
    assert parsed_path.exists()
    rows = [json.loads(line) for line in parsed_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    messages = [row for row in rows if row.get("record_type") == "message"]
    assert messages[2]["content"] == _load_fixture()["chat_messages"][2]["content"]
    assert messages[2]["text"] == _load_fixture()["chat_messages"][2]["text"]


def test_anthropic_text_prefers_top_level_text_over_block_reconstruction():
    raw = _load_fixture()
    raw["chat_messages"][1]["text"] = "TOP LEVEL"
    raw["chat_messages"][1]["content"][0]["text"] = "BLOCK TEXT"
    raw["chat_messages"][1]["content"][1]["message"] = "TOOL TEXT"

    assert anthropic_adapter(raw)[1]["text"] == "TOP LEVEL"


def test_anthropic_non_text_block_changes_do_not_alter_top_level_text():
    raw = _load_fixture()
    changed = _load_fixture()
    changed["chat_messages"][1]["content"][1]["input"]["query"] = "changed query"

    assert anthropic_adapter(raw)[1]["text"] == anthropic_adapter(changed)[1]["text"]
