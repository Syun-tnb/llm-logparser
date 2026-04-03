# tests/test_parser_validation.py

from llm_logparser.core.parser import validate_message


def test_validate_message_accepts_provider_native_content():
    msg = {
        "conversation_id": "conv-1",
        "message_id": "m1",
        "parent_id": None,
        "role": "assistant",
        "ts": 1730000001000,
        "content": [{"type": "text", "text": "hello"}],
        "text": "hello",
    }

    assert validate_message(msg) is True


def test_validate_message_rejects_missing_role():
    msg = {
        "conversation_id": "conv-1",
        "message_id": "m1",
        "parent_id": None,
        "ts": 1730000001000,
        "content": {"raw": "hello"},
        "text": "hello",
    }

    assert validate_message(msg) is False


def test_validate_message_rejects_non_json_compatible_content():
    msg = {
        "conversation_id": "conv-1",
        "message_id": "m1",
        "parent_id": None,
        "role": "assistant",
        "ts": 1730000001000,
        "content": object(),
        "text": "hello",
    }

    assert validate_message(msg) is False
