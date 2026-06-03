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


@dataclass(frozen=True)
class RecallResult:
    anchor: RecallMessage
    context_before: tuple[RecallMessage, ...] = ()
    context_after: tuple[RecallMessage, ...] = ()
    bookend_start: tuple[RecallMessage, ...] = ()
    bookend_end: tuple[RecallMessage, ...] = ()

    def to_json(self) -> dict[str, Any]:
        payload = self.anchor.to_json()
        payload["anchor"] = self.anchor.to_json()
        payload["context_before"] = [
            message.to_json() for message in self.context_before
        ]
        payload["context_after"] = [
            message.to_json() for message in self.context_after
        ]
        payload["bookend_start"] = [
            message.to_json() for message in self.bookend_start
        ]
        payload["bookend_end"] = [
            message.to_json() for message in self.bookend_end
        ]
        return payload


def _validate_limit(limit: int) -> int:
    if limit <= 0:
        raise RecallError("limit must be greater than 0")
    return limit


def _validate_context_count(value: int, *, name: str) -> int:
    if value < 0:
        raise RecallError(f"{name} must be >= 0")
    return value


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
    context_before: int = 0,
    context_after: int = 0,
    bookends: int = 0,
) -> list[RecallResult]:
    if not query.strip():
        raise RecallError("query must not be empty")
    limit = _validate_limit(limit)
    context_before = _validate_context_count(context_before, name="context_before")
    context_after = _validate_context_count(context_after, name="context_after")
    bookends = _validate_context_count(bookends, name="bookends")
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
            m.text,
            m.rowid
        FROM messages_fts
        JOIN messages AS m
          ON m.rowid = messages_fts.rowid
        WHERE {" AND ".join(clauses)}
        ORDER BY bm25(messages_fts) ASC,
                 m.ts ASC,
                 m.conversation_id ASC,
                 m.message_id ASC,
                 m.rowid ASC
        LIMIT ?
    """

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
        results = [
            _recall_result_from_row(
                conn,
                row,
                context_before=context_before,
                context_after=context_after,
                bookends=bookends,
            )
            for row in rows
        ]
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

    return results


def _message_from_row(row: tuple[Any, ...]) -> RecallMessage:
    provider_id, conversation_id, message_id, role, ts, text = row[:6]
    return RecallMessage(
        provider_id=str(provider_id),
        conversation_id=str(conversation_id),
        message_id=message_id,
        role=role,
        ts=ts,
        text=text or "",
    )


def _context_before(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    anchor_rowid: int,
    anchor_ts: int | None,
    anchor_message_id: str | None,
    limit: int,
) -> tuple[RecallMessage, ...]:
    if limit == 0:
        return ()
    rows = conn.execute(
        """
        SELECT provider_id, conversation_id, message_id, role, ts, text
        FROM (
            SELECT provider_id, conversation_id, message_id, role, ts, text,
                   rowid
            FROM messages
            WHERE conversation_id = ?
              AND (
                COALESCE(ts, -9223372036854775808) < ?
                OR (
                  COALESCE(ts, -9223372036854775808) = ?
                  AND COALESCE(message_id, '') < ?
                )
                OR (
                  COALESCE(ts, -9223372036854775808) = ?
                  AND COALESCE(message_id, '') = ?
                  AND rowid < ?
                )
              )
            ORDER BY COALESCE(ts, -9223372036854775808) DESC,
                     message_id DESC,
                     rowid DESC
            LIMIT ?
        )
        ORDER BY COALESCE(ts, -9223372036854775808) ASC,
                 message_id ASC,
                 rowid ASC
        """,
        (
            conversation_id,
            _sort_ts(anchor_ts),
            _sort_ts(anchor_ts),
            _sort_message_id(anchor_message_id),
            _sort_ts(anchor_ts),
            _sort_message_id(anchor_message_id),
            anchor_rowid,
            limit,
        ),
    ).fetchall()
    return tuple(_message_from_row(row) for row in rows)


def _context_after(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    anchor_rowid: int,
    anchor_ts: int | None,
    anchor_message_id: str | None,
    limit: int,
) -> tuple[RecallMessage, ...]:
    if limit == 0:
        return ()
    rows = conn.execute(
        """
        SELECT provider_id, conversation_id, message_id, role, ts, text
        FROM messages
        WHERE conversation_id = ?
          AND (
            COALESCE(ts, -9223372036854775808) > ?
            OR (
              COALESCE(ts, -9223372036854775808) = ?
              AND COALESCE(message_id, '') > ?
            )
            OR (
              COALESCE(ts, -9223372036854775808) = ?
              AND COALESCE(message_id, '') = ?
              AND rowid > ?
            )
          )
        ORDER BY COALESCE(ts, -9223372036854775808) ASC,
                 message_id ASC,
                 rowid ASC
        LIMIT ?
        """,
        (
            conversation_id,
            _sort_ts(anchor_ts),
            _sort_ts(anchor_ts),
            _sort_message_id(anchor_message_id),
            _sort_ts(anchor_ts),
            _sort_message_id(anchor_message_id),
            anchor_rowid,
            limit,
        ),
    ).fetchall()
    return tuple(_message_from_row(row) for row in rows)


def _recall_result_from_row(
    conn: sqlite3.Connection,
    row: tuple[Any, ...],
    *,
    context_before: int,
    context_after: int,
    bookends: int,
) -> RecallResult:
    anchor = _message_from_row(row)
    anchor_rowid = int(row[6])
    before = _context_before(
        conn,
        conversation_id=anchor.conversation_id,
        anchor_rowid=anchor_rowid,
        anchor_ts=anchor.ts,
        anchor_message_id=anchor.message_id,
        limit=context_before,
    )
    after = _context_after(
        conn,
        conversation_id=anchor.conversation_id,
        anchor_rowid=anchor_rowid,
        anchor_ts=anchor.ts,
        anchor_message_id=anchor.message_id,
        limit=context_after,
    )
    excluded = {_message_key(anchor)}
    excluded.update(_message_key(message) for message in before)
    excluded.update(_message_key(message) for message in after)
    start = _bookend_start(
        conn,
        conversation_id=anchor.conversation_id,
        limit=bookends,
        exclude_keys=excluded,
    )
    excluded.update(_message_key(message) for message in start)
    end = _bookend_end(
        conn,
        conversation_id=anchor.conversation_id,
        limit=bookends,
        exclude_keys=excluded,
    )
    return RecallResult(
        anchor=anchor,
        context_before=before,
        context_after=after,
        bookend_start=start,
        bookend_end=end,
    )


def _message_key(message: RecallMessage) -> tuple[object, ...]:
    return (
        message.provider_id,
        message.conversation_id,
        message.message_id,
        message.role,
        message.ts,
        message.text,
    )


def _filter_excluded(
    messages: tuple[RecallMessage, ...],
    *,
    exclude_keys: set[tuple[object, ...]],
    limit: int,
) -> tuple[RecallMessage, ...]:
    kept: list[RecallMessage] = []
    for message in messages:
        key = _message_key(message)
        if key in exclude_keys:
            continue
        kept.append(message)
        if len(kept) >= limit:
            break
    return tuple(kept)


def _bookend_start(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    limit: int,
    exclude_keys: set[tuple[object, ...]],
) -> tuple[RecallMessage, ...]:
    if limit == 0:
        return ()
    rows = conn.execute(
        """
        SELECT provider_id, conversation_id, message_id, role, ts, text
        FROM messages
        WHERE conversation_id = ?
        ORDER BY COALESCE(ts, -9223372036854775808) ASC,
                 message_id ASC,
                 rowid ASC
        """,
        (conversation_id,),
    ).fetchall()
    return _filter_excluded(
        tuple(_message_from_row(row) for row in rows),
        exclude_keys=exclude_keys,
        limit=limit,
    )


def _bookend_end(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    limit: int,
    exclude_keys: set[tuple[object, ...]],
) -> tuple[RecallMessage, ...]:
    if limit == 0:
        return ()
    rows = conn.execute(
        """
        SELECT provider_id, conversation_id, message_id, role, ts, text
        FROM messages
        WHERE conversation_id = ?
        ORDER BY COALESCE(ts, -9223372036854775808) DESC,
                 message_id DESC,
                 rowid DESC
        """,
        (conversation_id,),
    ).fetchall()
    kept = _filter_excluded(
        tuple(_message_from_row(row) for row in rows),
        exclude_keys=exclude_keys,
        limit=limit,
    )
    return tuple(reversed(kept))


def _sort_ts(value: int | None) -> int:
    return -9223372036854775808 if value is None else value


def _sort_message_id(value: str | None) -> str:
    return "" if value is None else value


def render_recall_json(
    results: list[RecallResult],
    *,
    query: str,
    limit: int,
    role: str | None = None,
    conversation_id: str | None = None,
    context_before: int = 0,
    context_after: int = 0,
    bookends: int = 0,
) -> str:
    payload = {
        "artifact_type": "recall_results",
        "schema_version": "0.3",
        "query": query,
        "limit": limit,
        "context_before": context_before,
        "context_after": context_after,
        "bookends": bookends,
        "filters": {
            "role": role,
            "conversation_id": conversation_id,
        },
        "results": [result.to_json() for result in results],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def render_recall_text(results: list[RecallResult], *, query: str) -> str:
    lines = [f"Recall results for: {query}", f"Matches: {len(results)}"]
    if not results:
        return "\n".join(lines) + "\n"

    for index, result in enumerate(results, start=1):
        message = result.anchor
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
        if result.context_before:
            lines.append("   context_before:")
            for context_message in result.context_before:
                lines.append(f"   - {_compact_message(context_message)}")
        if result.context_after:
            lines.append("   context_after:")
            for context_message in result.context_after:
                lines.append(f"   - {_compact_message(context_message)}")
        if result.bookend_start:
            lines.append("   bookend_start:")
            for bookend_message in result.bookend_start:
                lines.append(f"   - {_compact_message(bookend_message)}")
        if result.bookend_end:
            lines.append("   bookend_end:")
            for bookend_message in result.bookend_end:
                lines.append(f"   - {_compact_message(bookend_message)}")
    return "\n".join(lines) + "\n"


def _compact_message(message: RecallMessage) -> str:
    role = message.role or "unknown"
    message_id = message.message_id or "unknown"
    text = " ".join(message.text.split())
    if len(text) > 160:
        text = text[:157].rstrip() + "..."
    return f"{message_id} role={role} ts={message.ts if message.ts is not None else 'unknown'} {text}"
