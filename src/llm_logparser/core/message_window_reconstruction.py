from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .message_windows import (
    DEFAULT_MESSAGE_WINDOW_SIZE,
    DEFAULT_MESSAGE_WINDOW_STRIDE,
    iter_message_windows,
)
from .l1_derivation import canonical_role_or_unknown, iter_message_records, message_text
from .schema_validation import load_message_windows_validator


@dataclass(frozen=True)
class ReconstructedWindowMessage:
    message_id: str
    role: str
    text: str
    ts: int | None


@dataclass(frozen=True)
class ReconstructedMessageWindow:
    source_path: Path
    parsed_path: Path
    provider_id: str
    conversation_id: str
    window_id: str
    message_ids: tuple[str, ...]
    window_size: int
    window_stride: int
    char_count: int
    ts_start: int | None
    ts_end: int | None
    messages: tuple[ReconstructedWindowMessage, ...]

    @property
    def message_count(self) -> int:
        return len(self.message_ids)

    @property
    def roles(self) -> tuple[str, ...]:
        return tuple(message.role for message in self.messages)

    @property
    def message_texts(self) -> tuple[str, ...]:
        return tuple(message.text for message in self.messages)

    @property
    def text(self) -> str:
        return "\n\n".join(text for text in self.message_texts if text)


def parsed_path_for_message_windows(windows_path: Path) -> Path:
    parsed_path = windows_path.with_name("parsed.jsonl")
    if not parsed_path.exists() or not parsed_path.is_file():
        raise FileNotFoundError(
            f"parsed.jsonl not found next to message_windows.jsonl: {windows_path}"
        )
    return parsed_path


def _load_jsonl_rows(path: Path) -> list[tuple[int, dict[str, Any]]]:
    rows: list[tuple[int, dict[str, Any]]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON in {path}:{line_no}: {exc.msg}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"invalid record in {path}:{line_no}: expected object")
            rows.append((line_no, row))
    return rows


def _load_parsed_message_index(parsed_path: Path) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in iter_message_records(parsed_path):
        message_id = row.get("message_id")
        if not isinstance(message_id, str) or not message_id:
            continue
        index[message_id] = row
    return index


def _message_timestamp(row: dict[str, Any]) -> int | None:
    ts = row.get("ts")
    return ts if isinstance(ts, int) else None


def _reconstruct_messages(
    *,
    windows_path: Path,
    parsed_path: Path,
    line_no: int,
    provider_id: str,
    conversation_id: str,
    message_ids: tuple[str, ...],
    message_index: dict[str, dict[str, Any]],
) -> tuple[ReconstructedWindowMessage, ...]:
    messages: list[ReconstructedWindowMessage] = []
    for message_id in message_ids:
        row = message_index.get(message_id)
        if row is None:
            raise ValueError(
                f"message window references unknown message_id in "
                f"{windows_path}:{line_no}: {message_id}"
            )
        if row.get("provider_id") != provider_id:
            raise ValueError(
                f"message window provider mismatch in {windows_path}:{line_no}: "
                f"{message_id} belongs to {row.get('provider_id')!r}"
            )
        if row.get("conversation_id") != conversation_id:
            raise ValueError(
                f"message window conversation mismatch in {windows_path}:{line_no}: "
                f"{message_id} belongs to {row.get('conversation_id')!r}"
            )
        messages.append(
            ReconstructedWindowMessage(
                message_id=message_id,
                role=canonical_role_or_unknown(row.get("role")),
                text=message_text(row),
                ts=_message_timestamp(row),
            )
        )
    return tuple(messages)


def _validate_provenance(
    *,
    windows_path: Path,
    line_no: int,
    row: dict[str, Any],
    messages: tuple[ReconstructedWindowMessage, ...],
) -> None:
    expected_char_count = sum(len(message.text) for message in messages)
    if row["char_count"] != expected_char_count:
        raise ValueError(
            f"message window char_count mismatch in {windows_path}:{line_no}: "
            f"expected {expected_char_count}, got {row['char_count']}"
        )


def load_reconstructed_message_windows(
    windows_path: Path,
) -> list[ReconstructedMessageWindow]:
    validator = load_message_windows_validator()
    parsed_path = parsed_path_for_message_windows(windows_path)
    message_index = _load_parsed_message_index(parsed_path)
    windows: list[ReconstructedMessageWindow] = []

    for line_no, row in _load_jsonl_rows(windows_path):
        errors = list(validator.iter_errors(row))
        if errors:
            raise ValueError(
                f"message window schema validation failed for "
                f"{windows_path}:{line_no}: {errors[0].message}"
            )
        message_ids = tuple(str(message_id) for message_id in row["message_ids"])
        messages = _reconstruct_messages(
            windows_path=windows_path,
            parsed_path=parsed_path,
            line_no=line_no,
            provider_id=row["provider_id"],
            conversation_id=row["conversation_id"],
            message_ids=message_ids,
            message_index=message_index,
        )
        _validate_provenance(
            windows_path=windows_path,
            line_no=line_no,
            row=row,
            messages=messages,
        )
        windows.append(
            ReconstructedMessageWindow(
                source_path=windows_path,
                parsed_path=parsed_path,
                provider_id=row["provider_id"],
                conversation_id=row["conversation_id"],
                window_id=row["window_id"],
                message_ids=message_ids,
                window_size=row["window_size"],
                window_stride=row["window_stride"],
                char_count=row["char_count"],
                ts_start=row["ts_start"],
                ts_end=row["ts_end"],
                messages=messages,
            )
        )

    return windows


def load_reconstructed_message_windows_from_parsed(
    parsed_path: Path,
    *,
    window_size: int = DEFAULT_MESSAGE_WINDOW_SIZE,
    window_stride: int | None = DEFAULT_MESSAGE_WINDOW_STRIDE,
) -> list[ReconstructedMessageWindow]:
    message_index = _load_parsed_message_index(parsed_path)
    windows: list[ReconstructedMessageWindow] = []

    for row in iter_message_windows(
        parsed_path,
        window_size=window_size,
        window_stride=window_stride,
    ):
        message_ids = tuple(str(message_id) for message_id in row["message_ids"])
        messages = _reconstruct_messages(
            windows_path=parsed_path,
            parsed_path=parsed_path,
            line_no=0,
            provider_id=row["provider_id"],
            conversation_id=row["conversation_id"],
            message_ids=message_ids,
            message_index=message_index,
        )
        _validate_provenance(
            windows_path=parsed_path,
            line_no=0,
            row=row,
            messages=messages,
        )
        windows.append(
            ReconstructedMessageWindow(
                source_path=parsed_path,
                parsed_path=parsed_path,
                provider_id=row["provider_id"],
                conversation_id=row["conversation_id"],
                window_id=row["window_id"],
                message_ids=message_ids,
                window_size=row["window_size"],
                window_stride=row["window_stride"],
                char_count=row["char_count"],
                ts_start=row["ts_start"],
                ts_end=row["ts_end"],
                messages=messages,
            )
        )

    return windows
