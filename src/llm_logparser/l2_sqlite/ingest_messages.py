from __future__ import annotations

import sqlite3
from pathlib import Path

from llm_logparser.core.l1_derivation import (
    iter_message_records,
    message_character_count,
    message_role,
    message_text,
)

BATCH_SIZE = 500


def ingest_messages(conn: sqlite3.Connection, thread_dir: Path) -> int:
    parsed_path = thread_dir / "parsed.jsonl"
    if not parsed_path.exists():
        return 0

    batch: list[tuple[object, ...]] = []
    inserted = 0

    for row in iter_message_records(parsed_path):
        batch.append(
            (
                row.get("provider_id"),
                row.get("conversation_id"),
                row.get("message_id"),
                message_role(row) or "unknown",
                row.get("ts"),
                message_character_count(row),
                message_text(row),
            )
        )
        if len(batch) < BATCH_SIZE:
            continue
        conn.executemany(
            """
            INSERT INTO messages (
                provider_id,
                conversation_id,
                message_id,
                role,
                ts,
                char_count,
                text
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            batch,
        )
        inserted += len(batch)
        batch = []

    if batch:
        conn.executemany(
            """
            INSERT INTO messages (
                provider_id,
                conversation_id,
                message_id,
                role,
                ts,
                char_count,
                text
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            batch,
        )
        inserted += len(batch)

    return inserted
