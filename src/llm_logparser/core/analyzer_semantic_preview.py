from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schema_validation import (
    load_message_windows_validator,
    load_window_neighbors_validator,
)


class SemanticPreviewError(RuntimeError):
    pass


@dataclass(frozen=True)
class WindowPreviewRecord:
    provider_id: str
    conversation_id: str
    window_id: str
    message_count: int
    char_count: int
    roles: tuple[str, ...]
    text: str


@dataclass(frozen=True)
class WindowNeighborReference:
    provider_id: str
    conversation_id: str
    window_id: str
    score: float


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SemanticPreviewError(
                    f"invalid JSON in {path}:{line_no}: {exc.msg}"
                ) from exc
            if not isinstance(row, dict):
                raise SemanticPreviewError(
                    f"invalid record in {path}:{line_no}: expected object"
                )
            rows.append(row)
    return rows


def _discover_artifacts(root: Path, name: str) -> list[Path]:
    return sorted(root.rglob(name))


def load_window_preview_index(input_root: Path) -> dict[tuple[str, str], WindowPreviewRecord]:
    validator = load_message_windows_validator()
    index: dict[tuple[str, str], WindowPreviewRecord] = {}

    for path in _discover_artifacts(input_root, "message_windows.jsonl"):
        for line_no, row in enumerate(_load_jsonl(path), start=1):
            errors = list(validator.iter_errors(row))
            if errors:
                raise SemanticPreviewError(
                    f"message window schema validation failed for "
                    f"{path}:{line_no}: {errors[0].message}"
                )
            key = (row["conversation_id"], row["window_id"])
            index[key] = WindowPreviewRecord(
                provider_id=row["provider_id"],
                conversation_id=row["conversation_id"],
                window_id=row["window_id"],
                message_count=row["message_count"],
                char_count=row["char_count"],
                roles=tuple(row["roles"]),
                text=row["text"],
            )
    return index


def load_window_neighbor_index(
    input_root: Path,
) -> dict[tuple[str, str], list[WindowNeighborReference]]:
    validator = load_window_neighbors_validator()
    index: dict[tuple[str, str], list[WindowNeighborReference]] = {}

    for path in _discover_artifacts(input_root, "window_neighbors.jsonl"):
        for line_no, row in enumerate(_load_jsonl(path), start=1):
            errors = list(validator.iter_errors(row))
            if errors:
                raise SemanticPreviewError(
                    f"window neighbor schema validation failed for "
                    f"{path}:{line_no}: {errors[0].message}"
                )
            key = (row["conversation_id"], row["window_id"])
            index[key] = [
                WindowNeighborReference(
                    provider_id=neighbor["provider_id"],
                    conversation_id=neighbor["conversation_id"],
                    window_id=neighbor["window_id"],
                    score=float(neighbor["score"]),
                )
                for neighbor in row["neighbors"]
            ]
    return index


def estimate_turn_count(record: WindowPreviewRecord) -> int:
    user_turns = sum(1 for role in record.roles if role == "user")
    return user_turns if user_turns > 0 else record.message_count


def truncate_text(text: str, *, max_chars: int) -> str:
    if max_chars <= 0:
        raise ValueError("max_chars must be > 0")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _display_role(role: str) -> str:
    lowered = role.strip().lower()
    if not lowered:
        return "Unknown"
    return lowered[:1].upper() + lowered[1:]


def format_window_text(record: WindowPreviewRecord) -> str:
    if not record.text:
        return ""

    parts = record.text.split("\n\n")
    formatted_parts: list[str] = []
    for index, part in enumerate(parts):
        role = record.roles[index] if index < len(record.roles) else "unknown"
        body = part
        prefix = f"{role}:"
        if body.casefold().startswith(prefix.casefold()):
            body = body[len(prefix) :].lstrip()
        formatted_parts.append(f"[{_display_role(role)}] {body}")
    return "\n\n".join(formatted_parts)


def render_semantic_preview(
    *,
    input_root: Path,
    conversation_id: str,
    window_id: str,
    top_k: int | None = None,
    include_text: bool = True,
    max_chars: int = 400,
    show_meta: bool = True,
) -> str:
    if top_k is not None and top_k <= 0:
        raise SemanticPreviewError("top_k must be > 0")
    if max_chars <= 0:
        raise SemanticPreviewError("max_chars must be > 0")

    windows = load_window_preview_index(input_root)
    target_key = (conversation_id, window_id)
    target = windows.get(target_key)
    if target is None:
        raise SemanticPreviewError(
            f"window not found: conversation_id={conversation_id} window_id={window_id}"
        )

    neighbors_by_window = load_window_neighbor_index(input_root)
    neighbor_refs = neighbors_by_window.get(target_key, [])
    if top_k is not None:
        neighbor_refs = neighbor_refs[:top_k]

    lines = [f"### Window: {target.window_id} ({target.conversation_id})", ""]
    if show_meta:
        lines.extend(_render_meta_block(target))
        lines.extend(["", "---", ""])
    if include_text:
        lines.extend(
            [
                "[Content]",
                truncate_text(format_window_text(target), max_chars=max_chars)
                if target.text
                else "",
                "",
                "---",
                "",
            ]
        )

    lines.extend(["### Neighbors", ""])
    if not neighbor_refs:
        lines.append("No neighbors found")
        return "\n".join(lines)

    for index, neighbor in enumerate(neighbor_refs, start=1):
        lines.append(f"({index}) score: {neighbor.score:.4f}")
        lines.append(f"{neighbor.conversation_id} / {neighbor.window_id}")
        lines.append("")
        neighbor_record = windows.get((neighbor.conversation_id, neighbor.window_id))
        if neighbor_record is None:
            lines.append("[Meta]")
            lines.append("Turns: ?")
            lines.append("Messages: ?")
            lines.append("Chars: ?")
        elif show_meta:
            lines.extend(_render_meta_block(neighbor_record))
        if include_text:
            lines.append("")
            if neighbor_record is None or not neighbor_record.text:
                lines.append("")
            else:
                lines.append(
                    truncate_text(
                        format_window_text(neighbor_record),
                        max_chars=max_chars,
                    )
                )
        lines.extend(["", "---", ""])

    return "\n".join(lines).rstrip()


def _render_meta_block(record: WindowPreviewRecord) -> list[str]:
    return [
        "[Meta]",
        f"Turns: {estimate_turn_count(record)}",
        f"Messages: {record.message_count}",
        f"Chars: {record.char_count}",
    ]
