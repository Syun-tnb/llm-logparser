from llm_logparser.core.providers.openai.adapter import adapter as openai_adapter


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
    assert msg["conversation_id"] == "conv-1"
    assert msg["message_id"] == "m1"
    assert msg["parent_id"] == "root"
    assert msg["role"] == "user"

    # 秒→ms
    assert msg["ts"] == 1730000001_000

    assert msg["content"]["content_type"] == "text"
    assert msg["content"]["parts"] == ["hello", "world"]
    assert msg["text"] == "hello\nworld"
