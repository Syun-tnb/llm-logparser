from __future__ import annotations

import sqlite3


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE threads (
            provider_id TEXT NOT NULL,
            conversation_id TEXT PRIMARY KEY,
            message_count INTEGER,
            user_messages INTEGER,
            assistant_messages INTEGER,
            other_roles INTEGER,
            characters_total INTEGER,
            first_timestamp INTEGER,
            last_timestamp INTEGER
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
