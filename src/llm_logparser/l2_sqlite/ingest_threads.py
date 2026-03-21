from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"invalid JSON object in {path}")
    return payload


def _iso_to_epoch_ms(value: Any) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"invalid ISO timestamp: {value}") from exc
    return int(dt.timestamp() * 1000)


def _json_text(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def ingest_thread_stats(conn: sqlite3.Connection, thread_dir: Path) -> int:
    path = thread_dir / "thread_stats.json"
    if not path.exists():
        return 0

    payload = _load_json_object(path)
    character_count = payload.get("character_count")

    conn.execute(
        """
        INSERT INTO threads (
            provider_id,
            conversation_id,
            message_count,
            user_messages,
            assistant_messages,
            other_roles,
            character_count,
            characters_total,
            characters_user,
            characters_assistant,
            other_role_breakdown,
            first_timestamp,
            last_timestamp,
            conversation_span_seconds
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload.get("provider_id"),
            payload.get("conversation_id"),
            payload.get("message_count"),
            payload.get("user_messages"),
            payload.get("assistant_messages"),
            payload.get("other_roles"),
            character_count,
            # Keep the legacy SQLite column for compatibility with existing
            # queries while also exposing the canonical JSON field name.
            character_count,
            payload.get("characters_user"),
            payload.get("characters_assistant"),
            _json_text(payload.get("other_role_breakdown")),
            _iso_to_epoch_ms(payload.get("first_timestamp")),
            _iso_to_epoch_ms(payload.get("last_timestamp")),
            payload.get("conversation_span_seconds"),
        ),
    )
    return 1
