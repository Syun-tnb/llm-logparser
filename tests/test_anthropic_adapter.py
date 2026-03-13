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

    assert len(messages) == 2

    user_msg, assistant_msg = messages
    assert user_msg["conversation_id"] == shorten_id(raw["uuid"])
    assert user_msg["conv_id"] == shorten_id(raw["uuid"])
    assert user_msg["message_id"] == shorten_id(raw["chat_messages"][0]["uuid"])
    assert user_msg["id"] == shorten_id(raw["chat_messages"][0]["uuid"])
    assert user_msg["role"] == "user"
    assert user_msg["parent_id"] is None
    assert user_msg["created_at"] == raw["chat_messages"][0]["created_at"]
    assert user_msg["content"]["parts"] == ["Claudeは会話ログを出力する方法ある？"]
    assert user_msg["text"] == "Claudeは会話ログを出力する方法ある？"

    assert assistant_msg["message_id"] == shorten_id(raw["chat_messages"][1]["uuid"])
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["thread_title"] == raw["name"]
    assert assistant_msg["content"]["content_type"] == "text"
    assert assistant_msg["content"]["parts"] == [
        "はい、Claude.aiのウェブインターフェースでは会話ログを出力する方法があります。"
    ]
    assert assistant_msg["text"] == "はい、Claude.aiのウェブインターフェースでは会話ログを出力する方法があります。"
    assert "<system>You only have 2 searches left this turn</system>" not in assistant_msg["text"]


def test_anthropic_adapter_skips_messages_without_text_blocks():
    raw = _load_fixture()

    messages = list(anthropic_adapter(raw))
    message_ids = {msg["message_id"] for msg in messages}

    skipped_message_id = shorten_id(raw["chat_messages"][2]["uuid"])
    assert skipped_message_id not in message_ids


def test_parse_to_jsonl_supports_anthropic_provider(tmp_path):
    fixture = Path("tests/fixtures/claude_sample.json")

    stats = parse_to_jsonl("anthropic", fixture, tmp_path, dry_run=False, fail_fast=True)

    assert stats["threads"] == 1
    assert stats["messages"] == 2

    conv_id = shorten_id("16b9da4c-4b08-4bf4-85a8-911b04434241")
    parsed_path = tmp_path / "anthropic" / f"thread-{conv_id}" / "parsed.jsonl"
    assert parsed_path.exists()
