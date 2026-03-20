from __future__ import annotations

import json
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .l1_derivation import (
    ROLE_ORDER,
    UNKNOWN_ROLE,
    iter_parsed_records,
    normalize_role_value,
)


def string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def normalize_role(value: Any) -> str:
    return normalize_role_value(value)


def resolve_canonical_text(row: dict[str, Any]) -> tuple[str, str]:
    text = row.get("text")
    if isinstance(text, str):
        return text, "text"

    content = row.get("content")
    if isinstance(content, dict):
        parts = content.get("parts")
        if isinstance(parts, list):
            string_parts = [part for part in parts if isinstance(part, str)]
            if string_parts:
                return "\n".join(string_parts), "content.parts"

    return "", "empty"


def detect_header_metadata(parsed_path: Path) -> tuple[str | None, str | None]:
    provider_id: str | None = None
    conversation_id: str | None = None

    for row in iter_parsed_records(parsed_path):
        if row.get("record_type") == "thread":
            provider_id = provider_id or string_or_none(row.get("provider_id"))
            conversation_id = conversation_id or string_or_none(
                row.get("conversation_id")
            )
            if provider_id and conversation_id:
                break
            continue

        if row.get("record_type") != "message":
            continue

        provider_id = provider_id or string_or_none(row.get("provider_id"))
        conversation_id = conversation_id or string_or_none(row.get("conversation_id"))
        break

    return provider_id, conversation_id


def safe_ratio(numerator: int | float, denominator: int | float) -> float:
    if not denominator:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def safe_average(total: int | float, count: int | float) -> float:
    if not count:
        return 0.0
    return round(float(total) / float(count), 2)


def normalize_analysis_text(text: str) -> str:
    return " ".join(text.casefold().split())


def has_min_normalized_length(text: str, minimum: int) -> bool:
    return len(normalize_analysis_text(text)) >= minimum


def normalized_similarity(left: str, right: str) -> float:
    left_normalized = normalize_analysis_text(left)
    right_normalized = normalize_analysis_text(right)
    if not left_normalized or not right_normalized:
        return 0.0
    return SequenceMatcher(None, left_normalized, right_normalized).ratio()


def render_artifact_json(artifact: dict[str, Any]) -> str:
    return json.dumps(artifact, ensure_ascii=False, indent=2)


def write_json_artifact(path: Path, artifact: dict[str, Any]) -> Path:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(render_artifact_json(artifact), encoding="utf-8")
    tmp.replace(path)
    return path
