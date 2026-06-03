from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class RecallError(Exception):
    pass


@dataclass(frozen=True)
class RecallMessage:
    provider_id: str
    conversation_id: str
    message_id: str | None
    role: str | None
    ts: int | None
    text: str

    def to_json(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "conversation_id": self.conversation_id,
            "message_id": self.message_id,
            "role": self.role,
            "ts": self.ts,
            "text": self.text,
        }


def _validate_limit(limit: int) -> int:
    if limit <= 0:
        raise RecallError("limit must be greater than 0")
    return limit


def _db_path(input_root: Path) -> Path:
    root = input_root.expanduser()
    if not root.exists():
        raise RecallError(f"provider artifact root not found: {root}")
    if not root.is_dir():
        raise RecallError(f"provider artifact root must be a directory: {root}")
    db_path = root / "analysis.db"
    if not db_path.exists():
        raise RecallError(
            f"analysis.db not found: {db_path}. "
            "Run `llm-logparser analyze sqlite-build` first."
        )
    if not db_path.is_file():
        raise RecallError(f"analysis.db must be a file: {db_path}")
    return db_path


def search_recall_messages(
    input_root: Path,
    *,
    query: str,
    limit: int = 10,
    role: str | None = None,
    conversation_id: str | None = None,
) -> list[RecallMessage]:
    if not query.strip():
        raise RecallError("query must not be empty")
    limit = _validate_limit(limit)
    db_path = _db_path(input_root)

    clauses = ["messages_fts MATCH ?"]
    params: list[Any] = [query]
    if role:
        clauses.append("m.role = ?")
        params.append(role)
    if conversation_id:
        clauses.append("m.conversation_id = ?")
        params.append(conversation_id)
    params.append(limit)

    sql = f"""
        SELECT
            m.provider_id,
            m.conversation_id,
            m.message_id,
            m.role,
            m.ts,
            m.text
        FROM messages_fts
        JOIN messages AS m
          ON m.rowid = messages_fts.rowid
        WHERE {" AND ".join(clauses)}
        ORDER BY bm25(messages_fts) ASC,
                 m.ts ASC,
                 m.conversation_id ASC,
                 m.message_id ASC
        LIMIT ?
    """

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        message = str(exc)
        if "no such table: messages_fts" in message:
            raise RecallError(
                f"analysis.db does not contain messages_fts: {db_path}. "
                "Rebuild it with `llm-logparser analyze sqlite-build --overwrite`."
            ) from exc
        raise RecallError(f"recall query failed: {message}") from exc
    finally:
        conn.close()

    return [
        RecallMessage(
            provider_id=str(provider_id),
            conversation_id=str(row_conversation_id),
            message_id=message_id,
            role=row_role,
            ts=ts,
            text=text or "",
        )
        for provider_id, row_conversation_id, message_id, row_role, ts, text in rows
    ]


def render_recall_json(
    messages: list[RecallMessage],
    *,
    query: str,
    limit: int,
    role: str | None = None,
    conversation_id: str | None = None,
) -> str:
    payload = {
        "artifact_type": "recall_results",
        "schema_version": "0.1",
        "query": query,
        "limit": limit,
        "filters": {
            "role": role,
            "conversation_id": conversation_id,
        },
        "results": [message.to_json() for message in messages],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def render_recall_text(messages: list[RecallMessage], *, query: str) -> str:
    lines = [f"Recall results for: {query}", f"Matches: {len(messages)}"]
    if not messages:
        return "\n".join(lines) + "\n"

    for index, message in enumerate(messages, start=1):
        identity = (
            f"{message.provider_id}/{message.conversation_id}"
            f"/{message.message_id or 'unknown'}"
        )
        role = message.role or "unknown"
        ts = "unknown" if message.ts is None else str(message.ts)
        text = " ".join(message.text.split())
        if len(text) > 240:
            text = text[:237].rstrip() + "..."
        lines.extend(
            [
                "",
                f"{index}. {identity}",
                f"   role={role} ts={ts}",
                f"   {text}",
            ]
        )
    return "\n".join(lines) + "\n"
