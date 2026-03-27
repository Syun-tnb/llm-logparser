from __future__ import annotations

import sqlite3

SQLITE_SCHEMA_VERSION = "2"


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE metadata (
            schema_version TEXT NOT NULL,
            provider_id TEXT NOT NULL
        );

        CREATE TABLE threads (
            provider_id TEXT NOT NULL,
            conversation_id TEXT PRIMARY KEY,
            message_count INTEGER,
            user_messages INTEGER,
            assistant_messages INTEGER,
            other_roles INTEGER,
            character_count INTEGER,
            characters_total INTEGER,
            characters_user INTEGER,
            characters_assistant INTEGER,
            other_role_breakdown TEXT,
            first_timestamp INTEGER,
            last_timestamp INTEGER,
            conversation_span_seconds INTEGER
        );

        CREATE TABLE messages (
            provider_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            message_id TEXT,
            role TEXT,
            ts INTEGER,
            char_count INTEGER,
            text TEXT
        );

        CREATE INDEX idx_messages_conversation
        ON messages(conversation_id);

        CREATE INDEX idx_messages_ts
        ON messages(ts);

        CREATE INDEX idx_messages_role
        ON messages(role);

        CREATE TABLE message_windows (
            provider_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            window_id TEXT,
            message_ids TEXT,
            roles TEXT,
            message_count INTEGER,
            char_count INTEGER,
            ts_start INTEGER,
            ts_end INTEGER,
            text TEXT
        );

        CREATE INDEX idx_windows_conversation
        ON message_windows(conversation_id);

        CREATE INDEX idx_windows_ts
        ON message_windows(ts_start);
        """
    )


def insert_metadata(
    conn: sqlite3.Connection,
    *,
    provider_id: str,
) -> None:
    conn.execute(
        """
        INSERT INTO metadata (schema_version, provider_id)
        VALUES (?, ?)
        """,
        (SQLITE_SCHEMA_VERSION, provider_id),
    )
