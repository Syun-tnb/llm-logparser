from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .ingest_messages import ingest_messages
from .ingest_threads import ingest_thread_stats
from .ingest_windows import ingest_message_windows
from .schema import SQLITE_SCHEMA_VERSION, create_schema, insert_metadata

REQUIRED_TABLES = frozenset({"metadata", "threads", "messages", "message_windows"})


def _iter_thread_dirs(provider_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in provider_dir.iterdir()
        if path.is_dir() and path.name.startswith("thread-")
    )


def _prepare_db_path(db_path: Path, *, overwrite: bool) -> None:
    if db_path.exists() and db_path.is_dir():
        raise IsADirectoryError(f"analysis.db path is a directory: {db_path}")
    if db_path.exists():
        if not overwrite:
            raise FileExistsError(f"analysis.db already exists: {db_path}")
        db_path.unlink()


def _configure_build_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL").fetchone()
    conn.execute("PRAGMA synchronous=OFF")


def _finalize_build_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("PRAGMA journal_mode=DELETE").fetchone()


def _validate_completed_build(conn: sqlite3.Connection, *, provider_id: str) -> None:
    placeholders = ", ".join("?" for _ in REQUIRED_TABLES)
    rows = conn.execute(
        f"""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name IN ({placeholders})
        """,
        tuple(sorted(REQUIRED_TABLES)),
    ).fetchall()
    found_tables = {name for (name,) in rows}
    missing_tables = sorted(REQUIRED_TABLES - found_tables)
    if missing_tables:
        raise ValueError(
            "analysis.db validation failed: missing required tables: "
            + ", ".join(missing_tables)
        )

    metadata_rows = conn.execute(
        """
        SELECT schema_version, provider_id
        FROM metadata
        """
    ).fetchall()
    if metadata_rows != [(SQLITE_SCHEMA_VERSION, provider_id)]:
        raise ValueError(
            "analysis.db validation failed: metadata row mismatch: "
            f"{metadata_rows!r}"
        )


def build_analysis_db(
    input_root: Path,
    provider_id: str,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    provider_dir = input_root.expanduser() / provider_id
    if not provider_dir.exists():
        raise FileNotFoundError(f"provider directory not found: {provider_dir}")
    if not provider_dir.is_dir():
        raise NotADirectoryError(f"provider directory not found: {provider_dir}")

    db_path = provider_dir / "analysis.db"
    _prepare_db_path(db_path, overwrite=overwrite)

    conn = sqlite3.connect(db_path)
    try:
        _configure_build_pragmas(conn)
        create_schema(conn)
        insert_metadata(conn, provider_id=provider_id)
        conn.commit()

        threads_inserted = 0
        messages_inserted = 0
        windows_inserted = 0

        for thread_dir in _iter_thread_dirs(provider_dir):
            conn.execute("BEGIN")
            try:
                threads_inserted += ingest_thread_stats(conn, thread_dir)
                messages_inserted += ingest_messages(conn, thread_dir)
                windows_inserted += ingest_message_windows(conn, thread_dir)
            except Exception:
                conn.rollback()
                raise
            conn.commit()

        conn.execute("ANALYZE")
        conn.commit()
        _validate_completed_build(conn, provider_id=provider_id)
        _finalize_build_pragmas(conn)

        return {
            "db_path": db_path,
            "provider_id": provider_id,
            "threads": threads_inserted,
            "messages": messages_inserted,
            "message_windows": windows_inserted,
        }
    finally:
        conn.close()
