from __future__ import annotations

import hashlib
import json
import logging
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
    SEED_TAXONOMY_VERSION,
    normalize_representative_span,
    semantic_normalization_prompt_hashes,
    semantic_normalization_to_dict,
)
from .semantic_normalization_jobs import (
    SemanticNormalizationJobError,
    load_semantic_normalization_job_results,
)
from .analyzer_semantic_span_proposals import (
    SemanticSpanProposalError,
    load_semantic_span_proposal_rows,
    semantic_span_proposals_path,
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
TOPICS_SCHEMA_VERSION = "2.2"
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


LOGGER = logging.getLogger(__name__)


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


def _representative_span_excerpt(text: str) -> str:
    compact = " ".join(text.split())
    if len(compact) <= DEFAULT_TOPIC_MAX_WINDOW_CHARS:
        return compact
    return compact[: DEFAULT_TOPIC_MAX_WINDOW_CHARS - 3] + "..."


def _effective_source_window_ids(span_row: dict[str, Any]) -> tuple[str, ...]:
    source_window_ids = span_row.get("source_window_ids")
    if isinstance(source_window_ids, list) and source_window_ids:
        values = tuple(
            str(window_id)
            for window_id in source_window_ids
            if isinstance(window_id, str) and window_id
        )
        if values:
            return values
    window_id = span_row.get("window_id")
    if isinstance(window_id, str) and window_id:
        return (window_id,)
    return ()


def _reconstructed_span_record(
    *,
    conversation_id: str,
    message_ids: list[str] | tuple[str, ...],
    source_window_ids: list[str] | tuple[str, ...],
    windows: dict[WindowRef, WindowPreviewRecord],
) -> WindowPreviewRecord | None:
    if not source_window_ids:
        return None
    ordered_message_ids = tuple(str(message_id) for message_id in message_ids)
    message_lookup: dict[str, Any] = {}
    provider_id: str | None = None
    for window_id in source_window_ids:
        record = windows.get((conversation_id, window_id))
        if record is None:
            return None
        provider_id = provider_id or record.provider_id
        for message in record.messages:
            message_lookup.setdefault(message.message_id, message)
    selected_messages = []
    for message_id in ordered_message_ids:
        message = message_lookup.get(message_id)
        if message is None:
            return None
        selected_messages.append(message)
    timestamps = [message.ts for message in selected_messages if isinstance(message.ts, int)]
    return WindowPreviewRecord(
        provider_id=provider_id or windows[(conversation_id, source_window_ids[0])].provider_id,
        conversation_id=conversation_id,
        window_id=source_window_ids[0],
        message_ids=ordered_message_ids,
        char_count=sum(len(message.text) for message in selected_messages),
        ts_start=min(timestamps) if timestamps else None,
        ts_end=max(timestamps) if timestamps else None,
        messages=tuple(selected_messages),
    )


def _representative_windows(prompt_windows: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "conversation_id": row["conversation_id"],
            "window_id": row["window_id"],
            "excerpt": row["excerpt"],
        }
        for row in prompt_windows[:3]
    ]


def _normalization_text_sha1(text: str) -> str:
    # Batch normalization joins are only valid when producer and consumer hash
    # the same reconstructed span text bytes.
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _semantic_normalization_payload_from_job_row(
    row: dict[str, Any],
) -> dict[str, Any]:
    return {
        "conversation_id": row["conversation_id"],
        "span_id": row["span_id"],
        "window_id": row.get("window_id"),
        "message_ids": list(row["message_ids"]),
        "unit_kind": "representative_span",
        "raw_label": row["raw_label"],
        "normalized_label": row.get("normalized_label"),
        "mapping_status": row["mapping_status"],
        "confidence": row.get("confidence"),
        "method": row["method"],
    }


def _batch_normalization_index(
    rows: list[dict[str, Any]],
    *,
    job_id: str,
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        span_id = row.get("span_id")
        if not isinstance(span_id, str) or not span_id:
            continue
        if span_id in indexed:
            LOGGER.warning(
                "semantic normalization job %s has duplicate result rows for span_id=%s; using first row",
                job_id,
                span_id,
            )
            continue
        indexed[span_id] = row
    return indexed


def _semantic_span_proposal_index(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    indexed: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        conversation_id = row.get("conversation_id")
        source_window_ids = row.get("source_window_ids")
        if not isinstance(conversation_id, str):
            continue
        if not isinstance(source_window_ids, list):
            continue
        for window_id in source_window_ids:
            if not isinstance(window_id, str) or not window_id:
                continue
            indexed.setdefault((conversation_id, window_id), []).append(row)
    return indexed


def _is_message_id_prefix(
    candidate_ids: list[str] | tuple[str, ...],
    current_ids: list[str] | tuple[str, ...],
) -> bool:
    candidate_tuple = tuple(str(message_id) for message_id in candidate_ids)
    current_tuple = tuple(str(message_id) for message_id in current_ids)
    if not candidate_tuple or len(candidate_tuple) >= len(current_tuple):
        return False
    return current_tuple[: len(candidate_tuple)] == candidate_tuple


def _eligible_refinement_candidate(
    *,
    span_row: dict[str, Any],
    proposal_row: dict[str, Any],
) -> bool:
    anchor_window_id = span_row.get("window_id")
    if not isinstance(anchor_window_id, str) or not anchor_window_id:
        return False
    proposal_kind = proposal_row.get("proposal_kind")
    source_window_ids = proposal_row.get("source_window_ids")
    proposal_message_ids = proposal_row.get("message_ids")
    current_message_ids = span_row.get("message_ids")
    if not isinstance(source_window_ids, list) or not isinstance(proposal_message_ids, list):
        return False
    if not isinstance(current_message_ids, list):
        return False
    if proposal_kind == "split":
        return (
            source_window_ids == [anchor_window_id]
            and _is_message_id_prefix(proposal_message_ids, current_message_ids)
        )
    if proposal_kind == "merge":
        proposal_ids = tuple(str(message_id) for message_id in proposal_message_ids)
        current_ids = tuple(str(message_id) for message_id in current_message_ids)
        return (
            len(source_window_ids) > 1
            and source_window_ids[0] == anchor_window_id
            and len(proposal_ids) > len(current_ids)
            and proposal_ids[: len(current_ids)] == current_ids
        )
    return False


def _proposal_refinement_provenance(
    *,
    input_root: Path,
    proposal_count: int,
    counts: dict[str, int],
) -> dict[str, Any]:
    return {
        "source_kind": "semantic_span_proposals",
        "artifact_path": str(semantic_span_proposals_path(input_root).resolve()),
        "proposal_count": proposal_count,
        **counts,
    }


def _refine_representative_spans_from_proposals(
    representative_spans: list[dict[str, Any]],
    *,
    input_root: Path,
    windows: dict[WindowRef, WindowPreviewRecord],
    proposal_index: dict[tuple[str, str], list[dict[str, Any]]],
    dataset_max_ts: int | None,
    state_locale: str | None,
) -> dict[str, int]:
    counts = {
        "refined_representative_span_count": 0,
        "unchanged_representative_span_count": 0,
        "ambiguous_representative_span_count": 0,
        "drifted_representative_span_count": 0,
    }
    for span_row in representative_spans:
        conversation_id = span_row["conversation_id"]
        anchor_window_id = span_row.get("window_id")
        if not isinstance(anchor_window_id, str):
            counts["unchanged_representative_span_count"] += 1
            continue
        candidates = proposal_index.get((conversation_id, anchor_window_id), [])
        if not candidates:
            counts["unchanged_representative_span_count"] += 1
            continue
        eligible: list[tuple[dict[str, Any], WindowPreviewRecord]] = []
        drifted = False
        for proposal_row in candidates:
            if not _eligible_refinement_candidate(
                span_row=span_row,
                proposal_row=proposal_row,
            ):
                continue
            source_window_ids = proposal_row.get("source_window_ids")
            proposal_message_ids = proposal_row.get("message_ids")
            if not isinstance(source_window_ids, list) or not isinstance(proposal_message_ids, list):
                continue
            record = _reconstructed_span_record(
                conversation_id=conversation_id,
                message_ids=[str(message_id) for message_id in proposal_message_ids],
                source_window_ids=[str(window_id) for window_id in source_window_ids],
                windows=windows,
            )
            if record is None:
                drifted = True
                continue
            current_text_sha1 = _normalization_text_sha1(record.text)
            proposal_text_sha1 = proposal_row.get("text_sha1")
            if not isinstance(proposal_text_sha1, str) or proposal_text_sha1 != current_text_sha1:
                drifted = True
                LOGGER.warning(
                    "semantic span proposal drift detected for representative span "
                    "conversation_id=%s window_id=%s span_id=%s; expected text_sha1=%r current=%r; skipping refinement candidate",
                    conversation_id,
                    anchor_window_id,
                    proposal_row.get("span_id"),
                    proposal_text_sha1,
                    current_text_sha1,
                )
                continue
            eligible.append((proposal_row, record))
        if len(eligible) > 1:
            counts["ambiguous_representative_span_count"] += 1
            continue
        if not eligible:
            if drifted:
                counts["drifted_representative_span_count"] += 1
            else:
                counts["unchanged_representative_span_count"] += 1
            continue
        proposal_row, record = eligible[0]
        state_row = classify_span_state(
            record,
            dataset_max_ts=dataset_max_ts,
            state_locale=state_locale,
        )
        span_row["conversation_id"] = conversation_id
        span_row["span_id"] = proposal_row["span_id"]
        span_row["message_ids"] = [str(message_id) for message_id in proposal_row["message_ids"]]
        span_row["excerpt"] = _representative_span_excerpt(record.text)
        span_row["state"] = state_row.state
        span_row["state_confidence"] = state_row.state_confidence
        span_row["state_signals"] = list(state_row.state_signals)
        span_row["source_window_ids"] = [str(window_id) for window_id in proposal_row["source_window_ids"]]
        span_row["refinement"] = {
            "source_kind": "semantic_span_proposals",
            "proposal_kind": proposal_row["proposal_kind"],
            "reason_code": proposal_row["reason_code"],
        }
        if len(span_row["source_window_ids"]) == 1:
            span_row["window_id"] = span_row["source_window_ids"][0]
        else:
            span_row.pop("window_id", None)
        counts["refined_representative_span_count"] += 1
    return counts


def _attach_batch_semantic_normalization(
    representative_spans: list[dict[str, Any]],
    *,
    windows: dict[WindowRef, WindowPreviewRecord],
    batch_index: dict[str, dict[str, Any]],
    job_id: str,
) -> dict[str, int]:
    """Attach batch normalization by stable span identity plus text drift check.

    Join contract:
    - primary key is ``span_id``
    - ``text_sha1`` validates that the current reconstructed span text still
      matches the batch producer's text contract
    - missing or drifted rows are skipped; there is no fuzzy fallback
    """
    counts = {
        "matched_representative_span_count": 0,
        "unmatched_representative_span_count": 0,
        "drifted_representative_span_count": 0,
    }
    for span_row in representative_spans:
        span_id = span_row["span_id"]
        batch_row = batch_index.get(span_id)
        if batch_row is None:
            counts["unmatched_representative_span_count"] += 1
            continue
        conversation_id = span_row["conversation_id"]
        record = _reconstructed_span_record(
            conversation_id=conversation_id,
            message_ids=span_row["message_ids"],
            source_window_ids=_effective_source_window_ids(span_row),
            windows=windows,
        )
        if record is None:
            counts["unmatched_representative_span_count"] += 1
            continue
        current_text_sha1 = _normalization_text_sha1(record.text)
        batch_text_sha1 = batch_row.get("text_sha1")
        if not isinstance(batch_text_sha1, str) or batch_text_sha1 != current_text_sha1:
            counts["drifted_representative_span_count"] += 1
            LOGGER.warning(
                "semantic normalization drift detected for job %s span_id=%s; expected text_sha1=%r current=%r; skipping attachment",
                job_id,
                span_id,
                batch_text_sha1,
                current_text_sha1,
            )
            continue
        span_row["semantic_normalization"] = _semantic_normalization_payload_from_job_row(
            batch_row
        )
        counts["matched_representative_span_count"] += 1
    return counts


def _normalization_provenance(
    *,
    job_id: str,
    config: dict[str, Any],
    counts: dict[str, int],
) -> dict[str, Any]:
    normalization = config.get("normalization")
    prompt_provenance = config.get("prompt_provenance")
    normalization_row = normalization if isinstance(normalization, dict) else {}
    prompt_row = prompt_provenance if isinstance(prompt_provenance, dict) else {}
    prompt_payload = {
        "raw_label_prompt_sha1": prompt_row.get("raw_label_prompt_sha1"),
        "mapping_prompt_sha1": prompt_row.get("mapping_prompt_sha1"),
    }
    for key in ("prompt_set", "raw_label_prompt_path", "mapping_prompt_path"):
        value = prompt_row.get(key)
        if isinstance(value, str) and value.strip():
            prompt_payload[key] = value
    return {
        "source_kind": "batch",
        "job_id": job_id,
        "model": normalization_row.get("model"),
        "taxonomy_version": normalization_row.get("taxonomy_version"),
        "prompt_provenance": prompt_payload,
        **counts,
    }


def _batch_normalization_consistency_messages(
    *,
    job_id: str,
    config: dict[str, Any],
    expected_taxonomy_version: str | None,
) -> list[str]:
    normalization = config.get("normalization")
    prompt_provenance = config.get("prompt_provenance")
    normalization_row = normalization if isinstance(normalization, dict) else {}
    prompt_row = prompt_provenance if isinstance(prompt_provenance, dict) else {}

    expected_taxonomy = expected_taxonomy_version or SEED_TAXONOMY_VERSION
    messages: list[str] = []
    actual_taxonomy = normalization_row.get("taxonomy_version")
    if actual_taxonomy != expected_taxonomy:
        messages.append(
            "semantic normalization taxonomy mismatch for "
            f"job {job_id}: expected {expected_taxonomy!r}, got {actual_taxonomy!r}"
        )

    expected_prompt_hashes = semantic_normalization_prompt_hashes()
    for key, expected_hash in expected_prompt_hashes.items():
        actual_hash = prompt_row.get(key)
        if actual_hash != expected_hash:
            messages.append(
                "semantic normalization prompt hash mismatch for "
                f"job {job_id} ({key}): expected {expected_hash!r}, got {actual_hash!r}"
            )
    return messages


def _enforce_batch_normalization_consistency(
    *,
    job_id: str,
    config: dict[str, Any],
    expected_taxonomy_version: str | None,
    strict_normalization: bool,
) -> None:
    messages = _batch_normalization_consistency_messages(
        job_id=job_id,
        config=config,
        expected_taxonomy_version=expected_taxonomy_version,
    )
    if not messages:
        return
    for message in messages:
        LOGGER.warning(message)
    if strict_normalization:
        raise SemanticTopicsError(
            "semantic normalization consistency check failed: "
            + "; ".join(messages)
        )


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
    normalization_job: str | None = None,
    refine_representative_spans_from_proposals: bool = False,
    expected_taxonomy_version: str | None = None,
    strict_normalization: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if min_cluster_size <= 0:
        raise SemanticTopicsError("--min-cluster-size must be > 0")
    if timeout_seconds <= 0:
        raise SemanticTopicsError("--timeout-seconds must be > 0")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise SemanticTopicsError("--model must be a non-empty string when provided")
    if normalization_job is not None and (
        not isinstance(normalization_job, str) or not normalization_job.strip()
    ):
        raise SemanticTopicsError(
            "--normalization-job must be a non-empty string when provided"
        )
    if expected_taxonomy_version is not None and (
        not isinstance(expected_taxonomy_version, str)
        or not expected_taxonomy_version.strip()
    ):
        raise SemanticTopicsError(
            "--expected-taxonomy-version must be a non-empty string when provided"
        )

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
    normalized_job_id = (
        normalization_job.strip() if isinstance(normalization_job, str) else None
    )
    if (
        include_representative_span_normalization
        and normalized_job_id is not None
    ):
        raise SemanticTopicsError(
            "batch normalization (--normalization-job) cannot be combined with inline representative span normalization"
        )
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
    batch_normalization_config: dict[str, Any] | None = None
    batch_normalization_index: dict[str, dict[str, Any]] | None = None
    proposal_refinement_index: dict[tuple[str, str], list[dict[str, Any]]] | None = None
    proposal_refinement_count = 0
    proposal_refinement_counts = {
        "refined_representative_span_count": 0,
        "unchanged_representative_span_count": 0,
        "ambiguous_representative_span_count": 0,
        "drifted_representative_span_count": 0,
    }
    batch_normalization_counts = {
        "matched_representative_span_count": 0,
        "unmatched_representative_span_count": 0,
        "drifted_representative_span_count": 0,
    }
    if refine_representative_spans_from_proposals:
        try:
            proposal_rows = load_semantic_span_proposal_rows(input_root)
        except SemanticSpanProposalError as exc:
            raise SemanticTopicsError(str(exc)) from exc
        proposal_refinement_index = _semantic_span_proposal_index(proposal_rows)
        proposal_refinement_count = len(proposal_rows)
    if normalized_job_id is not None:
        try:
            batch_job = load_semantic_normalization_job_results(
                input_root,
                job_id=normalized_job_id,
            )
        except SemanticNormalizationJobError as exc:
            raise SemanticTopicsError(str(exc)) from exc
        batch_normalization_config = batch_job.config
        _enforce_batch_normalization_consistency(
            job_id=normalized_job_id,
            config=batch_normalization_config,
            expected_taxonomy_version=expected_taxonomy_version,
            strict_normalization=strict_normalization,
        )
        batch_normalization_index = _batch_normalization_index(
            batch_job.result_rows,
            job_id=normalized_job_id,
        )
        if not batch_normalization_index:
            LOGGER.warning(
                "semantic normalization job %s has no successful results to attach",
                normalized_job_id,
            )
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
        if proposal_refinement_index is not None:
            counts = _refine_representative_spans_from_proposals(
                representative_spans,
                input_root=input_root,
                windows=windows,
                proposal_index=proposal_refinement_index,
                dataset_max_ts=dataset_max_ts,
                state_locale=state_locale,
            )
            for key, value in counts.items():
                proposal_refinement_counts[key] += value
        if batch_normalization_index is not None and normalized_job_id is not None:
            counts = _attach_batch_semantic_normalization(
                representative_spans,
                windows=windows,
                batch_index=batch_normalization_index,
                job_id=normalized_job_id,
            )
            for key, value in counts.items():
                batch_normalization_counts[key] += value
        elif normalization_client is not None and normalized_model is not None:
            for span_row in representative_spans:
                conversation_id = span_row["conversation_id"]
                window_id = span_row.get("window_id")
                record = _reconstructed_span_record(
                    conversation_id=conversation_id,
                    message_ids=span_row["message_ids"],
                    source_window_ids=_effective_source_window_ids(span_row),
                    windows=windows,
                )
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
                ["span_proposals.jsonl"]
                if proposal_refinement_index is not None
                else []
            ),
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
            **(
                {
                    "normalization": _normalization_provenance(
                        job_id=normalized_job_id,
                        config=batch_normalization_config or {},
                        counts=batch_normalization_counts,
                    )
                }
                if normalized_job_id is not None
                else {}
            ),
            **(
                {
                    "representative_span_refinement": _proposal_refinement_provenance(
                        input_root=input_root,
                        proposal_count=proposal_refinement_count,
                        counts=proposal_refinement_counts,
                    )
                }
                if proposal_refinement_index is not None
                else {}
            ),
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
    include_representative_span_normalization: bool = False,
    normalization_job: str | None = None,
    refine_representative_spans_from_proposals: bool = False,
    expected_taxonomy_version: str | None = None,
    strict_normalization: bool = False,
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
        include_representative_span_normalization=include_representative_span_normalization,
        normalization_job=normalization_job,
        refine_representative_spans_from_proposals=refine_representative_spans_from_proposals,
        expected_taxonomy_version=expected_taxonomy_version,
        strict_normalization=strict_normalization,
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
