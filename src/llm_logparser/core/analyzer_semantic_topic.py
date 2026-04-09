from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .analyzer_semantic_preview import (
    SemanticPreviewError,
    WindowClusterMember,
    compute_cluster_quality_signals,
    load_window_cluster_index,
    load_window_neighbor_index,
    load_window_preview_index,
    select_representative_cluster_windows,
)
from .llm_client_protocol import LLMClient
from .ollama_client import OllamaClient
from .structured_llm import generate_structured_json
from .semantic_state import (
    aggregate_topic_state,
    classify_span_state,
    semantic_state_dataset_max_timestamp,
)

DEFAULT_TOPIC_PROMPT_VARIANT = "prompt_b"
DEFAULT_TOPIC_TOP_CLUSTERS = 20
DEFAULT_TOPIC_WINDOW_CAP = 8
DEFAULT_TOPIC_MAX_WINDOW_CHARS = 300
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_TIMEOUT_SECONDS = 120.0
DEFAULT_OLLAMA_NUM_PREDICT = 220

TOPIC_PROMPT_TEMPLATE = """You are analyzing a cluster of related conversation snippets.

Tasks:
1. Identify the main topic
2. Provide a concise label (max 5 words)
3. Provide a short summary
4. Extract 3-5 keywords

Return JSON:
{
  "topic_label": "...",
  "summary": "...",
  "keywords": ["..."]
}"""

class SemanticTopicError(RuntimeError):
    pass


@dataclass(frozen=True)
class TopicClusterInput:
    cluster_id: str
    cluster_size: int
    conversation_count: int
    state: str | None
    state_confidence: float | None
    quality_signals: dict[str, Any]
    windows: tuple[dict[str, Any], ...]
    representative_spans: tuple[dict[str, Any], ...]


def _filtered_cluster_items(
    *,
    clusters: dict[str, list[WindowClusterMember]],
    cluster_id: str | None,
    min_cluster_size: int,
    cross_thread_only: bool,
    top_clusters: int,
) -> list[tuple[str, list[WindowClusterMember]]]:
    if cluster_id is not None:
        members = clusters.get(cluster_id)
        if members is None:
            raise SemanticTopicError(f"cluster not found: {cluster_id}")
        candidates = [(cluster_id, members)]
    else:
        candidates = list(clusters.items())

    filtered: list[tuple[str, list[WindowClusterMember]]] = []
    for item_cluster_id, members in candidates:
        if len(members) < min_cluster_size:
            continue
        conversation_count = len({member.conversation_id for member in members})
        if cross_thread_only and conversation_count <= 1:
            continue
        filtered.append((item_cluster_id, members))

    filtered.sort(key=lambda item: (-len(item[1]), item[0]))
    if cluster_id is None:
        return filtered[:top_clusters]
    return filtered


def _build_topic_clusters(
    *,
    input_root: Path,
    cluster_id: str | None,
    top_clusters: int,
    min_cluster_size: int,
    cross_thread_only: bool,
    window_cap: int,
    max_window_chars: int,
    state_locale: str | None = None,
) -> list[TopicClusterInput]:
    try:
        windows = load_window_preview_index(input_root)
        neighbor_index = load_window_neighbor_index(input_root)
        clusters, _cluster_by_window = load_window_cluster_index(input_root)
    except SemanticPreviewError as exc:
        raise SemanticTopicError(str(exc)) from exc
    items = _filtered_cluster_items(
        clusters=clusters,
        cluster_id=cluster_id,
        min_cluster_size=min_cluster_size,
        cross_thread_only=cross_thread_only,
        top_clusters=top_clusters,
    )
    if not items:
        raise SemanticTopicError("no clusters matched the requested filters")

    topic_clusters: list[TopicClusterInput] = []
    dataset_max_ts = semantic_state_dataset_max_timestamp(windows.values())
    for item_cluster_id, members in items:
        topic_windows = select_representative_cluster_windows(
            members=members,
            windows=windows,
            neighbor_index=neighbor_index,
            window_cap=window_cap,
            max_window_chars=max_window_chars,
        )
        if not topic_windows:
            continue
        span_states_by_ref = {
            (member.conversation_id, member.window_id): classify_span_state(
                windows[(member.conversation_id, member.window_id)],
                dataset_max_ts=dataset_max_ts,
                state_locale=state_locale,
            )
            for member in members
            if (member.conversation_id, member.window_id) in windows
        }
        topic_state, topic_state_confidence = aggregate_topic_state(
            span_states_by_ref.values()
        )
        representative_spans: list[dict[str, Any]] = []
        for row in topic_windows[:3]:
            state_row = span_states_by_ref[(row["conversation_id"], row["window_id"])]
            representative_spans.append(
                {
                    "conversation_id": row["conversation_id"],
                    "span_id": row["span_id"],
                    "message_ids": list(row["message_ids"]),
                    "window_id": row["window_id"],
                    "excerpt": row["excerpt"],
                    "state": state_row.state,
                    "state_confidence": state_row.state_confidence,
                    "state_signals": list(state_row.state_signals),
                }
            )
        topic_clusters.append(
            TopicClusterInput(
                cluster_id=item_cluster_id,
                cluster_size=len(members),
                conversation_count=len({member.conversation_id for member in members}),
                state=topic_state,
                state_confidence=topic_state_confidence,
                quality_signals=compute_cluster_quality_signals(
                    members=members,
                    neighbor_index=neighbor_index,
                ),
                windows=topic_windows,
                representative_spans=tuple(representative_spans),
            )
        )
    if not topic_clusters:
        raise SemanticTopicError("matched clusters did not have usable window text")
    return topic_clusters


def _build_prompt(cluster: TopicClusterInput) -> str:
    windows_block = "\n\n".join(
        f"[{row['conversation_id']} / {row['window_id']}]\n{row['excerpt']}"
        for row in cluster.windows
    )
    return (
        f"{TOPIC_PROMPT_TEMPLATE}\n\n"
        f"Cluster ID: {cluster.cluster_id}\n"
        f"Cluster size: {cluster.cluster_size}\n"
        f"Conversation count: {cluster.conversation_count}\n\n"
        f"Messages:\n{windows_block}\n"
    )


def _generate_topic_output(
    *,
    model: str,
    prompt: str,
    base_url: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    try:
        client: LLMClient = OllamaClient(
            base_url=base_url,
            timeout=timeout_seconds,
        )
        return generate_structured_json(
            client,
            model=model,
            prompt=prompt,
            options={
                "temperature": 0.0,
                "num_predict": DEFAULT_OLLAMA_NUM_PREDICT,
            },
        )
    except RuntimeError as exc:
        raise SemanticTopicError(str(exc)) from exc


def _normalize_keywords(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    keywords: list[str] = []
    seen: set[str] = set()
    for item in value:
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
    return keywords


def _topic_payload(
    *,
    cluster: TopicClusterInput,
    output: dict[str, Any],
) -> dict[str, Any]:
    topic_label = output.get("topic_label")
    summary = output.get("summary")
    if not isinstance(topic_label, str) or not topic_label.strip():
        raise SemanticTopicError(
            f"topic response for {cluster.cluster_id} is missing 'topic_label'"
        )
    if not isinstance(summary, str) or not summary.strip():
        raise SemanticTopicError(
            f"topic response for {cluster.cluster_id} is missing 'summary'"
        )
    return {
        "cluster_id": cluster.cluster_id,
        "cluster_size": cluster.cluster_size,
        "conversation_count": cluster.conversation_count,
        "state": cluster.state,
        "state_confidence": cluster.state_confidence,
        "quality_signals": cluster.quality_signals,
        "topic_label": " ".join(topic_label.split()),
        "summary": " ".join(summary.split()),
        "keywords": _normalize_keywords(output.get("keywords")),
        "representative_spans": list(cluster.representative_spans[:3]),
        "representative_windows": [
            {
                "conversation_id": row["conversation_id"],
                "window_id": row["window_id"],
                "excerpt": row["excerpt"],
            }
            for row in cluster.windows[:3]
        ],
    }


def analyze_semantic_topic(
    input_root: Path,
    *,
    model: str,
    cluster_id: str | None = None,
    top_clusters: int = DEFAULT_TOPIC_TOP_CLUSTERS,
    min_cluster_size: int = 1,
    cross_thread_only: bool = False,
    base_url: str = DEFAULT_OLLAMA_BASE_URL,
    timeout_seconds: float = DEFAULT_OLLAMA_TIMEOUT_SECONDS,
    state_locale: str | None = None,
) -> dict[str, Any]:
    if not isinstance(model, str) or not model.strip():
        raise SemanticTopicError("--model is required for semantic-topic")
    if top_clusters <= 0:
        raise SemanticTopicError("--top-clusters must be > 0")
    if min_cluster_size <= 0:
        raise SemanticTopicError("--min-cluster-size must be > 0")
    if timeout_seconds <= 0:
        raise SemanticTopicError("--timeout-seconds must be > 0")

    clusters = _build_topic_clusters(
        input_root=input_root,
        cluster_id=cluster_id,
        top_clusters=top_clusters,
        min_cluster_size=min_cluster_size,
        cross_thread_only=cross_thread_only,
        window_cap=DEFAULT_TOPIC_WINDOW_CAP,
        max_window_chars=DEFAULT_TOPIC_MAX_WINDOW_CHARS,
        state_locale=state_locale,
    )

    topics: list[dict[str, Any]] = []
    for cluster in clusters:
        parsed = _generate_topic_output(
            model=model.strip(),
            prompt=_build_prompt(cluster),
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )
        topics.append(_topic_payload(cluster=cluster, output=parsed))

    return {
        "input_root": str(input_root.resolve()),
        "model": model.strip(),
        "prompt_variant": DEFAULT_TOPIC_PROMPT_VARIANT,
        "window_cap": DEFAULT_TOPIC_WINDOW_CAP,
        "max_window_chars": DEFAULT_TOPIC_MAX_WINDOW_CHARS,
        "cluster_count": len(topics),
        "topics": topics,
    }


def _render_text(result: dict[str, Any]) -> str:
    lines: list[str] = []
    for index, topic in enumerate(result["topics"]):
        if index > 0:
            lines.append("")
        lines.append(f"Cluster {topic['cluster_id']}")
        lines.append(f"size: {topic['cluster_size']}")
        lines.append(f"threads: {topic['conversation_count']}")
        lines.append(
            "State: "
            f"{topic['state'] or '?'}"
            + (
                f" ({topic['state_confidence']:.2f})"
                if isinstance(topic.get("state_confidence"), (int, float))
                else ""
            )
        )
        lines.append(f"Label: {topic['topic_label']}")
        lines.append(f"Summary: {topic['summary']}")
        keywords = topic["keywords"]
        lines.append(f"Keywords: {', '.join(keywords) if keywords else '(none)'}")
        lines.append("")
        lines.append("Representative:")
        for row in topic["representative_windows"]:
            lines.append(
                f"- [{row['conversation_id']} / {row['window_id']}] "
                f"\"{row['excerpt']}\""
            )
    return "\n".join(lines)


def render_semantic_topic(
    *,
    input_root: Path,
    model: str,
    cluster_id: str | None = None,
    top_clusters: int = DEFAULT_TOPIC_TOP_CLUSTERS,
    min_cluster_size: int = 1,
    cross_thread_only: bool = False,
    base_url: str = DEFAULT_OLLAMA_BASE_URL,
    timeout_seconds: float = DEFAULT_OLLAMA_TIMEOUT_SECONDS,
    state_locale: str | None = None,
    json_output: bool = False,
) -> str:
    result = analyze_semantic_topic(
        input_root,
        model=model,
        cluster_id=cluster_id,
        top_clusters=top_clusters,
        min_cluster_size=min_cluster_size,
        cross_thread_only=cross_thread_only,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        state_locale=state_locale,
    )
    if json_output:
        return json.dumps(result, ensure_ascii=False, indent=2)
    return _render_text(result)
