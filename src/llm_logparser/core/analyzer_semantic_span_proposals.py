from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
from typing import Any

from .analyzer_common import write_json_artifact
from .analyzer_semantic_prototype import (
    derive_semantic_span_id,
    discover_semantic_prototype_inputs,
)
from .message_window_reconstruction import (
    ReconstructedMessageWindow,
    load_reconstructed_message_windows,
    load_reconstructed_message_windows_from_parsed,
)
from .schema_validation import (
    load_semantic_span_proposal_validator,
    load_window_clusters_validator,
)

SEMANTIC_SPAN_PROPOSAL_SCHEMA_VERSION = "0.1"
SEMANTIC_SPAN_PROPOSAL_RECORD_TYPE = "semantic_span_proposal"


class SemanticSpanProposalError(RuntimeError):
    pass


@dataclass(frozen=True)
class _ProposalCandidate:
    provider_id: str
    conversation_id: str
    message_ids: tuple[str, ...]
    text: str
    source_window_ids: tuple[str, ...]
    proposal_kind: str
    reason_code: str
    ts_start: int | None
    ts_end: int | None


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
                raise SemanticSpanProposalError(
                    f"invalid JSON in {path}:{line_no}: {exc.msg}"
                ) from exc
            if not isinstance(row, dict):
                raise SemanticSpanProposalError(
                    f"invalid record in {path}:{line_no}: expected object"
                )
            rows.append((line_no, row))
    return rows


def semantic_span_proposals_path(input_root: Path) -> Path:
    return input_root / "l3" / "semantic-span-proposals" / "span_proposals.jsonl"


def _text_sha1(text: str) -> str:
    return sha1(text.encode("utf-8")).hexdigest()


def _joined_text(messages: tuple[str, ...]) -> str:
    return "\n\n".join(text for text in messages if text)


def _load_cluster_by_window(input_root: Path) -> dict[tuple[str, str], str]:
    validator = load_window_clusters_validator()
    index: dict[tuple[str, str], str] = {}
    for path in sorted(input_root.rglob("window_clusters.jsonl")):
        for line_no, row in _load_jsonl_rows(path):
            errors = list(validator.iter_errors(row))
            if errors:
                raise SemanticSpanProposalError(
                    f"window cluster schema validation failed for "
                    f"{path}:{line_no}: {errors[0].message}"
                )
            index[(str(row["conversation_id"]), str(row["window_id"]))] = str(
                row["cluster_id"]
            )
    return index


def _load_windows(input_root: Path) -> list[ReconstructedMessageWindow]:
    windows: list[ReconstructedMessageWindow] = []
    for path in discover_semantic_prototype_inputs(input_root):
        try:
            if path.name == "message_windows.jsonl":
                windows.extend(load_reconstructed_message_windows(path))
            else:
                windows.extend(load_reconstructed_message_windows_from_parsed(path))
        except (FileNotFoundError, ValueError) as exc:
            raise SemanticSpanProposalError(str(exc)) from exc
    if not windows:
        raise SemanticSpanProposalError(
            f"no message_windows.jsonl or parsed.jsonl found under: {input_root}"
        )
    return windows


def _window_sort_key(window: ReconstructedMessageWindow) -> tuple[Any, ...]:
    return (
        window.conversation_id,
        -1 if window.ts_start is None else window.ts_start,
        -1 if window.ts_end is None else window.ts_end,
        window.window_id,
    )


def _split_candidate(window: ReconstructedMessageWindow) -> list[_ProposalCandidate] | None:
    if window.message_count != 4:
        return None
    if window.roles != ("user", "assistant", "user", "assistant"):
        return None
    messages = window.messages
    first_messages = tuple(message.message_id for message in messages[:2])
    second_messages = tuple(message.message_id for message in messages[2:])
    return [
        _ProposalCandidate(
            provider_id=window.provider_id,
            conversation_id=window.conversation_id,
            message_ids=first_messages,
            text=_joined_text(tuple(message.text for message in messages[:2])),
            source_window_ids=(window.window_id,),
            proposal_kind="split",
            reason_code="split_two_turn_pairs",
            ts_start=messages[0].ts,
            ts_end=messages[1].ts,
        ),
        _ProposalCandidate(
            provider_id=window.provider_id,
            conversation_id=window.conversation_id,
            message_ids=second_messages,
            text=_joined_text(tuple(message.text for message in messages[2:])),
            source_window_ids=(window.window_id,),
            proposal_kind="split",
            reason_code="split_two_turn_pairs",
            ts_start=messages[2].ts,
            ts_end=messages[3].ts,
        ),
    ]


def _merge_candidate(
    previous: ReconstructedMessageWindow,
    current: ReconstructedMessageWindow,
    *,
    cluster_by_window: dict[tuple[str, str], str],
) -> _ProposalCandidate | None:
    if previous.conversation_id != current.conversation_id:
        return None
    previous_cluster = cluster_by_window.get((previous.conversation_id, previous.window_id))
    current_cluster = cluster_by_window.get((current.conversation_id, current.window_id))
    if previous_cluster is None or previous_cluster != current_cluster:
        return None
    if previous.message_count < 2 or current.message_count != 1:
        return None
    if set(previous.message_ids) & set(current.message_ids):
        return None
    merged_messages = previous.messages + current.messages
    return _ProposalCandidate(
        provider_id=previous.provider_id,
        conversation_id=previous.conversation_id,
        message_ids=tuple(message.message_id for message in merged_messages),
        text=_joined_text(tuple(message.text for message in merged_messages)),
        source_window_ids=(previous.window_id, current.window_id),
        proposal_kind="merge",
        reason_code="merge_adjacent_same_cluster_short_continuation",
        ts_start=previous.ts_start,
        ts_end=current.ts_end,
    )


def _keep_candidate(window: ReconstructedMessageWindow) -> _ProposalCandidate:
    return _ProposalCandidate(
        provider_id=window.provider_id,
        conversation_id=window.conversation_id,
        message_ids=window.message_ids,
        text=window.text,
        source_window_ids=(window.window_id,),
        proposal_kind="keep",
        reason_code="keep_window_as_span",
        ts_start=window.ts_start,
        ts_end=window.ts_end,
    )


def _proposal_row(candidate: _ProposalCandidate) -> dict[str, Any]:
    span_id = derive_semantic_span_id(
        provider_id=candidate.provider_id,
        conversation_id=candidate.conversation_id,
        message_ids=candidate.message_ids,
        window_id=candidate.source_window_ids[0],
    )
    row = {
        "record_type": SEMANTIC_SPAN_PROPOSAL_RECORD_TYPE,
        "schema_version": SEMANTIC_SPAN_PROPOSAL_SCHEMA_VERSION,
        "provider_id": candidate.provider_id,
        "conversation_id": candidate.conversation_id,
        "span_id": span_id,
        "message_ids": list(candidate.message_ids),
        "text_sha1": _text_sha1(candidate.text),
        "source_window_ids": list(candidate.source_window_ids),
        "proposal_kind": candidate.proposal_kind,
        "reason_code": candidate.reason_code,
        "message_count": len(candidate.message_ids),
        "char_count": len(candidate.text),
        "window_count": len(candidate.source_window_ids),
        "ts_start": candidate.ts_start,
        "ts_end": candidate.ts_end,
    }
    errors = list(load_semantic_span_proposal_validator().iter_errors(row))
    if errors:
        raise SemanticSpanProposalError(
            f"semantic span proposal schema validation failed: {errors[0].message}"
        )
    return row


def build_semantic_span_proposal_rows(input_root: Path) -> list[dict[str, Any]]:
    windows = _load_windows(input_root)
    cluster_by_window = _load_cluster_by_window(input_root)
    windows_by_conversation: dict[str, list[ReconstructedMessageWindow]] = {}
    for window in sorted(windows, key=_window_sort_key):
        windows_by_conversation.setdefault(window.conversation_id, []).append(window)

    rows: list[dict[str, Any]] = []
    for conversation_id in sorted(windows_by_conversation):
        conversation_windows = windows_by_conversation[conversation_id]
        index = 0
        while index < len(conversation_windows):
            window = conversation_windows[index]
            split = _split_candidate(window)
            if split is not None:
                rows.extend(_proposal_row(candidate) for candidate in split)
                index += 1
                continue

            if index + 1 < len(conversation_windows):
                merged = _merge_candidate(
                    window,
                    conversation_windows[index + 1],
                    cluster_by_window=cluster_by_window,
                )
                if merged is not None:
                    rows.append(_proposal_row(merged))
                    index += 2
                    continue

            rows.append(_proposal_row(_keep_candidate(window)))
            index += 1

    rows.sort(
        key=lambda row: (
            row["conversation_id"],
            row["source_window_ids"][0],
            row["span_id"],
        )
    )
    return rows


def load_semantic_span_proposal_rows(input_root: Path) -> list[dict[str, Any]]:
    path = semantic_span_proposals_path(input_root)
    if not path.exists():
        raise SemanticSpanProposalError(
            f"semantic span proposals artifact not found: {path}"
        )
    validator = load_semantic_span_proposal_validator()
    rows: list[dict[str, Any]] = []
    for line_no, row in _load_jsonl_rows(path):
        errors = list(validator.iter_errors(row))
        if errors:
            raise SemanticSpanProposalError(
                f"semantic span proposal schema validation failed for "
                f"{path}:{line_no}: {errors[0].message}"
            )
        rows.append(row)
    return rows


def _write_jsonl_artifact(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp_path.replace(path)
    return path


def write_semantic_span_proposals_artifact(input_root: Path) -> dict[str, Any]:
    provider_root = input_root.expanduser()
    if not provider_root.exists() or not provider_root.is_dir():
        raise SemanticSpanProposalError(f"provider root not found: {provider_root}")

    rows = build_semantic_span_proposal_rows(provider_root)
    output_dir = provider_root / "l3" / "semantic-span-proposals"
    output_dir.mkdir(parents=True, exist_ok=True)
    proposals_path = output_dir / "span_proposals.jsonl"
    summary_path = output_dir / "summary.json"

    _write_jsonl_artifact(proposals_path, rows)
    counts = {
        "split": sum(1 for row in rows if row["proposal_kind"] == "split"),
        "merge": sum(1 for row in rows if row["proposal_kind"] == "merge"),
        "keep": sum(1 for row in rows if row["proposal_kind"] == "keep"),
    }
    summary = {
        "artifact_type": "semantic_span_proposals_summary",
        "schema_version": "0.1",
        "proposal_count": len(rows),
        "proposal_kind_counts": counts,
        "proposals_path": str(proposals_path.resolve()),
    }
    write_json_artifact(summary_path, summary)
    return {
        "proposal_count": len(rows),
        "proposal_kind_counts": counts,
        "proposals_path": str(proposals_path),
        "summary_path": str(summary_path),
    }
