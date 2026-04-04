from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterator

BATCH_SIZE = 500


def _iter_jsonl_objects(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON in {path}:{line_no}: {exc.msg}") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"invalid JSON object in {path}:{line_no}")
            yield payload


def _json_text(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def ingest_message_windows(conn: sqlite3.Connection, thread_dir: Path) -> int:
    path = thread_dir / "message_windows.jsonl"
    if not path.exists():
        return 0

    batch: list[tuple[object, ...]] = []
    inserted = 0

    for row in _iter_jsonl_objects(path):
        batch.append(
            (
                row.get("provider_id"),
                row.get("conversation_id"),
                row.get("window_id"),
                _json_text(row.get("message_ids")),
                row.get("char_count"),
                row.get("ts_start"),
                row.get("ts_end"),
                row.get("window_size"),
                row.get("window_stride"),
            )
        )
        if len(batch) < BATCH_SIZE:
            continue
        conn.executemany(
            """
            INSERT INTO message_windows (
                provider_id,
                conversation_id,
                window_id,
                message_ids,
                char_count,
                ts_start,
                ts_end,
                window_size,
                window_stride
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            batch,
        )
        inserted += len(batch)
        batch = []

    if batch:
        conn.executemany(
            """
            INSERT INTO message_windows (
                provider_id,
                conversation_id,
                window_id,
                message_ids,
                char_count,
                ts_start,
                ts_end,
                window_size,
                window_stride
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            batch,
        )
        inserted += len(batch)

    return inserted
