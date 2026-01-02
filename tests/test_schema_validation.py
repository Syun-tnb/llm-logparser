from __future__ import annotations

import pytest

from llm_logparser.core.schema_validation import MessageSchemaValidator, MessageValidationError


GOOD_MESSAGE = {
    "record_type": "message",
    "provider_id": "openai",
    "conversation_id": "abc123",
    "message_id": "m1",
    "role": "user",
    "ts": 1_700_000_000_000,
    "content": {"content_type": "text", "parts": ["Hello!"]},
    "text": "Hello!",
}


def test_message_validator_accepts_valid_message():
    validator = MessageSchemaValidator()
    validator.validate_message(GOOD_MESSAGE)


def test_message_validator_rejects_missing_required_field():
    validator = MessageSchemaValidator()
    invalid = dict(GOOD_MESSAGE)
    invalid.pop("role")
    with pytest.raises(MessageValidationError):
        validator.validate_message(invalid)
