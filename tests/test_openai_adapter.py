import json
from pathlib import Path

from llm_logparser.core.parser import parse_to_jsonl
from llm_logparser.core.providers.openai.adapter import adapter as openai_adapter
from llm_logparser.core.utils import shorten_id


def test_openai_adapter_basic():
    # OpenAI 生ログっぽいミニマム fixture
    raw = {
        "conversation_id": "conv-1",
        "title": "Test conversation",
        "create_time": 1730000000.0,
        "mapping": {
            "root": {
                "id": "root",
                "parent": None,
                "children": ["m1"],
                "message": None,
            },
            "m1": {
                "id": "m1",
                "parent": "root",
                "children": ["m2"],
                "message": {
                    "id": "m1",
                    "author": {"role": "user"},
                    "content": {
                        "content_type": "multimodal_text",
                        "parts": ["hello", {"text": "world", "kind": "delta"}],
                    },
                    "create_time": 1730000001.0,
                },
            },
        },
    }

    messages = list(openai_adapter(raw))
    assert len(messages) == 1

    msg = messages[0]
    assert msg["conversation_id"] == shorten_id("conv-1")
    assert msg["conv_id"] == shorten_id("conv-1")
    assert msg["message_id"] == shorten_id("m1")
    assert msg["id"] == shorten_id("m1")
    assert msg["parent_id"] == shorten_id("root")
    assert msg["role"] == "user"
    assert msg["thread_title"] == "Test conversation"
    assert msg["created_at"] == 1730000001.0

    # 秒→ms
    assert msg["ts"] == 1730000001_000

    assert type(msg) is dict
    assert msg["content"] == raw["mapping"]["m1"]["message"]["content"]
    assert msg["text"] == "hello\nworld"


def test_openai_adapter_adds_finish_reason_and_root_created_at_fallback():
    raw = {
        "conversation_id": "123e4567-e89b-12d3-a456-426614174000",
        "title": "Research thread",
        "create_time": 1731000000.5,
        "mapping": {
            "root": {
                "id": "root",
                "parent": None,
                "children": ["assistant-1"],
                "message": None,
            },
            "assistant-1": {
                "id": "assistant-1",
                "parent": "root",
                "children": [],
                "message": {
                    "id": "assistant-1",
                    "author": {"role": "assistant"},
                    "content": {"content_type": "text", "parts": ["answer"]},
                    "create_time": None,
                    "metadata": {"finish_details": {"type": "stop"}},
                },
            },
        },
    }

    messages = list(openai_adapter(raw))
    assert len(messages) == 1

    msg = messages[0]
    assert msg["conversation_id"] == shorten_id(raw["conversation_id"])
    assert len(msg["conversation_id"]) == 12
    assert len(msg["message_id"]) == 12
    assert len(msg["id"]) == 12
    assert len(msg["parent_id"]) == 12
    assert msg["thread_title"] == "Research thread"
    assert msg["created_at"] == 1731000000.5
    assert msg["ts"] == 1731000000_500
    assert msg["finish_reason"] == "stop"


def test_openai_adapter_content_matches_serialized_output_with_schema_validation(tmp_path):
    raw = {
        "conversation_id": "conv-1",
        "title": "Test conversation",
        "create_time": 1730000000.0,
        "mapping": {
            "root": {"id": "root", "parent": None, "children": ["m1"], "message": None},
            "m1": {
                "id": "m1",
                "parent": "root",
                "children": [],
                "message": {
                    "id": "m1",
                    "author": {"role": "assistant"},
                    "content": {
                        "content_type": "multimodal_text",
                        "parts": ["hello", {"text": "world", "kind": "delta"}],
                    },
                    "create_time": 1730000001.0,
                },
            },
        },
    }

    adapted = openai_adapter(raw)
    input_path = tmp_path / "openai.json"
    input_path.write_text(json.dumps([raw], ensure_ascii=False), encoding="utf-8")

    stats = parse_to_jsonl("openai", input_path, tmp_path / "artifacts", dry_run=False, fail_fast=True, validate_schema=True)

    assert stats["messages"] == 1
    conv_id = adapted[0]["conversation_id"]
    parsed_path = tmp_path / "artifacts" / "openai" / f"thread-{conv_id}" / "parsed.jsonl"
    rows = [json.loads(line) for line in parsed_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    message = [row for row in rows if row.get("record_type") == "message"][0]
    assert message["content"] == adapted[0]["content"]
    assert message["text"] == adapted[0]["text"]


def test_openai_text_ignores_non_text_parts_but_keeps_order():
    raw = {
        "conversation_id": "conv-1",
        "title": "Test conversation",
        "create_time": 1730000000.0,
        "mapping": {
            "root": {"id": "root", "parent": None, "children": ["m1"], "message": None},
            "m1": {
                "id": "m1",
                "parent": "root",
                "children": [],
                "message": {
                    "id": "m1",
                    "author": {"role": "assistant"},
                    "content": {
                        "content_type": "multimodal_text",
                        "parts": [
                            "alpha",
                            {"image_url": "https://example.test/a.png"},
                            {"text": "beta"},
                        ],
                    },
                    "create_time": 1730000001.0,
                },
            },
        },
    }

    changed = json.loads(json.dumps(raw))
    changed["mapping"]["m1"]["message"]["content"]["parts"][1]["image_url"] = "https://example.test/b.png"

    assert openai_adapter(raw)[0]["text"] == "alpha\nbeta"
    assert openai_adapter(changed)[0]["text"] == "alpha\nbeta"
