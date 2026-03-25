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


def _display_turn_role(role: str) -> str:
    lowered = role.strip().lower()
    if lowered == "user":
        return "U"
    if lowered == "assistant":
        return "A"
    if not lowered:
        return "?"
    return lowered[:1].upper()


def _window_messages(record: WindowPreviewRecord) -> list[tuple[str, str]]:
    if not record.text:
        return []

    parts = record.text.split("\n\n")
    message_count = max(len(parts), len(record.roles))
    messages: list[tuple[str, str]] = []
    for index in range(message_count):
        role = record.roles[index] if index < len(record.roles) else "unknown"
        body = parts[index] if index < len(parts) else ""
        prefix = f"{role}:"
        if body.casefold().startswith(prefix.casefold()):
            body = body[len(prefix) :].lstrip()
        messages.append((role, body))
    return messages


def format_window_turns(record: WindowPreviewRecord) -> str:
    messages = _window_messages(record)
    if not messages:
        return ""

    turns: list[list[tuple[str, str]]] = []
    current_turn: list[tuple[str, str]] = []

    def _flush_current_turn() -> None:
        if current_turn:
            turns.append(current_turn.copy())
            current_turn.clear()

    for role, body in messages:
        if role == "user":
            _flush_current_turn()
            current_turn.append(("U", body))
            continue
        if not current_turn:
            current_turn.append((_display_turn_role(role), body))
            continue
        current_turn.append((_display_turn_role(role), body))

    _flush_current_turn()

    lines: list[str] = []
    for index, turn in enumerate(turns, start=1):
        lines.append(f"Turn {index}")
        for label, body in turn:
            lines.append(f"{label}: {body}")
        if index != len(turns):
            lines.append("")
    return "\n".join(lines)


def _similarity_label(score: float) -> str:
    if score >= 0.90:
        return "🔥 almost same"
    if score >= 0.80:
        return "👍 very similar"
    if score >= 0.65:
        return "🤝 related"
    return "... weak"


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

    lines = ["=== Target Window ===", f"[{target.window_id}]", ""]
    if show_meta:
        lines.extend(_render_meta_block(target))
        lines.extend(["", "---", ""])
    if include_text:
        lines.append(
            truncate_text(format_window_turns(target), max_chars=max_chars)
            if target.text
            else ""
        )
        lines.extend(["", "---", ""])

    lines.extend(["=== Top Neighbors ===", ""])
    if not neighbor_refs:
        lines.append("No neighbors found")
        return "\n".join(lines)

    for index, neighbor in enumerate(neighbor_refs, start=1):
        lines.append(
            f"#{index} ({neighbor.score:.2f} {_similarity_label(neighbor.score)})"
        )
        lines.append(f"[{neighbor.window_id}]")
        lines.append("")
        neighbor_record = windows.get((neighbor.conversation_id, neighbor.window_id))
        if neighbor_record is None:
            if show_meta:
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
                        format_window_turns(neighbor_record),
                        max_chars=max_chars,
                    )
                )
        lines.extend(["", "---", ""])

    top_neighbor = neighbor_refs[0]
    lines.extend(
        [
            "NEXT",
            f"thread: {top_neighbor.conversation_id}",
            f"window: {top_neighbor.window_id}",
        ]
    )

    return "\n".join(lines).rstrip()


def _render_meta_block(record: WindowPreviewRecord) -> list[str]:
    return [
        "[Meta]",
        f"Turns: {estimate_turn_count(record)}",
        f"Messages: {record.message_count}",
        f"Chars: {record.char_count}",
    ]
