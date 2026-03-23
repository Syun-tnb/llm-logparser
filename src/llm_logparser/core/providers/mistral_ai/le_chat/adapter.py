from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from ....utils import shorten_id


REQUIRED_MESSAGE_KEYS = ("id", "chatId", "role", "createdAt")


def get_manifest() -> dict:
    return {
        "schema_version": "1.0",
        "provider": "mistral_ai",
        "family": "le_chat",
        "input_format": "le_chat_export_v1",
        "description": "Adapter for Mistral Le Chat exports with one thread per JSON file.",
        "expected_top_keys": list(REQUIRED_MESSAGE_KEYS) + ["content", "contentChunks"],
        "id_fields": ["conversation_id", "message_id"],
    }


def get_policy() -> dict:
    return {
        "allow_partial_parse": True,
        "timestamp_fields": ["createdAt"],
        "safe_null_handling": True,
        "one_thread_per_file": True,
    }


def _load_json_payload(path: Path, logger: logging.Logger) -> Any | None:
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("skip unreadable JSON file %s: %s", path, exc)
        return None


def _is_candidate_chunk(chunk: Any) -> bool:
    if chunk is None:
        return True
    if not isinstance(chunk, dict):
        return False
    chunk_type = chunk.get("type")
    return (chunk_type is None or chunk_type == "text") and isinstance(chunk.get("text"), str)


def _looks_like_le_chat_message(message: Any) -> bool:
    if not isinstance(message, dict):
        return False

    for key in REQUIRED_MESSAGE_KEYS:
        value = message.get(key)
        if not isinstance(value, str) or not value:
            return False

    if "content" not in message and "contentChunks" not in message:
        return False

    content = message.get("content")
    if content is not None and not isinstance(content, str):
        return False

    chunks = message.get("contentChunks")
    if chunks is None:
        return True
    if not isinstance(chunks, list):
        return False
    return all(_is_candidate_chunk(chunk) for chunk in chunks)


def is_le_chat_export(payload: Any) -> bool:
    if not isinstance(payload, list) or not payload:
        return False

    sample = payload[: min(len(payload), 5)]
    return all(_looks_like_le_chat_message(item) for item in sample)


def iter_input_records(
    input_path: Path,
    logger: logging.Logger,
):
    target = input_path.expanduser()
    candidates = [target]
    if target.is_dir():
        candidates = sorted(path for path in target.rglob("*.json") if path.is_file())

    for candidate in candidates:
        payload = _load_json_payload(candidate, logger)
        if is_le_chat_export(payload):
            yield payload, str(candidate)


def _to_epoch_ms(value: Any) -> int | None:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric > 1_000_000_000_000:
            return int(numeric)
        return int(numeric * 1000)

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            numeric = float(text)
        except ValueError:
            numeric = None
        if numeric is not None:
            if numeric > 1_000_000_000_000:
                return int(numeric)
            return int(numeric * 1000)
        try:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            return int(datetime.fromisoformat(text).timestamp() * 1000)
        except ValueError:
            return None

    return None


def _normalize_role(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return "unknown"

    normalized = value.strip().lower()
    if normalized in {"user", "assistant", "system", "tool"}:
        return normalized
    if normalized == "human":
        return "user"
    if normalized in {"function", "tool_call"} or "tool" in normalized:
        return "tool"
    return normalized


def _extract_parts(content: Any, content_chunks: Any) -> list[str]:
    if isinstance(content_chunks, list):
        parts = []
        for chunk in content_chunks:
            if not isinstance(chunk, dict):
                continue
            chunk_type = chunk.get("type")
            text = chunk.get("text")
            if (chunk_type is None or chunk_type == "text") and isinstance(text, str):
                parts.append(text)
        if parts:
            return parts

    if isinstance(content, str) and content:
        return [content]

    return []


def adapter(messages: Any, *, source: str | None = None) -> list[dict]:
    del source

    if not is_le_chat_export(messages):
        return []

    out: list[dict] = []
    for message in messages:
        raw_conversation_id = message.get("chatId")
        raw_message_id = message.get("id")
        if not isinstance(raw_conversation_id, str) or not raw_conversation_id:
            continue
        if not isinstance(raw_message_id, str) or not raw_message_id:
            continue

        created_at = message.get("createdAt")
        ts = _to_epoch_ms(created_at)
        if ts is None:
            continue

        content = message.get("content") if isinstance(message.get("content"), str) else ""
        parts = _extract_parts(content, message.get("contentChunks"))
        short_conversation_id = shorten_id(raw_conversation_id)
        short_message_id = shorten_id(raw_message_id)

        meta = {
            "service": "le_chat",
            "raw_conversation_id": raw_conversation_id,
            "raw_message_id": raw_message_id,
            "version": message.get("version"),
            "reaction": message.get("reaction"),
            "reactionDetail": message.get("reactionDetail"),
            "reactionComment": message.get("reactionComment"),
            "preference": message.get("preference"),
            "preferenceOver": message.get("preferenceOver"),
            "context": message.get("context"),
            "canvas": message.get("canvas"),
            "files": message.get("files"),
        }

        out.append(
            {
                "conversation_id": short_conversation_id,
                "conv_id": short_conversation_id,
                "message_id": short_message_id,
                "id": short_message_id,
                "parent_id": None,
                "role": _normalize_role(message.get("role")),
                "ts": ts,
                "created_at": created_at,
                "content": {"content_type": "text", "parts": parts},
                "text": content,
                "meta": meta,
            }
        )

    return out


def get_adapter():
    return adapter


def get_input_records():
    return iter_input_records
