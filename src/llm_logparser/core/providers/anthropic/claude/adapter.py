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


def _extract_text_parts(content: Any) -> list[str]:
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            parts.extend(_extract_text_parts(block))
        return parts

    if not isinstance(content, dict):
        return []

    parts: list[str] = []
    if content.get("type") == "text" and isinstance(content.get("text"), str):
        parts.append(content["text"])
    message_text = content.get("message")
    if isinstance(message_text, str):
        parts.append(message_text)
    nested = content.get("content")
    if nested is not None:
        parts.extend(_extract_text_parts(nested))
    return parts


def _extract_message_text(message: dict[str, Any]) -> str:
    """
    Prefer Claude's top-level raw text surface when present.
    Fallback to explicit text-bearing content blocks only.
    """
    raw_text = message.get("text")
    if isinstance(raw_text, str):
        return raw_text
    return "\n".join(_extract_text_parts(message.get("content")))


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
        text = _extract_message_text(message)

        entry = {
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
        }
        out.append(entry)

    return out


def get_adapter():
    return adapter
