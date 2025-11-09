# src/llm_logparser/providers/openai/utils.py
from __future__ import annotations
import re
import json
import typing as t

from decimal import Decimal


# -------------------------------------------------------------------
# JSON serialization safety helpers
# -------------------------------------------------------------------

def json_safe(obj: t.Any) -> t.Any:
    """Recursively make data JSON-serializable (for exporter output)."""
    if obj is None:
        return None
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [json_safe(v) for v in obj]
    return str(obj)


# -------------------------------------------------------------------
# Text sanitation utilities
# -------------------------------------------------------------------

def _sanitize_impl(s: str) -> str:
    """Remove control characters and normalize whitespace."""
    s = s.replace("\u00A0", " ")  # NBSP → space
    s = re.sub(r"[\x00-\x1F\x7F]", "", s)  # control chars
    s = re.sub(r"\s+", " ", s).strip()
    return s


def sanitize_text(s: t.Optional[t.Any]) -> str:
    """Safely convert input into a clean, single-line string."""
    if s is None:
        return ""
    if isinstance(s, (dict, list)):
        try:
            # Decimal などを安全な形に変換してから JSON 文字列化
            from .utils import json_safe
            s = json.dumps(json_safe(s), ensure_ascii=False)
        except Exception:
            s = str(s)
    return _sanitize_impl(str(s))


# -------------------------------------------------------------------
# Parts → Text flatten helpers
# -------------------------------------------------------------------

def _strict_parts_to_text(parts: list[t.Any]) -> str:
    """Flatten parts strictly. Invalid elements raise ValueError."""
    texts: list[str] = []
    for p in parts:
        if isinstance(p, str):
            texts.append(_sanitize_impl(p))
        elif isinstance(p, dict):
            # structured parts (delta/text)
            val = p.get("text") or p.get("delta")
            if isinstance(val, str):
                texts.append(_sanitize_impl(val))
            else:
                raise ValueError(f"Invalid part: {p!r}")
        else:
            raise ValueError(f"Unsupported part type: {type(p)}")
    return " ".join(texts)


def _loose_parts_to_text(parts: list[t.Any]) -> str:
    """Flatten parts loosely. Tolerant of mixed/invalid items."""
    texts: list[str] = []
    for p in parts:
        if isinstance(p, str):
            texts.append(_sanitize_impl(p))
        elif isinstance(p, dict):
            val = p.get("text") or p.get("delta") or json.dumps(json_safe(p), ensure_ascii=False)
            texts.append(_sanitize_impl(val))
        else:
            texts.append(_sanitize_impl(str(p)))
    return " ".join(texts)


# -------------------------------------------------------------------
# ChatGPT export normalization helpers
# -------------------------------------------------------------------

def extract_chatgpt_text(content: t.Any) -> str:
    """
    Extract the most human-readable text from ChatGPT export content.

    Supported forms:
      - {"parts": [...]}
      - {"content_type": "text", "text": "..."}
      - {"type": "message", "data": "..."}
    """
    if not content:
        return ""
    if isinstance(content, str):
        return _sanitize_impl(content)
    if isinstance(content, dict):
        # common key paths
        if "parts" in content and isinstance(content["parts"], list):
            return _loose_parts_to_text(content["parts"])
        if "text" in content:
            return _sanitize_impl(str(content["text"]))
        if "data" in content:
            return _sanitize_impl(str(content["data"]))
    return sanitize_text(content)


# -------------------------------------------------------------------
# Unified text normalization entrypoint
# -------------------------------------------------------------------

def normalize_text(
    content: t.Any,
    *,
    preferred_keys: tuple[str, ...] = (),
    allow_loose: bool = True,
    preserve_structure: bool = False,
) -> t.Union[str, dict]:
    """
    Normalize ChatGPT content to text (or structured form, if preserve_structure=True).

    - If preserve_structure=True: return {"content_type": ..., "parts": [...]} (for future Exporter v2)
    - Else: return plain text (for current Exporter compatibility)
    """
    # Try preferred keys (like "summary", "result", etc.)
    if isinstance(content, dict):
        for k in preferred_keys:
            if k in content and isinstance(content[k], (str, list, dict)):
                content = content[k]
                break

    # Structural preservation path (for future exporter)
    if preserve_structure:
        if isinstance(content, dict) and "parts" in content:
            return {
                "content_type": content.get("content_type", "text"),
                "parts": content.get("parts", []),
            }
        if isinstance(content, str):
            return {"content_type": "text", "parts": [content]}
        if isinstance(content, list):
            return {"content_type": "text", "parts": content}
        return {"content_type": "text", "parts": [str(content)]}

    # Text normalization (current MVP behavior)
    try:
        if isinstance(content, dict) and "parts" in content:
            parts = content["parts"]
            if allow_loose:
                return _loose_parts_to_text(parts)
            return _strict_parts_to_text(parts)
        if isinstance(content, list):
            return _loose_parts_to_text(content) if allow_loose else _strict_parts_to_text(content)
        if isinstance(content, str):
            return _sanitize_impl(content)
    except Exception:
        pass

    return extract_chatgpt_text(content)
