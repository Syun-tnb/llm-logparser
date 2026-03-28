from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from .analyzer_semantic_preview import (
    SemanticPreviewError,
    WindowClusterMember,
    WindowPreviewRecord,
    load_window_cluster_index,
    load_window_preview_index,
)

WindowRef = tuple[str, str]

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

JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


class SemanticTopicError(RuntimeError):
    pass


@dataclass(frozen=True)
class TopicClusterInput:
    cluster_id: str
    cluster_size: int
    conversation_count: int
    windows: tuple[dict[str, str], ...]


def _normalize_text(text: str, *, max_chars: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3] + "..."


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


def _cluster_windows(
    *,
    members: list[WindowClusterMember],
    windows: dict[WindowRef, WindowPreviewRecord],
    window_cap: int,
    max_window_chars: int,
) -> tuple[dict[str, str], ...]:
    candidates: list[tuple[int, str, str, str]] = []
    for member in members:
        key = (member.conversation_id, member.window_id)
        record = windows.get(key)
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

    deduped: list[dict[str, str]] = []
    seen_hashes: set[str] = set()
    for _neg_char_count, conversation_id, window_id, text in sorted(candidates):
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if text_hash in seen_hashes:
            continue
        seen_hashes.add(text_hash)
        deduped.append(
            {
                "conversation_id": conversation_id,
                "window_id": window_id,
                "text": text,
            }
        )
        if len(deduped) == window_cap:
            break
    return tuple(deduped)


def _build_topic_clusters(
    *,
    input_root: Path,
    cluster_id: str | None,
    top_clusters: int,
    min_cluster_size: int,
    cross_thread_only: bool,
    window_cap: int,
    max_window_chars: int,
) -> list[TopicClusterInput]:
    try:
        windows = load_window_preview_index(input_root)
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
    for item_cluster_id, members in items:
        topic_windows = _cluster_windows(
            members=members,
            windows=windows,
            window_cap=window_cap,
            max_window_chars=max_window_chars,
        )
        if not topic_windows:
            continue
        topic_clusters.append(
            TopicClusterInput(
                cluster_id=item_cluster_id,
                cluster_size=len(members),
                conversation_count=len({member.conversation_id for member in members}),
                windows=topic_windows,
            )
        )
    if not topic_clusters:
        raise SemanticTopicError("matched clusters did not have usable window text")
    return topic_clusters


def _build_prompt(cluster: TopicClusterInput) -> str:
    windows_block = "\n\n".join(
        f"[{row['conversation_id']} / {row['window_id']}]\n{row['text']}"
        for row in cluster.windows
    )
    return (
        f"{TOPIC_PROMPT_TEMPLATE}\n\n"
        f"Cluster ID: {cluster.cluster_id}\n"
        f"Cluster size: {cluster.cluster_size}\n"
        f"Conversation count: {cluster.conversation_count}\n\n"
        f"Messages:\n{windows_block}\n"
    )


def _decode_error_body(exc: urllib_error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8").strip()
    except Exception:
        body = ""
    if not body:
        return ""
    return f" ({body})"


def _call_ollama(
    *,
    model: str,
    prompt: str,
    base_url: str,
    timeout_seconds: float,
) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.0,
            "num_predict": DEFAULT_OLLAMA_NUM_PREDICT,
        },
    }
    request = urllib_request.Request(
        f"{base_url.rstrip('/')}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(request, timeout=timeout_seconds) as response:
            raw_response = response.read().decode("utf-8")
    except urllib_error.HTTPError as exc:
        detail = _decode_error_body(exc)
        raise SemanticTopicError(
            f"ollama topic request failed for model '{model}': HTTP {exc.code}{detail}"
        ) from exc
    except urllib_error.URLError as exc:
        raise SemanticTopicError(
            "ollama topic backend is unavailable at "
            f"{base_url.rstrip('/')}/api/generate: {exc.reason}"
        ) from exc

    try:
        payload = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise SemanticTopicError("ollama topic response was not valid JSON") from exc

    response_text = payload.get("response")
    if not isinstance(response_text, str) or not response_text.strip():
        raise SemanticTopicError("ollama topic response is missing 'response' text")
    return response_text.strip()


def _parse_topic_output(raw_output: str) -> dict[str, Any]:
    stripped = raw_output.strip()
    candidates = [stripped]
    match = JSON_BLOCK_RE.search(stripped)
    if match is not None and match.group(0) != stripped:
        candidates.insert(0, match.group(0))

    payload: dict[str, Any] | None = None
    for candidate in candidates:
        try:
            loaded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            payload = loaded
            break
    if payload is None:
        raise SemanticTopicError("topic response was not valid JSON")
    return payload


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
        "topic_label": " ".join(topic_label.split()),
        "summary": " ".join(summary.split()),
        "keywords": _normalize_keywords(output.get("keywords")),
        "representative_windows": list(cluster.windows[:3]),
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
    )

    topics: list[dict[str, Any]] = []
    for cluster in clusters:
        raw_output = _call_ollama(
            model=model.strip(),
            prompt=_build_prompt(cluster),
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )
        parsed = _parse_topic_output(raw_output)
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
        lines.append(f"Label: {topic['topic_label']}")
        lines.append(f"Summary: {topic['summary']}")
        keywords = topic["keywords"]
        lines.append(f"Keywords: {', '.join(keywords) if keywords else '(none)'}")
        lines.append("")
        lines.append("Representative:")
        for row in topic["representative_windows"]:
            lines.append(
                f"- [{row['conversation_id']} / {row['window_id']}] "
                f"\"{row['text']}\""
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
    )
    if json_output:
        return json.dumps(result, ensure_ascii=False, indent=2)
    return _render_text(result)
