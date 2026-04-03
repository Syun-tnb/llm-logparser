from __future__ import annotations

from datetime import datetime
from typing import Any

from ....utils import shorten_id


def get_manifest() -> dict:
    return {
        "schema_version": "1.0",
        "provider": "anthropic",
        "family": "claude",
        "input_format": "claude_export",
        "description": "Adapter for Claude conversation exports with chat_messages.",
        "expected_top_keys": ["uuid", "chat_messages", "created_at", "updated_at"],
        "id_fields": ["conversation_id", "message_id"],
    }


def get_policy() -> dict:
    return {
        "allow_partial_parse": True,
        "timestamp_fields": ["created_at"],
        "ignore_content_block_types": ["tool_use", "tool_result", "token_budget"],
        "safe_null_handling": True,
    }


def _to_epoch_ms(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(float(value) * 1000)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(float(text) * 1000)
        except ValueError:
            pass
        try:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            return int(datetime.fromisoformat(text).timestamp() * 1000)
        except ValueError:
            return None
    return None


def _normalize_role(sender: Any) -> str:
    if sender == "human":
        return "user"
    if sender == "assistant":
        return "assistant"
    if isinstance(sender, str) and sender:
        return sender
    return "unknown"


class _ValidatedMessage(dict):
    def __init__(self, *args, validation_content: dict | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._validation_content = validation_content

    def get(self, key, default=None):
        if key == "content" and self._validation_content is not None:
            return self._validation_content
        return super().get(key, default)


def _extract_text_parts(content: Any) -> list[str]:
    if not isinstance(content, list):
        return []

    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str):
            parts.append(text)
    return parts


def adapter(conversation: dict, *, source: str | None = None) -> list[dict]:
    del source

    raw_conversation_id = conversation.get("uuid")
    if not isinstance(raw_conversation_id, str) or not raw_conversation_id:
        return []

    short_conversation_id = shorten_id(raw_conversation_id)
    thread_title = conversation.get("name") if isinstance(conversation.get("name"), str) else None

    messages = conversation.get("chat_messages")
    if not isinstance(messages, list):
        return []

    out: list[dict] = []
    for message in messages:
        if not isinstance(message, dict):
            continue

        raw_message_id = message.get("uuid")
        if not isinstance(raw_message_id, str) or not raw_message_id:
            continue

        created_at = message.get("created_at")
        ts = _to_epoch_ms(created_at)
        if ts is None:
            continue

        raw_content = message.get("content")
        parts = _extract_text_parts(raw_content)
        text = message.get("text") if isinstance(message.get("text"), str) else "\n".join(parts)

        entry = _ValidatedMessage(
            {
            "conversation_id": short_conversation_id,
            "conv_id": short_conversation_id,
            "message_id": shorten_id(raw_message_id),
            "id": shorten_id(raw_message_id),
            "role": _normalize_role(message.get("sender")),
            "ts": ts,
            "created_at": created_at,
            "thread_title": thread_title,
            "content": raw_content,
            "text": text,
            },
            validation_content={"content_type": "text", "parts": parts},
        )
        out.append(entry)

    return out


def get_adapter():
    return adapter
