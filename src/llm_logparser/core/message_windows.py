from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Iterator

from .l1_derivation import (
    iter_message_records,
    message_character_count,
    message_role,
    message_text,
)

DEFAULT_MESSAGE_WINDOW_SIZE = 4
DEFAULT_MESSAGE_WINDOW_STRIDE: int | None = None


def _message_timestamp(row: dict[str, Any]) -> int | float | None:
    ts = row.get("ts")
    return ts if isinstance(ts, (int, float)) else None


def _window_id(index: int) -> str:
    return f"window-{index:04d}"


def _window_text(rows: list[dict[str, Any]]) -> str:
    parts = []
    for row in rows:
        parts.append(f"{message_role(row) or 'unknown'}: {message_text(row)}")
    return "\n\n".join(parts)


def build_message_window_artifact(
    rows: list[dict[str, Any]],
    *,
    window_index: int,
    window_size: int,
    window_stride: int,
) -> dict[str, Any]:
    """Build a deterministic message window artifact from canonical message rows."""
    if not rows:
        raise ValueError("message window requires at least one message")

    provider_id = rows[0].get("provider_id")
    conversation_id = rows[0].get("conversation_id")
    timestamps = [ts for row in rows if (ts := _message_timestamp(row)) is not None]

    return {
        "record_type": "message_window",
        "schema_version": "1.0",
        "provider_id": provider_id,
        "conversation_id": conversation_id,
        "window_id": _window_id(window_index),
        "message_ids": [row.get("message_id") for row in rows],
        "roles": [message_role(row) or "unknown" for row in rows],
        "message_count": len(rows),
        "char_count": sum(message_character_count(row) for row in rows),
        "ts_start": min(timestamps) if timestamps else None,
        "ts_end": max(timestamps) if timestamps else None,
        "window_size": window_size,
        "window_stride": window_stride,
        "text": _window_text(rows),
    }


def resolve_message_window_stride(
    *,
    window_size: int,
    window_stride: int | None = DEFAULT_MESSAGE_WINDOW_STRIDE,
) -> int:
    if window_size <= 0:
        raise ValueError("window_size must be > 0")
    stride = window_size if window_stride is None else window_stride
    if stride <= 0:
        raise ValueError("window_stride must be > 0")
    return stride


def iter_message_windows_from_rows(
    rows: Iterable[dict[str, Any]],
    *,
    window_size: int = DEFAULT_MESSAGE_WINDOW_SIZE,
    window_stride: int | None = DEFAULT_MESSAGE_WINDOW_STRIDE,
) -> Iterator[dict[str, Any]]:
    """Yield deterministic contiguous windows from canonical message rows."""
    stride = resolve_message_window_stride(
        window_size=window_size,
        window_stride=window_stride,
    )
    message_rows = [
        row for row in rows if row.get("record_type") == "message"
    ]
    if not message_rows:
        return

    window_index = 1
    for start in range(0, len(message_rows), stride):
        chunk = message_rows[start : start + window_size]
        if not chunk:
            continue
        yield build_message_window_artifact(
            chunk,
            window_index=window_index,
            window_size=window_size,
            window_stride=stride,
        )
        window_index += 1


def iter_message_windows(
    parsed_path: Path,
    *,
    window_size: int = DEFAULT_MESSAGE_WINDOW_SIZE,
    window_stride: int | None = DEFAULT_MESSAGE_WINDOW_STRIDE,
) -> Iterator[dict[str, Any]]:
    """Yield deterministic message windows from canonical parsed.jsonl records."""
    yield from iter_message_windows_from_rows(
        iter_message_records(parsed_path),
        window_size=window_size,
        window_stride=window_stride,
    )


def render_message_windows_jsonl(
    rows: Iterable[dict[str, Any]],
    *,
    window_size: int = DEFAULT_MESSAGE_WINDOW_SIZE,
    window_stride: int | None = DEFAULT_MESSAGE_WINDOW_STRIDE,
) -> str:
    """Render message windows as JSONL."""
    lines = [
        json.dumps(window, ensure_ascii=True)
        for window in iter_message_windows_from_rows(
            rows,
            window_size=window_size,
            window_stride=window_stride,
        )
    ]
    return "\n".join(lines) + ("\n" if lines else "")
