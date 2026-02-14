from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from llm_logparser.core.parser import LLPInputError, iter_json_records
from llm_logparser.core.providers.openai.chatgpt.utils import json_safe

_REDACTED = "REDACTED"
_SENSITIVE_KEYWORDS = (
    "SECRET",
    "TOKEN",
    "API_KEY",
    "AUTHORIZATION",
    "COOKIE",
    "PASSWORD",
)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"\b(?:\+?\d[\d().\s-]{7,}\d)\b")


def _is_sensitive_key(key: str) -> bool:
    upper_key = key.upper()
    return any(keyword in upper_key for keyword in _SENSITIVE_KEYWORDS)


def _sanitize_text_part(value: str) -> str:
    sanitized = _EMAIL_RE.sub("[REDACTED_EMAIL]", value)
    sanitized = _PHONE_RE.sub("[REDACTED_PHONE]", sanitized)
    return sanitized


def _sanitize_value(value: Any, *, path: tuple[str, ...] = ()) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, child in value.items():
            if _is_sensitive_key(key):
                out[key] = _REDACTED
                continue
            out[key] = _sanitize_value(child, path=(*path, key))
        return out

    if isinstance(value, list):
        # only sanitize text content inside `content.parts: list[str]`
        if len(path) >= 2 and path[-2] == "content" and path[-1] == "parts":
            out_parts: list[Any] = []
            for item in value:
                if isinstance(item, str):
                    out_parts.append(_sanitize_text_part(item))
                else:
                    out_parts.append(item)
            return out_parts
        return [_sanitize_value(item, path=path) for item in value]

    return value


def _conversation_id_of(record: dict[str, Any]) -> str | None:
    for key in ("conversation_id", "id", "uuid"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def get_extractor():
    def extract(
        *,
        input_path: Path,
        outdir: Path,
        provider: str,
        conversation_id: str,
        dry_run: bool = False,
        logger: logging.Logger | None = None,
    ) -> dict[str, Any]:
        log = logger or logging.getLogger("llm_logparser.extractor")

        matched_record: dict[str, Any] | None = None
        scanned = 0
        for raw in iter_json_records(input_path, log):
            scanned += 1
            if not isinstance(raw, dict):
                continue
            if _conversation_id_of(raw) == conversation_id:
                matched_record = raw
                break

        if matched_record is None:
            raise LLPInputError(f"conversation not found: {conversation_id}")

        sanitized = json_safe(_sanitize_value(matched_record))
        thread_dir = outdir / provider / f"thread-{conversation_id}"
        out_path = thread_dir / "extract.json"

        if dry_run:
            log.info(f"[extract] dry-run: matched conversation={conversation_id}; skip writing {out_path}")
            return {
                "conversation_id": conversation_id,
                "records_scanned": scanned,
                "path": str(out_path),
                "written": False,
            }

        thread_dir.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as f:
            json.dump([sanitized], f, ensure_ascii=False, indent=2)
            f.write("\n")
        log.info(f"[extract] wrote {out_path}")
        return {
            "conversation_id": conversation_id,
            "records_scanned": scanned,
            "path": str(out_path),
            "written": True,
        }

    return extract
