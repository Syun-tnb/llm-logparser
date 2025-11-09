# src/llm_logparser/providers/openai/adapter.py
from __future__ import annotations
import typing as t
from .utils import normalize_text

# -------------------------------------------------------------------
# Manifest & Policy
# -------------------------------------------------------------------

def get_manifest() -> dict:
    """Schema/format definition for OpenAI ChatGPT export JSON."""
    return {
        "schema_version": "1.1",
        "provider": "openai",
        "input_format": "chatgpt_export_v2+",
        "description": "Adapter for OpenAI ChatGPT export JSON (mapping-based structure)",
        "expected_top_keys": ["mapping", "id", "create_time", "update_time"],
        "id_fields": ["conversation_id", "message_id"],  # schema統一
    }


def get_policy() -> dict:
    """Normalization and output policy for OpenAI exports."""
    return {
        "keep_unicode_escape": True,
        "ignore_fields": ["metadata", "status", "recipient", "weight"],
        "flatten_parts_in_exporter": True,
        "allow_partial_parse": True,
        "timestamp_fields": ["create_time", "end_turn_time", "timestamp"],
        "safe_null_handling": True,
    }


# -------------------------------------------------------------------
# Core Adapter
# -------------------------------------------------------------------

PREFERRED_KEYS = ("summary", "result", "user_profile", "user_instructions")


def adapter(conversation: dict) -> list[dict]:
    """
    Convert a single ChatGPT export conversation into normalized message entries.
    Compatible with mapping-based ChatGPT export v2+.
    """
    conv_id = (
        conversation.get("id")
        or conversation.get("conversation_id")
        or conversation.get("uuid")
        or "unknown"
    )

    mapping = conversation.get("mapping")
    if not isinstance(mapping, dict):
        return []

    out: list[dict] = []
    for node_id, node in mapping.items():
        if not isinstance(node, dict):
            continue

        msg = node.get("message")
        if not isinstance(msg, dict):
            continue

        msg_id = msg.get("id") or node_id
        # skip dummy/root/system nodes
        if msg_id in ("client-created-root", "root"):
            continue

        author = msg.get("author") or {}
        author_role = author.get("role") or msg.get("role") or "unknown"
        content = msg.get("content")

        # ignore empty nodes
        if not author_role and not content:
            continue

        # timestamp resolution (prefer message-level)
        ts = (
            msg.get("create_time")
            or node.get("create_time")
            or msg.get("end_turn_time")
            or msg.get("timestamp")
        )

        # normalize text (Exporter互換のためtextを維持)
        text = normalize_text(content, preferred_keys=PREFERRED_KEYS, allow_loose=True)

        # metadata and model resolution
        mmeta = (msg.get("metadata") or {})
        model = (
            mmeta.get("model_slug")
            or mmeta.get("default_model_slug")
            or mmeta.get("model")
            or msg.get("model")
            or "unknown"
        )

        relations = {
            "parent": node.get("parent"),
            "children": node.get("children") or [],
        }

        meta = {
            "provider": "openai",
            "model": model,
            "source": mmeta.get("source") or node.get("type") or None,
            "relations": relations,
        }

        out.append(
            {
                "conversation_id": conv_id,
                "message_id": msg_id,
                "author_role": author_role,
                "text": text or "",
                "ts": ts,
                "meta": meta,
            }
        )

    # sort by timestamp if available
    out.sort(key=lambda e: (e["ts"] is None, e["ts"]))
    return out


def get_adapter():
    """Exported entrypoint (used by parser.load_provider)."""
    return adapter
