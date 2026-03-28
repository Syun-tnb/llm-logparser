from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .analyzer_semantic_preview import (
    SemanticPreviewError,
    WindowClusterMember,
    WindowPreviewRecord,
    load_window_cluster_index,
    load_window_preview_index,
)
from .analyzer_semantic_topic import (
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_OLLAMA_TIMEOUT_SECONDS,
    DEFAULT_TOPIC_MAX_WINDOW_CHARS,
    DEFAULT_TOPIC_PROMPT_VARIANT,
    DEFAULT_TOPIC_WINDOW_CAP,
    SemanticTopicError,
    TOPIC_PROMPT_TEMPLATE,
    _call_ollama,
    _parse_topic_output,
)
from .schema_validation import (
    load_topic_membership_validator,
    load_topics_validator,
)

WindowRef = tuple[str, str]


class SemanticTopicsError(RuntimeError):
    pass


def _normalize_text(text: str, *, max_chars: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3] + "..."


def _selected_cluster_items(
    *,
    clusters: dict[str, list[WindowClusterMember]],
    cluster_id: str | None,
    min_cluster_size: int,
    cross_thread_only: bool,
) -> list[tuple[str, list[WindowClusterMember]]]:
    if cluster_id is not None:
        members = clusters.get(cluster_id)
        if members is None:
            raise SemanticTopicsError(f"cluster not found: {cluster_id}")
        candidates = [(cluster_id, members)]
    else:
        candidates = list(clusters.items())

    items: list[tuple[str, list[WindowClusterMember]]] = []
    for item_cluster_id, members in candidates:
        if len(members) < min_cluster_size:
            continue
        conversation_count = len({member.conversation_id for member in members})
        if cross_thread_only and conversation_count <= 1:
            continue
        items.append((item_cluster_id, members))
    items.sort(key=lambda item: (-len(item[1]), item[0]))
    if not items:
        raise SemanticTopicsError("no clusters matched the requested filters")
    return items


def _topic_id(provider_id: str, members: list[WindowClusterMember]) -> str:
    anchors = sorted(
        f"{member.conversation_id}::{member.window_id}"
        for member in members
    )
    digest = hashlib.sha256(
        f"{provider_id}|{'|'.join(anchors)}".encode("utf-8")
    ).hexdigest()
    return f"topic_{digest[:12]}"


def _prompt_windows(
    *,
    members: list[WindowClusterMember],
    windows: dict[WindowRef, WindowPreviewRecord],
    window_cap: int,
    max_window_chars: int,
) -> list[dict[str, str]]:
    candidates: list[tuple[int, str, str, str]] = []
    for member in members:
        record = windows.get((member.conversation_id, member.window_id))
        if record is None:
            continue
        candidates.append(
            (
                -record.char_count,
                member.conversation_id,
                member.window_id,
                _normalize_text(record.text, max_chars=max_window_chars),
            )
        )

    selected: list[dict[str, str]] = []
    seen_hashes: set[str] = set()
    for _neg_char_count, conversation_id, window_id, text in sorted(candidates):
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if text_hash in seen_hashes:
            continue
        seen_hashes.add(text_hash)
        selected.append(
            {
                "conversation_id": conversation_id,
                "window_id": window_id,
                "excerpt": text,
            }
        )
        if len(selected) == window_cap:
            break
    return selected


def _message_refs(
    members: list[WindowClusterMember],
    windows: dict[WindowRef, WindowPreviewRecord],
) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for member in sorted(members, key=lambda item: (item.conversation_id, item.window_id)):
        record = windows.get((member.conversation_id, member.window_id))
        if record is None:
            continue
        for message_id in record.message_ids:
            refs.append(
                {
                    "conversation_id": member.conversation_id,
                    "message_id": message_id,
                }
            )
    return refs


def _window_refs(members: list[WindowClusterMember]) -> list[dict[str, str]]:
    return [
        {
            "conversation_id": member.conversation_id,
            "window_id": member.window_id,
        }
        for member in sorted(members, key=lambda item: (item.conversation_id, item.window_id))
    ]


def _time_bounds(
    members: list[WindowClusterMember],
    windows: dict[WindowRef, WindowPreviewRecord],
) -> tuple[int | None, int | None]:
    values: list[int] = []
    for member in members:
        record = windows.get((member.conversation_id, member.window_id))
        if record is None:
            continue
        if isinstance(record.ts_start, int):
            values.append(record.ts_start)
        if isinstance(record.ts_end, int):
            values.append(record.ts_end)
    if not values:
        return None, None
    return min(values), max(values)


def _topic_prompt(
    *,
    cluster_id: str,
    members: list[WindowClusterMember],
    prompt_windows: list[dict[str, str]],
) -> str:
    windows_block = "\n\n".join(
        f"[{row['conversation_id']} / {row['window_id']}]\n{row['excerpt']}"
        for row in prompt_windows
    )
    return (
        f"{TOPIC_PROMPT_TEMPLATE}\n\n"
        f"Cluster ID: {cluster_id}\n"
        f"Cluster size: {len(members)}\n"
        f"Conversation count: {len({member.conversation_id for member in members})}\n\n"
        f"Messages:\n{windows_block}\n"
    )


def _topic_model_fields(
    *,
    model: str | None,
    cluster_id: str,
    members: list[WindowClusterMember],
    prompt_windows: list[dict[str, str]],
    base_url: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    if model is None:
        return {
            "label": None,
            "summary": None,
            "keywords": [],
            "confidence": None,
        }

    try:
        raw_output = _call_ollama(
            model=model,
            prompt=_topic_prompt(
                cluster_id=cluster_id,
                members=members,
                prompt_windows=prompt_windows,
            ),
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )
        parsed = _parse_topic_output(raw_output)
    except SemanticTopicError as exc:
        raise SemanticTopicsError(str(exc)) from exc

    label = parsed.get("topic_label")
    summary = parsed.get("summary")
    if not isinstance(label, str) or not label.strip():
        raise SemanticTopicsError(
            f"topic response for {cluster_id} is missing 'topic_label'"
        )
    if not isinstance(summary, str) or not summary.strip():
        raise SemanticTopicsError(
            f"topic response for {cluster_id} is missing 'summary'"
        )

    keywords: list[str] = []
    seen: set[str] = set()
    raw_keywords = parsed.get("keywords")
    if isinstance(raw_keywords, list):
        for item in raw_keywords:
            if not isinstance(item, str):
                continue
            normalized = " ".join(item.split()).strip()
            if not normalized:
                continue
            key = normalized.casefold()
            if key in seen:
                continue
            seen.add(key)
            keywords.append(normalized)
            if len(keywords) == 5:
                break

    confidence = parsed.get("confidence")
    if not isinstance(confidence, (int, float)):
        confidence = None

    return {
        "label": " ".join(label.split()),
        "summary": " ".join(summary.split()),
        "keywords": keywords,
        "confidence": float(confidence) if confidence is not None else None,
    }


def build_semantic_topics_artifact(
    input_root: Path,
    *,
    model: str | None = None,
    cluster_id: str | None = None,
    min_cluster_size: int = 1,
    cross_thread_only: bool = False,
    base_url: str = DEFAULT_OLLAMA_BASE_URL,
    timeout_seconds: float = DEFAULT_OLLAMA_TIMEOUT_SECONDS,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if min_cluster_size <= 0:
        raise SemanticTopicsError("--min-cluster-size must be > 0")
    if timeout_seconds <= 0:
        raise SemanticTopicsError("--timeout-seconds must be > 0")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise SemanticTopicsError("--model must be a non-empty string when provided")

    try:
        windows = load_window_preview_index(input_root)
        clusters, _cluster_by_window = load_window_cluster_index(input_root)
    except SemanticPreviewError as exc:
        raise SemanticTopicsError(str(exc)) from exc
    if not windows:
        raise SemanticTopicsError(f"no message_windows.jsonl found under: {input_root}")

    items = _selected_cluster_items(
        clusters=clusters,
        cluster_id=cluster_id,
        min_cluster_size=min_cluster_size,
        cross_thread_only=cross_thread_only,
    )

    provider_id = next(iter(windows.values())).provider_id
    topics: list[dict[str, Any]] = []
    membership_rows: list[dict[str, Any]] = []
    for item_cluster_id, members in items:
        prompt_windows = _prompt_windows(
            members=members,
            windows=windows,
            window_cap=DEFAULT_TOPIC_WINDOW_CAP,
            max_window_chars=DEFAULT_TOPIC_MAX_WINDOW_CHARS,
        )
        if not prompt_windows:
            continue

        first_seen, last_seen = _time_bounds(members, windows)
        topic_id = _topic_id(provider_id, members)
        topic_fields = _topic_model_fields(
            model=model.strip() if isinstance(model, str) else None,
            cluster_id=item_cluster_id,
            members=members,
            prompt_windows=prompt_windows,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )
        topic = {
            "topic_id": topic_id,
            "provider_id": provider_id,
            "cluster_ids": [item_cluster_id],
            "conversation_ids": sorted({member.conversation_id for member in members}),
            "window_refs": _window_refs(members),
            "message_refs": _message_refs(members, windows),
            "cluster_count": 1,
            "window_count": len(members),
            "message_count": sum(
                len(windows[(member.conversation_id, member.window_id)].message_ids)
                for member in members
                if (member.conversation_id, member.window_id) in windows
            ),
            "first_seen": first_seen,
            "last_seen": last_seen,
            "label": topic_fields["label"],
            "summary": topic_fields["summary"],
            "keywords": topic_fields["keywords"],
            "confidence": topic_fields["confidence"],
            "representative_windows": prompt_windows[:3],
        }
        topics.append(topic)
        membership_rows.append(
            {
                "record_type": "topic_membership",
                "schema_version": "0.1",
                "provider_id": provider_id,
                "topic_id": topic_id,
                "membership_type": "cluster",
                "conversation_id": None,
                "cluster_id": item_cluster_id,
                "window_id": None,
                "message_id": None,
            }
        )
        for member in sorted(members, key=lambda item: (item.conversation_id, item.window_id)):
            membership_rows.append(
                {
                    "record_type": "topic_membership",
                    "schema_version": "0.1",
                    "provider_id": provider_id,
                    "topic_id": topic_id,
                    "membership_type": "window",
                    "conversation_id": member.conversation_id,
                    "cluster_id": item_cluster_id,
                    "window_id": member.window_id,
                    "message_id": None,
                }
            )
            record = windows.get((member.conversation_id, member.window_id))
            if record is None:
                continue
            for message_id in record.message_ids:
                membership_rows.append(
                    {
                        "record_type": "topic_membership",
                        "schema_version": "0.1",
                        "provider_id": provider_id,
                        "topic_id": topic_id,
                        "membership_type": "message",
                        "conversation_id": member.conversation_id,
                        "cluster_id": item_cluster_id,
                        "window_id": member.window_id,
                        "message_id": message_id,
                    }
                )

    if not topics:
        raise SemanticTopicsError("matched clusters did not have usable topic inputs")

    artifact = {
        "artifact_type": "semantic_topics",
        "schema_version": "0.1",
        "provider_id": provider_id,
        "topic_count": len(topics),
        "source_inputs": [
            "message_windows.jsonl",
            "window_clusters.jsonl",
        ],
        "generation": {
            "membership_mode": "cluster-is-topic-v1",
            "label_mode": "model-enriched" if model else "structural-only",
            "prompt_variant": DEFAULT_TOPIC_PROMPT_VARIANT if model else None,
            "window_cap": DEFAULT_TOPIC_WINDOW_CAP,
            "max_window_chars": DEFAULT_TOPIC_MAX_WINDOW_CHARS,
            "model": f"ollama/{model.strip()}" if model else None,
            "filters": {
                "cluster_id": cluster_id,
                "min_cluster_size": min_cluster_size,
                "cross_thread_only": cross_thread_only,
            },
        },
        "topics": topics,
    }
    return artifact, membership_rows


def write_semantic_topics_artifacts(
    input_root: Path,
    *,
    model: str | None = None,
    cluster_id: str | None = None,
    min_cluster_size: int = 1,
    cross_thread_only: bool = False,
    base_url: str = DEFAULT_OLLAMA_BASE_URL,
    timeout_seconds: float = DEFAULT_OLLAMA_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    artifact, membership_rows = build_semantic_topics_artifact(
        input_root,
        model=model,
        cluster_id=cluster_id,
        min_cluster_size=min_cluster_size,
        cross_thread_only=cross_thread_only,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
    )

    topics_validator = load_topics_validator()
    topic_errors = list(topics_validator.iter_errors(artifact))
    if topic_errors:
        raise SemanticTopicsError(
            f"topics artifact schema validation failed: {topic_errors[0].message}"
        )

    membership_validator = load_topic_membership_validator()
    for row in membership_rows:
        errors = list(membership_validator.iter_errors(row))
        if errors:
            raise SemanticTopicsError(
                "topic membership artifact schema validation failed: "
                f"{errors[0].message}"
            )

    output_dir = input_root / "l3" / "semantic-topics"
    output_dir.mkdir(parents=True, exist_ok=True)
    topics_path = output_dir / "topics.json"
    membership_path = output_dir / "topic_membership.jsonl"

    topics_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with membership_path.open("w", encoding="utf-8") as handle:
        for row in membership_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    return {
        "provider_id": artifact["provider_id"],
        "topic_count": artifact["topic_count"],
        "topics_path": str(topics_path),
        "membership_path": str(membership_path),
        "label_mode": artifact["generation"]["label_mode"],
    }
