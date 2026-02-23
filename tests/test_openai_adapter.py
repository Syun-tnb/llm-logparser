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
                        "content_type": "text",
                        "parts": ["hello", "world"],
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

    assert msg["content"]["content_type"] == "text"
    assert msg["content"]["parts"] == ["hello", "world"]
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
