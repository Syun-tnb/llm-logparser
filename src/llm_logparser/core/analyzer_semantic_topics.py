from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any

from .analyzer_semantic_preview import (
    SemanticPreviewError,
    WindowClusterMember,
    WindowPreviewRecord,
    compute_cluster_quality_signals,
    load_window_cluster_index,
    load_window_neighbor_index,
    load_window_preview_index,
    select_representative_cluster_windows,
)
from .analyzer_semantic_topic import (
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_OLLAMA_TIMEOUT_SECONDS,
    DEFAULT_TOPIC_MAX_WINDOW_CHARS,
    DEFAULT_TOPIC_PROMPT_VARIANT,
    DEFAULT_TOPIC_WINDOW_CAP,
    SemanticTopicError,
    TOPIC_PROMPT_TEMPLATE,
    _generate_topic_output,
)
from .llm_client_protocol import LLMClient
from .ollama_client import OllamaClient
from .semantic_normalization import (
    normalize_representative_span,
    semantic_normalization_to_dict,
)
from .semantic_state import (
    SpanStateResult,
    aggregate_topic_state,
    classify_span_state,
    semantic_state_dataset_max_timestamp,
)
from .schema_validation import (
    load_topic_membership_validator,
    load_topics_validator,
    load_window_neighbors_validator,
)

WindowRef = tuple[str, str]
TOPICS_SCHEMA_VERSION = "2.1"
TOPIC_MEMBERSHIP_SCHEMA_VERSION = "1.0"
TOPIC_MEMBERSHIP_MODE = "span-and-message-v2"
TOPIC_CLUSTERING_METHOD = "connected-components"
TOPIC_CLUSTERING_SCORE_POLICY = (
    "same-thread-shared-messages<=1;cross-thread-mutual-score>=runtime-p75"
)
TOPIC_HEURISTIC_LABEL_FALLBACK = "misc"
TOPIC_HEURISTIC_LABEL_MAX_TOKENS = 4
TOPIC_HEURISTIC_LATIN_MIN_LEN = 3
TOPIC_HEURISTIC_TOKEN_RE = re.compile(
    r"[a-z][a-z0-9_/-]*|[一-龯ぁ-んァ-ヶー]+",
    re.IGNORECASE,
)
TOPIC_HEURISTIC_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "any",
        "are",
        "assistant",
        "can",
        "capture",
        "check",
        "codex",
        "compare",
        "could",
        "designing",
        "did",
        "do",
        "does",
        "done",
        "draft",
        "for",
        "from",
        "get",
        "go",
        "got",
        "here",
        "how",
        "implement",
        "implementation",
        "include",
        "in_progress",
        "into",
        "just",
        "make",
        "need",
        "next",
        "not",
        "now",
        "okay",
        "ok",
        "one",
        "only",
        "out",
        "please",
        "prompting",
        "review",
        "run",
        "same",
        "ship",
        "should",
        "show",
        "single",
        "some",
        "state",
        "sure",
        "task",
        "tests",
        "thank",
        "thanks",
        "that",
        "the",
        "their",
        "them",
        "there",
        "these",
        "thing",
        "this",
        "those",
        "through",
        "today",
        "topic",
        "topics",
        "unresolved",
        "update",
        "user",
        "using",
        "want",
        "what",
        "with",
        "work",
        "workflowing",
        "would",
        "yes",
        "you",
        "your",
        "ありがとう",
        "お願いします",
        "これ",
        "それ",
        "です",
        "ます",
        "こと",
        "もの",
        "よう",
    }
)


class SemanticTopicsError(RuntimeError):
    pass


def _displayable_label_text(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    return " ".join(text.split()).strip()


def _topic_label_source_texts(
    *,
    prompt_windows: list[dict[str, Any]],
    members: list[WindowClusterMember],
    windows: dict[WindowRef, WindowPreviewRecord],
) -> list[str]:
    texts: list[str] = []
    seen_refs: set[WindowRef] = set()

    for row in prompt_windows:
        conversation_id = row.get("conversation_id")
        window_id = row.get("window_id")
        if not isinstance(conversation_id, str) or not isinstance(window_id, str):
            continue
        ref = (conversation_id, window_id)
        seen_refs.add(ref)
        excerpt = _displayable_label_text(row.get("excerpt"))
        if excerpt:
            texts.append(excerpt)
            continue
        record = windows.get(ref)
        if record is None:
            continue
        text = _displayable_label_text(record.text)
        if text:
            texts.append(text)

    if texts:
        return texts

    for member in members:
        ref = (member.conversation_id, member.window_id)
        if ref in seen_refs:
            continue
        record = windows.get(ref)
        if record is None:
            continue
        text = _displayable_label_text(record.text)
        if text:
            texts.append(text)
    return texts


def _heuristic_label_tokens(texts: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    position = 0

    for text in texts:
        normalized = re.sub(r"`{3,}[\w+-]*", " ", text.casefold())
        normalized = re.sub(r"[#>*_\-\[\](){}|]+", " ", normalized)
        for token in TOPIC_HEURISTIC_TOKEN_RE.findall(normalized):
            if token.isdigit():
                continue
            if (
                re.fullmatch(r"[a-z0-9_/-]+", token)
                and len(token) < TOPIC_HEURISTIC_LATIN_MIN_LEN
            ):
                continue
            if token in TOPIC_HEURISTIC_STOPWORDS:
                continue
            counts[token] = counts.get(token, 0) + 1
            first_seen.setdefault(token, position)
            position += 1

    ranked = sorted(
        counts,
        key=lambda token: (-counts[token], first_seen[token], token),
    )[:TOPIC_HEURISTIC_LABEL_MAX_TOKENS]
    ranked.sort(key=lambda token: first_seen[token])
    return ranked


def _heuristic_topic_label(
    *,
    prompt_windows: list[dict[str, Any]],
    members: list[WindowClusterMember],
    windows: dict[WindowRef, WindowPreviewRecord],
) -> str:
    texts = _topic_label_source_texts(
        prompt_windows=prompt_windows,
        members=members,
        windows=windows,
    )
    tokens = _heuristic_label_tokens(texts)
    if not tokens:
        return TOPIC_HEURISTIC_LABEL_FALLBACK
    return " ".join(tokens)


def _utc_now_isoformat() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _pipeline_version() -> str:
    try:
        return package_version("llm-logparser")
    except PackageNotFoundError:
        return "unknown"


def _prompt_hash() -> str:
    return f"sha256:{hashlib.sha256(TOPIC_PROMPT_TEMPLATE.encode('utf-8')).hexdigest()}"


def _labeling_prompt_provenance(model: str | None) -> tuple[str | None, str | None]:
    if model is None:
        return None, None
    return DEFAULT_TOPIC_PROMPT_VARIANT, _prompt_hash()


def _discover_artifacts(root: Path, name: str) -> list[Path]:
    return sorted(root.rglob(name))


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
                raise SemanticTopicsError(
                    f"invalid JSON in {path}:{line_no}: {exc.msg}"
                ) from exc
            if not isinstance(row, dict):
                raise SemanticTopicsError(
                    f"invalid record in {path}:{line_no}: expected object"
                )
            rows.append(row)
    return rows


def _semantic_neighbor_provenance(input_root: Path) -> tuple[str | None, int | None, bool]:
    validator = load_window_neighbors_validator()
    embedding_models: set[str] = set()
    neighbor_k: int | None = None
    found = False

    for path in _discover_artifacts(input_root, "window_neighbors.jsonl"):
        for line_no, row in enumerate(_load_jsonl(path), start=1):
            errors = list(validator.iter_errors(row))
            if errors:
                raise SemanticTopicsError(
                    f"window neighbor schema validation failed for "
                    f"{path}:{line_no}: {errors[0].message}"
                )
            found = True
            embedding_models.add(row["embedding_model"])
            row_neighbor_count = int(row.get("neighbor_count", len(row.get("neighbors", []))))
            neighbor_k = (
                row_neighbor_count
                if neighbor_k is None
                else max(neighbor_k, row_neighbor_count)
            )

    embedding_model = next(iter(sorted(embedding_models))) if len(embedding_models) == 1 else None
    return embedding_model, neighbor_k, found


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


def _topic_id(
    provider_id: str,
    members: list[WindowClusterMember],
    windows: dict[WindowRef, WindowPreviewRecord],
) -> str:
    anchors = sorted(
        f"{member.conversation_id}::"
        f"{windows[(member.conversation_id, member.window_id)].span_id}"
        if (member.conversation_id, member.window_id) in windows
        else f"{member.conversation_id}::legacy-window::{member.window_id}"
        for member in members
    )
    digest = hashlib.sha256(
        f"{provider_id}|{'|'.join(anchors)}".encode("utf-8")
    ).hexdigest()
    return f"topic_{digest[:12]}"


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


def _span_refs(
    members: list[WindowClusterMember],
    windows: dict[WindowRef, WindowPreviewRecord],
    *,
    span_state_by_ref: dict[WindowRef, SpanStateResult],
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for member in sorted(members, key=lambda item: (item.conversation_id, item.window_id)):
        record = windows.get((member.conversation_id, member.window_id))
        if record is None:
            continue
        key = (member.conversation_id, record.span_id)
        if key in seen:
            continue
        seen.add(key)
        state_row = span_state_by_ref[(member.conversation_id, member.window_id)]
        refs.append(
            {
                "conversation_id": member.conversation_id,
                "span_id": record.span_id,
                "message_ids": list(record.message_ids),
                "state": state_row.state,
                "state_confidence": state_row.state_confidence,
                "state_signals": list(state_row.state_signals),
                # Compatibility overlay only; semantic identity is span-based.
                "window_id": member.window_id,
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


def _representative_spans(
    prompt_windows: list[dict[str, Any]],
    *,
    span_state_by_ref: dict[WindowRef, SpanStateResult],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in prompt_windows[:3]:
        state_row = span_state_by_ref[(row["conversation_id"], row["window_id"])]
        rows.append(
            {
                "conversation_id": row["conversation_id"],
                "span_id": row["span_id"],
                "message_ids": list(row["message_ids"]),
                "excerpt": row["excerpt"],
                "state": state_row.state,
                "state_confidence": state_row.state_confidence,
                "state_signals": list(state_row.state_signals),
                # Compatibility overlay only; semantic identity is span-based.
                "window_id": row["window_id"],
            }
        )
    return rows


def _representative_windows(prompt_windows: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "conversation_id": row["conversation_id"],
            "window_id": row["window_id"],
            "excerpt": row["excerpt"],
        }
        for row in prompt_windows[:3]
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


def _write_json_artifact(path: Path, payload: dict[str, Any]) -> Path:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)
    return path


def _write_jsonl_artifact(path: Path, rows: list[dict[str, Any]]) -> Path:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(path)
    return path


def _topic_prompt(
    *,
    cluster_id: str,
    members: list[WindowClusterMember],
    prompt_windows: list[dict[str, Any]],
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
    prompt_windows: list[dict[str, Any]],
    windows: dict[WindowRef, WindowPreviewRecord],
    base_url: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    if model is None:
        return {
            "label": _heuristic_topic_label(
                prompt_windows=prompt_windows,
                members=members,
                windows=windows,
            ),
            "summary": None,
            "keywords": [],
            "confidence": None,
        }

    try:
        parsed = _generate_topic_output(
            model=model,
            prompt=_topic_prompt(
                cluster_id=cluster_id,
                members=members,
                prompt_windows=prompt_windows,
            ),
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )
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
    state_locale: str | None = None,
    include_representative_span_normalization: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if min_cluster_size <= 0:
        raise SemanticTopicsError("--min-cluster-size must be > 0")
    if timeout_seconds <= 0:
        raise SemanticTopicsError("--timeout-seconds must be > 0")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise SemanticTopicsError("--model must be a non-empty string when provided")

    try:
        windows = load_window_preview_index(input_root)
        neighbor_index = load_window_neighbor_index(input_root)
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
    normalized_model = model.strip() if isinstance(model, str) else None
    if include_representative_span_normalization and normalized_model is None:
        raise SemanticTopicsError(
            "--model is required when representative span normalization is enabled"
        )
    prompt_variant, prompt_hash = _labeling_prompt_provenance(normalized_model)
    embedding_model, neighbor_k, has_neighbors = _semantic_neighbor_provenance(input_root)
    edge_policies = sorted({member.edge_policy for _cluster_id, members in items for member in members})
    dataset_max_ts = semantic_state_dataset_max_timestamp(windows.values())
    span_state_by_ref: dict[WindowRef, SpanStateResult] = {
        key: result
        for key, result in (
            (
                key,
                classify_span_state(
                    record,
                    dataset_max_ts=dataset_max_ts,
                    state_locale=state_locale,
                ),
            )
            for key, record in windows.items()
        )
    }
    normalization_client: LLMClient | None = None
    if include_representative_span_normalization and normalized_model is not None:
        normalization_client = OllamaClient(
            base_url=base_url,
            timeout=timeout_seconds,
        )
    topics: list[dict[str, Any]] = []
    membership_rows: list[dict[str, Any]] = []
    for item_cluster_id, members in items:
        prompt_windows = list(
            select_representative_cluster_windows(
                members=members,
                windows=windows,
                neighbor_index=neighbor_index,
                window_cap=DEFAULT_TOPIC_WINDOW_CAP,
                max_window_chars=DEFAULT_TOPIC_MAX_WINDOW_CHARS,
            )
        )
        if not prompt_windows:
            continue

        first_seen, last_seen = _time_bounds(members, windows)
        span_refs = _span_refs(
            members,
            windows,
            span_state_by_ref=span_state_by_ref,
        )
        message_refs = _message_refs(members, windows)
        representative_spans = _representative_spans(
            prompt_windows,
            span_state_by_ref=span_state_by_ref,
        )
        if normalization_client is not None and normalized_model is not None:
            for span_row in representative_spans:
                conversation_id = span_row["conversation_id"]
                window_id = span_row.get("window_id")
                if not isinstance(window_id, str):
                    continue
                record = windows.get((conversation_id, window_id))
                if record is None:
                    continue
                span_row["semantic_normalization"] = semantic_normalization_to_dict(
                    normalize_representative_span(
                        client=normalization_client,
                        model=normalized_model,
                        conversation_id=conversation_id,
                        span_id=span_row["span_id"],
                        window_id=window_id,
                        message_ids=list(span_row["message_ids"]),
                        text=record.text,
                    )
                )
        topic_id = _topic_id(provider_id, members, windows)
        topic_state, topic_state_confidence = aggregate_topic_state(
            [
                span_state_by_ref[(member.conversation_id, member.window_id)]
                for member in members
                if (member.conversation_id, member.window_id) in windows
            ]
        )
        topic_fields = _topic_model_fields(
            model=normalized_model,
            cluster_id=item_cluster_id,
            members=members,
            prompt_windows=prompt_windows,
            windows=windows,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )
        topic = {
            "topic_id": topic_id,
            "provider_id": provider_id,
            "label": topic_fields["label"],
            "summary": topic_fields["summary"],
            "keywords": topic_fields["keywords"],
            "confidence": topic_fields["confidence"],
            "state": topic_state,
            "state_confidence": topic_state_confidence,
            "cluster_ids": [item_cluster_id],
            "conversation_ids": sorted({member.conversation_id for member in members}),
            "span_refs": span_refs,
            "message_refs": message_refs,
            "cluster_count": 1,
            "span_count": len(span_refs),
            "window_count": len(members),
            "message_count": len(message_refs),
            "quality_signals": compute_cluster_quality_signals(
                members=members,
                neighbor_index=neighbor_index,
            ),
            "first_seen": first_seen,
            "last_seen": last_seen,
            "representative_spans": representative_spans,
            # Compatibility overlays: retained for browse/render paths that still
            # expect window-shaped references.
            "window_refs": _window_refs(members),
            "representative_windows": _representative_windows(prompt_windows),
        }
        topics.append(topic)
        membership_rows.append(
            {
                "record_type": "topic_membership",
                "schema_version": TOPIC_MEMBERSHIP_SCHEMA_VERSION,
                "provider_id": provider_id,
                "topic_id": topic_id,
                "membership_type": "cluster",
                "conversation_id": None,
                "cluster_id": item_cluster_id,
                "span_id": None,
                "window_id": None,
                "message_id": None,
            }
        )
        for member in sorted(members, key=lambda item: (item.conversation_id, item.window_id)):
            record = windows.get((member.conversation_id, member.window_id))
            if record is None:
                continue
            membership_rows.append(
                {
                    "record_type": "topic_membership",
                    "schema_version": TOPIC_MEMBERSHIP_SCHEMA_VERSION,
                    "provider_id": provider_id,
                    "topic_id": topic_id,
                    "membership_type": "span",
                    "conversation_id": member.conversation_id,
                    "cluster_id": item_cluster_id,
                    "span_id": record.span_id,
                    "window_id": member.window_id,
                    "message_id": None,
                }
            )
            for message_id in record.message_ids:
                membership_rows.append(
                    {
                        "record_type": "topic_membership",
                        "schema_version": TOPIC_MEMBERSHIP_SCHEMA_VERSION,
                        "provider_id": provider_id,
                        "topic_id": topic_id,
                        "membership_type": "message",
                        "conversation_id": member.conversation_id,
                        "cluster_id": item_cluster_id,
                        "span_id": record.span_id,
                        "window_id": member.window_id,
                        "message_id": message_id,
                    }
                )

    if not topics:
        raise SemanticTopicsError("matched clusters did not have usable topic inputs")

    artifact = {
        "artifact_type": "semantic_topics",
        "schema_version": TOPICS_SCHEMA_VERSION,
        "provider_id": provider_id,
        "topic_count": len(topics),
        "generated_at": _utc_now_isoformat(),
        "source_inputs": [
            "message_windows.jsonl",
            "window_clusters.jsonl",
            *(
                ["window_neighbors.jsonl"]
                if has_neighbors
                else []
            ),
        ],
        "provenance": {
            "pipeline_version": _pipeline_version(),
            "membership_mode": TOPIC_MEMBERSHIP_MODE,
            "label_mode": "model-enriched" if normalized_model else "structural-only",
            "embedding_model": embedding_model,
            "labeling_model": f"ollama/{normalized_model}" if normalized_model else None,
            "prompt_hash": prompt_hash,
            "prompt_variant": prompt_variant,
            "window_cap": DEFAULT_TOPIC_WINDOW_CAP,
            "max_window_chars": DEFAULT_TOPIC_MAX_WINDOW_CHARS,
            "clustering": {
                "method": TOPIC_CLUSTERING_METHOD,
                "edge_policy": edge_policies[0] if len(edge_policies) == 1 else "mixed",
                "neighbor_k": neighbor_k,
                "score_threshold_policy": TOPIC_CLUSTERING_SCORE_POLICY,
            },
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
    state_locale: str | None = None,
) -> dict[str, Any]:
    artifact, membership_rows = build_semantic_topics_artifact(
        input_root,
        model=model,
        cluster_id=cluster_id,
        min_cluster_size=min_cluster_size,
        cross_thread_only=cross_thread_only,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        state_locale=state_locale,
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

    _write_json_artifact(topics_path, artifact)
    _write_jsonl_artifact(membership_path, membership_rows)

    return {
        "provider_id": artifact["provider_id"],
        "topic_count": artifact["topic_count"],
        "topics_path": str(topics_path),
        "membership_path": str(membership_path),
        "label_mode": artifact["provenance"]["label_mode"],
    }
