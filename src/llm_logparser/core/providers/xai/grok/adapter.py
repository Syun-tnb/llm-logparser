from __future__ import annotations
from datetime import datetime, timezone
from typing import Any

from ....utils import shorten_id


def get_manifest() -> dict:
    return {
        "schema_version": "1.0",
        "provider": "xai",
        "family": "grok",
        "input_format": "grok_conversation_export",
        "description": "Adapter for xAI Grok conversation exports with conversation/responses records.",
        "expected_top_keys": ["conversation", "responses"],
        "id_fields": ["conversation_id", "message_id"],
    }


def get_policy() -> dict:
    return {
        "allow_partial_parse": True,
        "timestamp_fields": ["create_time", "thinking_start_time", "thinking_end_time"],
        "ignore_fields": ["metadata", "web_search_results", "steps", "agent_thinking_traces"],
        "safe_null_handling": True,
    }


def _to_epoch_ms(value: Any) -> int | None:
    if value is None:
        return None

    if isinstance(value, dict):
        if "$date" in value:
            return _to_epoch_ms(value.get("$date"))
        if "$numberLong" in value:
            return _to_epoch_ms(value.get("$numberLong"))
        if "value" in value:
            return _to_epoch_ms(value.get("value"))
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


def _to_iso_utc(value: Any) -> str | None:
    ts = _to_epoch_ms(value)
    if ts is None:
        return None
    return (
        datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _normalize_role(sender: Any) -> str:
    if not isinstance(sender, str) or not sender.strip():
        return "system"

    normalized = sender.strip().lower()
    if normalized in {"human", "user", "customer"}:
        return "user"
    if normalized in {"assistant", "bot", "grok", "model"}:
        return "assistant"
    if normalized in {"system"}:
        return "system"
    if normalized in {"tool", "function"} or "tool" in normalized:
        return "tool"
    if "assistant" in normalized:
        return "assistant"
    if "user" in normalized or "human" in normalized:
        return "user"
    if "system" in normalized:
        return "system"
    return "assistant"


class _ValidatedMessage(dict):
    def __init__(self, *args, validation_content: dict | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._validation_content = validation_content

    def get(self, key, default=None):
        if key == "content" and self._validation_content is not None:
            return self._validation_content
        return super().get(key, default)


def _append_part(parts: list[str], value: str) -> None:
    if value:
        parts.append(value)


def _extract_text_parts(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        return [value] if value else []

    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            parts.extend(_extract_text_parts(item))
        return parts

    if isinstance(value, dict):
        block_type = value.get("type")
        if block_type == "text" and isinstance(value.get("text"), str):
            return [value["text"]]

        direct_parts: list[str] = []
        for key in ("text", "message"):
            item = value.get(key)
            if isinstance(item, str) and item:
                _append_part(direct_parts, item)
        if direct_parts:
            return direct_parts

        for key in ("parts", "content", "blocks", "items", "messages"):
            nested = value.get(key)
            nested_parts = _extract_text_parts(nested)
            if nested_parts:
                return nested_parts

    return []


def _unwrap_conversation(raw: dict) -> dict | None:
    if isinstance(raw.get("conversation"), dict) and isinstance(raw.get("responses"), list):
        return raw

    return None


def _unwrap_response(item: Any) -> dict | None:
    if isinstance(item, dict):
        response = item.get("response", item)
        if isinstance(response, dict):
            return response
    return None


def adapter(conversation: dict, *, source: str | None = None) -> list[dict]:
    del source

    bundle = _unwrap_conversation(conversation)
    if bundle is None:
        return []

    conversation_meta = bundle.get("conversation")
    if not isinstance(conversation_meta, dict):
        return []

    raw_conversation_id = conversation_meta.get("id")
    if not isinstance(raw_conversation_id, str) or not raw_conversation_id:
        return []

    short_conversation_id = shorten_id(raw_conversation_id)
    thread_title = conversation_meta.get("title") if isinstance(conversation_meta.get("title"), str) else None
    root_created_at = conversation_meta.get("create_time")

    responses = bundle.get("responses")
    if not isinstance(responses, list):
        return []

    out: list[tuple[int, int, str, dict[str, Any]]] = []
    for index, item in enumerate(responses):
        response = _unwrap_response(item)
        if response is None:
            continue

        raw_message_id = response.get("_id")
        if not isinstance(raw_message_id, str) or not raw_message_id:
            continue

        created_raw = response.get("create_time", root_created_at)
        ts = _to_epoch_ms(created_raw)
        created_at = _to_iso_utc(created_raw)
        if ts is None or created_at is None:
            continue

        parts = _extract_text_parts(response.get("message"))
        if not parts:
            for candidate in ("content", "parts", "blocks", "items"):
                parts = _extract_text_parts(response.get(candidate))
                if parts:
                    break

        if not parts and not isinstance(item, dict):
            continue

        content_type = "text"
        media_types = response.get("media_types")
        if not parts and isinstance(media_types, list) and media_types:
            content_type = str(media_types[0])

        meta: dict[str, Any] = {}
        model = response.get("model")
        if isinstance(model, str) and model.strip():
            meta["model"] = model.strip()

        raw_parent_id = response.get("parent_response_id")
        parent_id = shorten_id(raw_parent_id) if isinstance(raw_parent_id, str) and raw_parent_id else None

        entry = _ValidatedMessage(
            {
            "conversation_id": short_conversation_id,
            "conv_id": short_conversation_id,
            "message_id": shorten_id(raw_message_id),
            "id": shorten_id(raw_message_id),
            "parent_id": parent_id,
            "role": _normalize_role(response.get("sender")),
            "ts": ts,
            "created_at": created_at,
            "thread_title": thread_title,
            "content": item if isinstance(item, dict) else response,
            "text": "\n".join(parts),
            },
            validation_content={"content_type": content_type, "parts": parts},
        )
        if meta:
            entry["meta"] = meta

        out.append((ts, index, entry["message_id"], entry))

    out.sort(key=lambda item: (item[0], item[1], item[2]))
    return [item[3] for item in out]


def expand_input_records(raw: dict) -> list[dict]:
    if not isinstance(raw, dict):
        return []

    bundle = _unwrap_conversation(raw)
    if bundle is not None:
        return [bundle]

    conversations = raw.get("conversations")
    if isinstance(conversations, list):
        return [item for item in conversations if isinstance(item, dict)]

    return []


def get_adapter():
    return adapter


def get_record_expander():
    return expand_input_records
