from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .analyzer_semantic_prototype import _derive_span_id
from .message_window_reconstruction import load_reconstructed_message_windows
from .schema_validation import (
    load_window_clusters_validator,
    load_window_neighbors_validator,
)


class SemanticPreviewError(RuntimeError):
    pass


@dataclass(frozen=True)
class WindowPreviewRecord:
    provider_id: str
    conversation_id: str
    window_id: str
    message_ids: tuple[str, ...]
    message_count: int
    char_count: int
    ts_start: int | None
    ts_end: int | None
    roles: tuple[str, ...]
    text: str
    message_texts: tuple[str, ...] = ()
    span_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "span_id",
            _derive_span_id(
                provider_id=self.provider_id,
                conversation_id=self.conversation_id,
                message_ids=self.message_ids,
                window_id=self.window_id,
            ),
        )


@dataclass(frozen=True)
class WindowNeighborReference:
    provider_id: str
    conversation_id: str
    window_id: str
    score: float


@dataclass(frozen=True)
class WindowClusterMember:
    provider_id: str
    conversation_id: str
    window_id: str
    cluster_id: str
    cluster_size: int
    edge_policy: str


WindowRef = tuple[str, str]


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


def load_window_preview_index(input_root: Path) -> dict[WindowRef, WindowPreviewRecord]:
    index: dict[WindowRef, WindowPreviewRecord] = {}
    paths = _discover_artifacts(input_root, "message_windows.jsonl")
    if not paths:
        raise SemanticPreviewError(f"no message_windows.jsonl found under: {input_root}")

    for path in paths:
        try:
            windows = load_reconstructed_message_windows(path)
        except (FileNotFoundError, ValueError) as exc:
            raise SemanticPreviewError(str(exc)) from exc
        for window in windows:
            key = (window.conversation_id, window.window_id)
            index[key] = WindowPreviewRecord(
                provider_id=window.provider_id,
                conversation_id=window.conversation_id,
                window_id=window.window_id,
                message_ids=window.message_ids,
                message_count=window.message_count,
                char_count=window.char_count,
                ts_start=window.ts_start,
                ts_end=window.ts_end,
                roles=window.roles,
                text=window.text,
                message_texts=window.message_texts,
            )
    return index


def load_window_neighbor_index(
    input_root: Path,
) -> dict[WindowRef, list[WindowNeighborReference]]:
    validator = load_window_neighbors_validator()
    index: dict[WindowRef, list[WindowNeighborReference]] = {}

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
                if "score" in neighbor
            ]
    return index


def load_window_cluster_index(
    input_root: Path,
) -> tuple[dict[str, list[WindowClusterMember]], dict[WindowRef, str]]:
    validator = load_window_clusters_validator()
    clusters: dict[str, list[WindowClusterMember]] = defaultdict(list)
    cluster_by_window: dict[WindowRef, str] = {}
    paths = _discover_artifacts(input_root, "window_clusters.jsonl")
    if not paths:
        raise SemanticPreviewError(f"no window_clusters.jsonl found under: {input_root}")

    for path in paths:
        for line_no, row in enumerate(_load_jsonl(path), start=1):
            errors = list(validator.iter_errors(row))
            if errors:
                raise SemanticPreviewError(
                    f"window cluster schema validation failed for "
                    f"{path}:{line_no}: {errors[0].message}"
                )
            member = WindowClusterMember(
                provider_id=row["provider_id"],
                conversation_id=row["conversation_id"],
                window_id=row["window_id"],
                cluster_id=row["cluster_id"],
                cluster_size=int(row["cluster_size"]),
                edge_policy=row["edge_policy"],
            )
            clusters[member.cluster_id].append(member)
            cluster_by_window[(member.conversation_id, member.window_id)] = member.cluster_id

    sorted_clusters = {
        cluster_id: sorted(
            members,
            key=lambda member: (member.conversation_id, member.window_id),
        )
        for cluster_id, members in sorted(clusters.items())
    }
    return sorted_clusters, cluster_by_window


def estimate_turn_count(record: WindowPreviewRecord) -> int:
    user_turns = sum(1 for role in record.roles if role == "user")
    return user_turns if user_turns > 0 else record.message_count


def truncate_text(text: str, *, max_chars: int) -> str:
    if max_chars <= 0:
        raise ValueError("max_chars must be > 0")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _canonical_preview_text(text: str) -> str:
    return " ".join(text.split())


def _representative_window_text(
    record: WindowPreviewRecord,
    *,
    max_chars: int,
) -> str:
    # Presentation-only projection for already selected representative windows.
    return truncate_text(_canonical_preview_text(record.text), max_chars=max_chars)


def _representative_text_key(record: WindowPreviewRecord) -> str:
    # Semantic selection may dedupe identical canonical text, but it must not
    # depend on truncated display excerpts.
    return _canonical_preview_text(record.text)


def _select_representative_window_refs(
    *,
    members: list[WindowClusterMember],
    windows: dict[WindowRef, WindowPreviewRecord],
    neighbor_index: dict[WindowRef, list[WindowNeighborReference]] | None,
    window_cap: int,
) -> tuple[WindowRef, ...]:
    if window_cap <= 0:
        raise ValueError("window_cap must be > 0")

    cluster_keys = {
        (member.conversation_id, member.window_id)
        for member in members
    }
    neighbor_index = neighbor_index or {}
    candidates: list[tuple[int, float, int, int, str, str, str]] = []

    for member in members:
        key = (member.conversation_id, member.window_id)
        record = windows.get(key)
        if record is None:
            continue

        intra_cluster_neighbors = [
            neighbor
            for neighbor in neighbor_index.get(key, [])
            if (neighbor.conversation_id, neighbor.window_id) in cluster_keys
        ]
        link_count = len(intra_cluster_neighbors)
        average_score = (
            sum(neighbor.score for neighbor in intra_cluster_neighbors) / link_count
            if link_count > 0
            else 0.0
        )
        candidates.append(
            (
                -link_count,
                -average_score,
                -record.message_count,
                -record.char_count,
                member.conversation_id,
                member.window_id,
                _representative_text_key(record),
            )
        )

    selected: list[WindowRef] = []
    seen_text_keys: set[str] = set()
    for (
        _neg_link_count,
        _neg_average_score,
        _neg_message_count,
        _neg_char_count,
        conversation_id,
        window_id,
        text_key,
    ) in sorted(candidates):
        if text_key in seen_text_keys:
            continue
        seen_text_keys.add(text_key)
        selected.append((conversation_id, window_id))
        if len(selected) == window_cap:
            break

    return tuple(selected)


def select_representative_cluster_windows(
    *,
    members: list[WindowClusterMember],
    windows: dict[WindowRef, WindowPreviewRecord],
    neighbor_index: dict[WindowRef, list[WindowNeighborReference]] | None,
    window_cap: int,
    max_window_chars: int,
) -> tuple[dict[str, str], ...]:
    if max_window_chars <= 0:
        raise ValueError("max_window_chars must be > 0")

    selected_refs = _select_representative_window_refs(
        members=members,
        windows=windows,
        neighbor_index=neighbor_index,
        window_cap=window_cap,
    )
    rendered: list[dict[str, str]] = []
    for conversation_id, window_id in selected_refs:
        record = windows.get((conversation_id, window_id))
        if record is None:
            continue
        rendered.append(
            {
                "conversation_id": conversation_id,
                "window_id": window_id,
                "text": _representative_window_text(
                    record,
                    max_chars=max_window_chars,
                ),
            }
        )
    return tuple(rendered)


def compute_cluster_quality_signals(
    *,
    members: list[WindowClusterMember],
    neighbor_index: dict[WindowRef, list[WindowNeighborReference]] | None,
) -> dict[str, Any]:
    cluster_keys = {
        (member.conversation_id, member.window_id)
        for member in members
    }
    scores: list[float] = []
    neighbor_index = neighbor_index or {}

    for key in cluster_keys:
        for neighbor in neighbor_index.get(key, []):
            neighbor_key = (neighbor.conversation_id, neighbor.window_id)
            if neighbor_key in cluster_keys and neighbor_key != key:
                scores.append(float(neighbor.score))

    return {
        "cluster_size": len(members),
        "conversation_count": len({member.conversation_id for member in members}),
        "avg_intra_cluster_score": (
            sum(scores) / len(scores)
            if scores
            else None
        ),
        "max_intra_cluster_score": max(scores) if scores else None,
        "single_window": len(members) == 1,
    }


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
    if record.message_texts:
        return [
            (
                record.roles[index] if index < len(record.roles) else "unknown",
                record.message_texts[index],
            )
            for index in range(len(record.message_texts))
        ]

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


def _render_meta_block(record: WindowPreviewRecord) -> list[str]:
    return [
        "[Meta]",
        f"Turns: {estimate_turn_count(record)}",
        f"Messages: {record.message_count}",
        f"Chars: {record.char_count}",
    ]


def _normalize_excerpt(text: str, *, max_chars: int) -> str:
    compact = " ".join(text.split())
    if not compact:
        return ""
    return truncate_text(compact, max_chars=max_chars)


def _window_excerpt(
    windows: dict[WindowRef, WindowPreviewRecord],
    key: WindowRef,
    *,
    max_chars: int,
) -> str:
    record = windows.get(key)
    if record is None:
        return ""
    return _normalize_excerpt(record.text, max_chars=max_chars)


def _cluster_summary_payload(
    *,
    cluster_id: str,
    members: list[WindowClusterMember],
    windows: dict[WindowRef, WindowPreviewRecord],
    excerpt_chars: int,
) -> dict[str, Any]:
    distinct_conversations = sorted({member.conversation_id for member in members})
    representatives: list[dict[str, Any]] = []
    seen_conversations: set[str] = set()
    for member in members:
        key = (member.conversation_id, member.window_id)
        if member.conversation_id in seen_conversations:
            continue
        seen_conversations.add(member.conversation_id)
        representatives.append(
            {
                "conversation_id": member.conversation_id,
                "window_id": member.window_id,
                "excerpt": _window_excerpt(windows, key, max_chars=excerpt_chars),
            }
        )
        if len(representatives) == 3:
            break
    if len(representatives) < 3:
        represented_keys = {
            (row["conversation_id"], row["window_id"]) for row in representatives
        }
        for member in members:
            key = (member.conversation_id, member.window_id)
            if key in represented_keys:
                continue
            representatives.append(
                {
                    "conversation_id": member.conversation_id,
                    "window_id": member.window_id,
                    "excerpt": _window_excerpt(windows, key, max_chars=excerpt_chars),
                }
            )
            if len(representatives) == 3:
                break
    return {
        "cluster_id": cluster_id,
        "cluster_size": len(members),
        "distinct_conversations": len(distinct_conversations),
        "conversation_ids": distinct_conversations,
        "cross_thread": len(distinct_conversations) > 1,
        "representative_windows": representatives,
    }


def _cluster_neighbor_payload(
    *,
    member_key: WindowRef,
    member_keys: set[WindowRef],
    neighbors_by_window: dict[WindowRef, list[WindowNeighborReference]],
    top_k: int | None,
) -> dict[str, Any]:
    refs = [
        ref
        for ref in neighbors_by_window.get(member_key, [])
        if (ref.conversation_id, ref.window_id) in member_keys
    ]
    if top_k is not None:
        refs = refs[:top_k]
    same_thread = sum(1 for ref in refs if ref.conversation_id == member_key[0])
    cross_thread = len(refs) - same_thread
    return {
        "cluster_neighbor_count": len(refs),
        "same_thread_neighbor_count": same_thread,
        "cross_thread_neighbor_count": cross_thread,
        "cluster_neighbors": [
            {
                "conversation_id": ref.conversation_id,
                "window_id": ref.window_id,
                "score": round(ref.score, 4),
                "relationship": (
                    "same-thread"
                    if ref.conversation_id == member_key[0]
                    else "cross-thread"
                ),
            }
            for ref in refs
        ],
    }


def _cluster_detail_payload(
    *,
    cluster_id: str,
    members: list[WindowClusterMember],
    windows: dict[WindowRef, WindowPreviewRecord],
    neighbors_by_window: dict[WindowRef, list[WindowNeighborReference]],
    excerpt_chars: int,
    top_k: int | None,
) -> dict[str, Any]:
    summary = _cluster_summary_payload(
        cluster_id=cluster_id,
        members=members,
        windows=windows,
        excerpt_chars=excerpt_chars,
    )
    member_keys = {(member.conversation_id, member.window_id) for member in members}
    member_rows: list[dict[str, Any]] = []
    for member in members:
        member_key = (member.conversation_id, member.window_id)
        row = {
            "conversation_id": member.conversation_id,
            "window_id": member.window_id,
            "excerpt": _window_excerpt(windows, member_key, max_chars=excerpt_chars),
        }
        if neighbors_by_window:
            row.update(
                _cluster_neighbor_payload(
                    member_key=member_key,
                    member_keys=member_keys,
                    neighbors_by_window=neighbors_by_window,
                    top_k=top_k,
                )
            )
        member_rows.append(row)
    summary["members"] = member_rows
    return summary


def _conversation_cluster_payload(
    *,
    conversation_id: str,
    cluster_id: str,
    members: list[WindowClusterMember],
    windows: dict[WindowRef, WindowPreviewRecord],
    neighbors_by_window: dict[WindowRef, list[WindowNeighborReference]],
    excerpt_chars: int,
    top_k: int | None,
) -> dict[str, Any]:
    summary = _cluster_summary_payload(
        cluster_id=cluster_id,
        members=members,
        windows=windows,
        excerpt_chars=excerpt_chars,
    )
    own_members = [
        member
        for member in members
        if member.conversation_id == conversation_id
    ]
    summary["windows"] = [
        {
            "conversation_id": member.conversation_id,
            "window_id": member.window_id,
            "excerpt": _window_excerpt(
                windows,
                (member.conversation_id, member.window_id),
                max_chars=excerpt_chars,
            ),
        }
        for member in own_members
    ]

    if neighbors_by_window:
        member_keys = {(member.conversation_id, member.window_id) for member in members}
        connection_counts: dict[str, int] = defaultdict(int)
        strongest_scores: dict[str, float] = {}
        for member in own_members:
            local_neighbors = _cluster_neighbor_payload(
                member_key=(member.conversation_id, member.window_id),
                member_keys=member_keys,
                neighbors_by_window=neighbors_by_window,
                top_k=top_k,
            )["cluster_neighbors"]
            for neighbor in local_neighbors:
                if neighbor["conversation_id"] == conversation_id:
                    continue
                target_conversation = str(neighbor["conversation_id"])
                connection_counts[target_conversation] += 1
                strongest_scores[target_conversation] = max(
                    strongest_scores.get(target_conversation, float("-inf")),
                    float(neighbor["score"]),
                )
        summary["cross_thread_connections"] = [
            {
                "conversation_id": target_conversation,
                "link_count": connection_counts[target_conversation],
                "strongest_score": round(strongest_scores[target_conversation], 4),
            }
            for target_conversation in sorted(connection_counts)
        ]
    else:
        summary["cross_thread_connections"] = [
            {
                "conversation_id": target_conversation,
                "member_count": sum(
                    1 for member in members if member.conversation_id == target_conversation
                ),
            }
            for target_conversation in summary["conversation_ids"]
            if target_conversation != conversation_id
        ]

    return summary


def _filtered_cluster_items(
    *,
    clusters: dict[str, list[WindowClusterMember]],
    min_cluster_size: int,
    cross_thread_only: bool,
) -> list[tuple[str, list[WindowClusterMember]]]:
    items: list[tuple[str, list[WindowClusterMember]]] = []
    for cluster_id, members in clusters.items():
        if len(members) < min_cluster_size:
            continue
        if cross_thread_only and len({member.conversation_id for member in members}) <= 1:
            continue
        items.append((cluster_id, members))
    items.sort(key=lambda item: (-len(item[1]), item[0]))
    return items


def _build_window_view_payload(
    *,
    windows: dict[WindowRef, WindowPreviewRecord],
    neighbors_by_window: dict[WindowRef, list[WindowNeighborReference]],
    conversation_id: str,
    window_id: str,
    top_k: int | None,
) -> dict[str, Any]:
    target_key = (conversation_id, window_id)
    target = windows.get(target_key)
    if target is None:
        raise SemanticPreviewError(
            f"window not found: conversation_id={conversation_id} window_id={window_id}"
        )

    neighbor_refs = neighbors_by_window.get(target_key, [])
    if top_k is not None:
        neighbor_refs = neighbor_refs[:top_k]

    empty_record = WindowPreviewRecord(
        provider_id="",
        conversation_id="",
        window_id="",
        message_ids=(),
        message_count=0,
        char_count=0,
        ts_start=None,
        ts_end=None,
        roles=(),
        text="",
    )

    return {
        "view": "window",
        "target": {
            "conversation_id": target.conversation_id,
            "window_id": target.window_id,
            "message_count": target.message_count,
            "char_count": target.char_count,
            "roles": list(target.roles),
            "turn_count": estimate_turn_count(target),
            "text": format_window_turns(target),
        },
        "neighbors": [
            {
                "conversation_id": neighbor.conversation_id,
                "window_id": neighbor.window_id,
                "score": round(neighbor.score, 4),
                "similarity_label": _similarity_label(neighbor.score),
                "message_count": windows.get(
                    (neighbor.conversation_id, neighbor.window_id),
                    empty_record,
                ).message_count,
                "char_count": windows.get(
                    (neighbor.conversation_id, neighbor.window_id),
                    empty_record,
                ).char_count,
                "turn_count": (
                    estimate_turn_count(windows[(neighbor.conversation_id, neighbor.window_id)])
                    if (neighbor.conversation_id, neighbor.window_id) in windows
                    else None
                ),
                "text": (
                    format_window_turns(windows[(neighbor.conversation_id, neighbor.window_id)])
                    if (neighbor.conversation_id, neighbor.window_id) in windows
                    else ""
                ),
            }
            for neighbor in neighbor_refs
        ],
    }


def build_semantic_preview_payload(
    *,
    input_root: Path,
    cluster_id: str | None = None,
    conversation_id: str | None = None,
    window_id: str | None = None,
    top_clusters: int = 20,
    min_cluster_size: int = 1,
    cross_thread_only: bool = False,
    top_k: int | None = None,
    max_chars: int = 400,
) -> dict[str, Any]:
    if top_clusters <= 0:
        raise SemanticPreviewError("top_clusters must be > 0")
    if min_cluster_size <= 0:
        raise SemanticPreviewError("min_cluster_size must be > 0")
    if top_k is not None and top_k <= 0:
        raise SemanticPreviewError("top_k must be > 0")
    if max_chars <= 0:
        raise SemanticPreviewError("max_chars must be > 0")
    if cluster_id and (conversation_id or window_id):
        raise SemanticPreviewError(
            "cluster detail view cannot be combined with conversation or window lookup options"
        )
    if window_id is not None and not conversation_id:
        raise SemanticPreviewError("window lookup requires --conversation-id/--thread")

    windows = load_window_preview_index(input_root)
    neighbors_by_window = load_window_neighbor_index(input_root)
    excerpt_chars = min(max_chars, 120)

    if window_id is not None:
        return _build_window_view_payload(
            windows=windows,
            neighbors_by_window=neighbors_by_window,
            conversation_id=conversation_id,
            window_id=window_id,
            top_k=top_k,
        )

    clusters, _ = load_window_cluster_index(input_root)
    if cluster_id is not None:
        members = clusters.get(cluster_id)
        if members is None:
            raise SemanticPreviewError(f"cluster not found: cluster_id={cluster_id}")
        return {
            "view": "cluster_detail",
            "cluster": _cluster_detail_payload(
                cluster_id=cluster_id,
                members=members,
                windows=windows,
                neighbors_by_window=neighbors_by_window,
                excerpt_chars=excerpt_chars,
                top_k=top_k,
            ),
        }

    filtered_clusters = _filtered_cluster_items(
        clusters=clusters,
        min_cluster_size=min_cluster_size,
        cross_thread_only=cross_thread_only,
    )
    if conversation_id is not None:
        conversation_clusters = [
            (current_cluster_id, members)
            for current_cluster_id, members in filtered_clusters
            if any(member.conversation_id == conversation_id for member in members)
        ]
        if not conversation_clusters:
            raise SemanticPreviewError(
                f"conversation not found in clustered windows: conversation_id={conversation_id}"
            )
        return {
            "view": "conversation",
            "conversation_id": conversation_id,
            "clusters": [
                _conversation_cluster_payload(
                    conversation_id=conversation_id,
                    cluster_id=current_cluster_id,
                    members=members,
                    windows=windows,
                    neighbors_by_window=neighbors_by_window,
                    excerpt_chars=excerpt_chars,
                    top_k=top_k,
                )
                for current_cluster_id, members in conversation_clusters
            ],
        }

    return {
        "view": "cluster_list",
        "clusters": [
            _cluster_summary_payload(
                cluster_id=current_cluster_id,
                members=members,
                windows=windows,
                excerpt_chars=excerpt_chars,
            )
            for current_cluster_id, members in filtered_clusters[:top_clusters]
        ],
    }


def _render_window_text(
    payload: dict[str, Any],
    *,
    include_text: bool,
    max_chars: int,
    show_meta: bool,
) -> str:
    target = payload["target"]
    lines = ["=== Target Window ===", f"[{target['window_id']}]", ""]
    if show_meta:
        lines.extend(
            [
                "[Meta]",
                f"Turns: {target['turn_count']}",
                f"Messages: {target['message_count']}",
                f"Chars: {target['char_count']}",
                "",
                "---",
                "",
            ]
        )
    if include_text:
        lines.append(truncate_text(target["text"], max_chars=max_chars))
        lines.extend(["", "---", ""])

    lines.extend(["=== Top Neighbors ===", ""])
    neighbors = payload["neighbors"]
    if not neighbors:
        lines.append("No neighbors found")
        return "\n".join(lines)

    for index, neighbor in enumerate(neighbors, start=1):
        lines.append(
            f"#{index} ({neighbor['score']:.2f} {neighbor['similarity_label']})"
        )
        lines.append(f"[{neighbor['window_id']}]")
        lines.append("")
        if show_meta:
            turn_count = "?" if neighbor["turn_count"] is None else neighbor["turn_count"]
            message_count = "?" if neighbor["message_count"] == 0 else neighbor["message_count"]
            char_count = "?" if neighbor["char_count"] == 0 else neighbor["char_count"]
            lines.extend(
                [
                    "[Meta]",
                    f"Turns: {turn_count}",
                    f"Messages: {message_count}",
                    f"Chars: {char_count}",
                ]
            )
        if include_text:
            lines.append("")
            lines.append(truncate_text(neighbor["text"], max_chars=max_chars))
        lines.extend(["", "---", ""])

    top_neighbor = neighbors[0]
    lines.extend(
        [
            "NEXT",
            f"thread: {top_neighbor['conversation_id']}",
            f"window: {top_neighbor['window_id']}",
        ]
    )
    return "\n".join(lines).rstrip()


def _render_cluster_list_text(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    clusters = payload["clusters"]
    if not clusters:
        return "No clusters matched the current filters"
    for index, cluster in enumerate(clusters):
        lines.extend(
            [
                f"Cluster {cluster['cluster_id']}",
                f"size: {cluster['cluster_size']}",
                f"threads: {cluster['distinct_conversations']}",
                f"cross-thread: {'yes' if cluster['cross_thread'] else 'no'}",
                "",
                "Representative:",
            ]
        )
        for representative in cluster["representative_windows"]:
            lines.append(
                f"- [{representative['conversation_id']} / {representative['window_id']}] "
                f"\"{representative['excerpt']}\""
            )
        if index != len(clusters) - 1:
            lines.extend(["", "---", ""])
    return "\n".join(lines)


def _render_cluster_detail_text(payload: dict[str, Any]) -> str:
    cluster = payload["cluster"]
    lines = [
        f"Cluster {cluster['cluster_id']}",
        f"size: {cluster['cluster_size']}",
        f"threads: {cluster['distinct_conversations']}",
        f"cross-thread: {'yes' if cluster['cross_thread'] else 'no'}",
        "",
        "Members:",
    ]
    for member in cluster["members"]:
        lines.append(
            f"- [{member['conversation_id']} / {member['window_id']}] "
            f"\"{member['excerpt']}\""
        )
        if "cluster_neighbor_count" in member:
            lines.append(
                f"  cluster-neighbors: {member['cluster_neighbor_count']} "
                f"(same-thread {member['same_thread_neighbor_count']}, "
                f"cross-thread {member['cross_thread_neighbor_count']})"
            )
            if member["cluster_neighbors"]:
                lines.append("  neighbor-scores:")
                for neighbor in member["cluster_neighbors"]:
                    lines.append(
                        f"  - [{neighbor['conversation_id']} / {neighbor['window_id']}] "
                        f"{neighbor['score']:.2f} ({neighbor['relationship']})"
                    )
    return "\n".join(lines)


def _render_conversation_text(payload: dict[str, Any]) -> str:
    lines = [
        f"Conversation {payload['conversation_id']}",
        f"clusters: {len(payload['clusters'])}",
    ]
    if not payload["clusters"]:
        return "\n".join(lines)

    for cluster in payload["clusters"]:
        lines.extend(
            [
                "",
                f"Cluster {cluster['cluster_id']}",
                f"size: {cluster['cluster_size']}",
                f"threads: {cluster['distinct_conversations']}",
                f"cross-thread: {'yes' if cluster['cross_thread'] else 'no'}",
                "Windows:",
            ]
        )
        for window in cluster["windows"]:
            lines.append(
                f"- [{window['conversation_id']} / {window['window_id']}] "
                f"\"{window['excerpt']}\""
            )
        lines.append("Cross-thread connections:")
        if not cluster["cross_thread_connections"]:
            lines.append("- none")
            continue
        for connection in cluster["cross_thread_connections"]:
            if "strongest_score" in connection:
                lines.append(
                    f"- {connection['conversation_id']}: "
                    f"{connection['link_count']} links, strongest {connection['strongest_score']:.2f}"
                )
            else:
                lines.append(
                    f"- {connection['conversation_id']}: "
                    f"{connection['member_count']} cluster members"
                )
    return "\n".join(lines)


def render_semantic_preview(
    *,
    input_root: Path,
    cluster_id: str | None = None,
    conversation_id: str | None = None,
    window_id: str | None = None,
    top_clusters: int = 20,
    min_cluster_size: int = 1,
    cross_thread_only: bool = False,
    json_output: bool = False,
    top_k: int | None = None,
    include_text: bool = True,
    max_chars: int = 400,
    show_meta: bool = True,
) -> str:
    payload = build_semantic_preview_payload(
        input_root=input_root,
        cluster_id=cluster_id,
        conversation_id=conversation_id,
        window_id=window_id,
        top_clusters=top_clusters,
        min_cluster_size=min_cluster_size,
        cross_thread_only=cross_thread_only,
        top_k=top_k,
        max_chars=max_chars,
    )
    if json_output:
        return json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True)
    if payload["view"] == "window":
        return _render_window_text(
            payload,
            include_text=include_text,
            max_chars=max_chars,
            show_meta=show_meta,
        )
    if payload["view"] == "cluster_detail":
        return _render_cluster_detail_text(payload)
    if payload["view"] == "conversation":
        return _render_conversation_text(payload)
    if payload["view"] == "cluster_list":
        return _render_cluster_list_text(payload)
    raise SemanticPreviewError(f"unsupported preview view: {payload['view']}")
