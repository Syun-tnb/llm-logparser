from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

ROLE_ORDER = ("user", "assistant", "system", "tool")
UNKNOWN_ROLE = "unknown"


def discover_parsed_jsonl(input_path: Path) -> list[Path]:
    """Return canonical parsed JSONL files from a file or directory input."""
    path = input_path.expanduser()
    if not path.exists():
        raise FileNotFoundError(f"input not found: {path}")
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise FileNotFoundError(f"input not found: {path}")

    parsed_files = sorted(path.rglob("parsed.jsonl"))
    if not parsed_files:
        raise FileNotFoundError(f"no parsed.jsonl found under: {path}")
    return parsed_files


def iter_parsed_records(parsed_path: Path) -> Iterator[dict[str, Any]]:
    """Yield canonical parsed.jsonl records from a single thread file."""
    with parsed_path.open("r", encoding="utf-8") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid JSON in {parsed_path}:{line_no}: {exc.msg}"
                ) from exc
            if not isinstance(row, dict):
                raise ValueError(
                    f"invalid record in {parsed_path}:{line_no}: expected object"
                )
            yield row


def iter_message_records(parsed_path: Path) -> Iterator[dict[str, Any]]:
    """Yield canonical message rows from a single parsed.jsonl file."""
    for row in iter_parsed_records(parsed_path):
        if row.get("record_type") == "message":
            yield row


def iter_input_message_records(input_path: Path) -> Iterator[dict[str, Any]]:
    """Yield canonical message rows for a parsed.jsonl file or directory tree."""
    for parsed_path in discover_parsed_jsonl(input_path):
        yield from iter_message_records(parsed_path)


def ts_to_seconds(ts: Any) -> float | None:
    if not isinstance(ts, (int, float)):
        return None
    value = float(ts)
    return value / 1000.0 if value >= 1e11 else value


def to_iso_utc(ts: float | None) -> str | None:
    if ts is None:
        return None
    return (
        datetime.fromtimestamp(ts, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def span_seconds(start: float | None, end: float | None) -> int | None:
    if start is None or end is None:
        return None
    return int(end - start)


def message_text(row: dict[str, Any]) -> str:
    value = row.get("text")
    return value if isinstance(value, str) else ""


def message_character_count(row: dict[str, Any]) -> int:
    return len(message_text(row))


def normalize_role_value(value: Any) -> str:
    """Return the canonical analyzer role label.

    Analyzer artifacts normalize the small standard role set to lowercase and
    collapse everything else, including empty/missing values, to `unknown`.
    Some L1 consumers still need the raw provider role string for display or
    pass-through indexing, so that path stays separate in `message_role()`.
    """
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ROLE_ORDER:
            return normalized
    return UNKNOWN_ROLE


def message_role(row: dict[str, Any]) -> str | None:
    """Return the raw provider role string when present.

    This is intentionally distinct from `normalize_role_value()`. Raw access is
    still used by thread stats, timeline text, and SQLite/message-window
    sidecars where preserving the provider-emitted role label is desirable.
    """
    role = row.get("role")
    return role if isinstance(role, str) and role else None


@dataclass
class ThreadMetrics:
    conversation_id: str
    message_count: int = 0
    character_count: int = 0
    user_messages: int = 0
    assistant_messages: int = 0
    other_roles: int = 0
    characters_user: int = 0
    characters_assistant: int = 0
    other_role_breakdown: dict[str, int] | None = None
    first_ts: float | None = None
    last_ts: float | None = None

    def add_message(self, row: dict[str, Any]) -> None:
        self.message_count += 1

        char_count = message_character_count(row)
        self.character_count += char_count

        role = message_role(row)
        if role == "user":
            self.user_messages += 1
            self.characters_user += char_count
        elif role == "assistant":
            self.assistant_messages += 1
            self.characters_assistant += char_count
        else:
            self.other_roles += 1
            role_key = role or "unknown"
            if self.other_role_breakdown is None:
                self.other_role_breakdown = {}
            self.other_role_breakdown[role_key] = (
                self.other_role_breakdown.get(role_key, 0) + 1
            )

        ts = ts_to_seconds(row.get("ts"))
        if ts is None:
            return
        self.first_ts = ts if self.first_ts is None else min(self.first_ts, ts)
        self.last_ts = ts if self.last_ts is None else max(self.last_ts, ts)

    def to_detail(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "message_count": self.message_count,
            "character_count": self.character_count,
            "first_timestamp": to_iso_utc(self.first_ts),
            "last_timestamp": to_iso_utc(self.last_ts),
            "conversation_span_seconds": span_seconds(self.first_ts, self.last_ts),
            "user_messages": self.user_messages,
            "assistant_messages": self.assistant_messages,
            "other_roles": self.other_roles,
            "characters_user": self.characters_user,
            "characters_assistant": self.characters_assistant,
        }


def derive_thread_metrics_from_rows(
    rows: Iterable[dict[str, Any]],
    *,
    conversation_id: str | None = None,
) -> ThreadMetrics:
    """Compute cheap deterministic thread-local metrics from canonical rows."""
    derived: ThreadMetrics | None = None
    thread_conversation_id = conversation_id

    for row in rows:
        row_conversation_id = row.get("conversation_id")
        if (
            thread_conversation_id is None
            and isinstance(row_conversation_id, str)
            and row_conversation_id
        ):
            thread_conversation_id = row_conversation_id

        if row.get("record_type") != "message":
            continue

        if derived is None:
            derived = ThreadMetrics(
                conversation_id=thread_conversation_id or "unknown"
            )
        derived.add_message(row)

    if derived is not None:
        return derived
    if thread_conversation_id is None:
        raise ValueError("parsed thread has no records")
    return ThreadMetrics(conversation_id=thread_conversation_id)


def derive_thread_metrics(parsed_path: Path) -> ThreadMetrics:
    """Compute cheap deterministic thread-local metrics from parsed.jsonl."""
    try:
        return derive_thread_metrics_from_rows(iter_parsed_records(parsed_path))
    except ValueError as exc:
        if str(exc) == "parsed thread has no records":
            raise ValueError(f"parsed thread has no records: {parsed_path}") from exc
        raise


def build_thread_stats_artifact(
    metrics: ThreadMetrics,
    *,
    provider_id: str,
) -> dict[str, Any]:
    """Build a deterministic thread-local stats artifact for parse-time reuse."""
    artifact = metrics.to_detail()
    artifact.update(
        {
            "artifact_type": "thread_stats",
            "provider_id": provider_id,
            "other_role_breakdown": dict(sorted((metrics.other_role_breakdown or {}).items())),
        }
    )
    return artifact
