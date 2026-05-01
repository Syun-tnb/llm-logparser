from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from .analyzer_intra_thread_topics import (
    IntraThreadTopicsError,
    intra_thread_segments_artifact_path,
    intra_thread_topics_dir,
    reconstruct_thread_messages,
)
from .l1_derivation import discover_parsed_jsonl
from .schema_validation import load_intra_thread_topic_summary_validator

TOPIC_SUMMARY_SCHEMA_VERSION = "0.1"
SUMMARY_MAX_CHARS = 280
TITLE_MAX_CHARS = 72
TITLE_MAX_TOKENS = 8
KEYWORD_LIMIT = 8
KEYWORD_MIN_LENGTH = 2
TOKEN_RE = re.compile(r"[a-z][a-z0-9_/-]*|[一-龯ぁ-んァ-ヶー]+", re.IGNORECASE)
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "can",
        "for",
        "from",
        "how",
        "into",
        "not",
        "the",
        "this",
        "that",
        "these",
        "those",
        "with",
        "you",
        "your",
        "です",
        "ます",
        "これ",
        "それ",
        "この",
        "その",
        "こと",
        "ため",
        "よう",
        "ください",
        "お願いします",
    }
)


class IntraThreadTopicSummaryError(RuntimeError):
    pass


def intra_thread_topic_summaries_artifact_path(parsed_path: Path) -> Path:
    return intra_thread_topics_dir(parsed_path) / "topic-summaries.jsonl"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        handle = path.open("r", encoding="utf-8")
    except FileNotFoundError as exc:
        raise IntraThreadTopicSummaryError(f"segments artifact not found: {path}") from exc
    with handle:
        for line_no, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise IntraThreadTopicSummaryError(
                    f"invalid JSON in {path}:{line_no}: {exc.msg}"
                ) from exc
            if not isinstance(row, dict):
                raise IntraThreadTopicSummaryError(
                    f"invalid record in {path}:{line_no}: expected object"
                )
            rows.append(row)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(path)
    return path


def _compact_text(text: str) -> str:
    return " ".join(text.split()).strip()


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3].rstrip() + "..."


def _title_from_text(text: str) -> str:
    compact = _compact_text(text)
    if not compact:
        return ""
    first_line = next(
        (line.strip() for line in text.splitlines() if line.strip()),
        compact,
    )
    normalized = _compact_text(first_line)
    tokens = normalized.split()
    if len(tokens) > TITLE_MAX_TOKENS:
        normalized = " ".join(tokens[:TITLE_MAX_TOKENS])
    return _truncate(normalized, TITLE_MAX_CHARS)


def _keyword_tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    position = 0
    for token in TOKEN_RE.findall(normalized):
        token = token.strip()
        if not token or token in STOPWORDS or token.isdigit():
            continue
        if re.fullmatch(r"[a-z0-9_/-]+", token) and len(token) < KEYWORD_MIN_LENGTH:
            continue
        counts[token] = counts.get(token, 0) + 1
        first_seen.setdefault(token, position)
        position += 1
    ranked = sorted(counts, key=lambda item: (-counts[item], first_seen[item], item))
    return ranked[:KEYWORD_LIMIT]


def _coerce_segment_row(
    row: dict[str, Any],
    *,
    path: Path,
    line_no: int,
) -> dict[str, Any]:
    if row.get("record_type") != "intra_thread_segment":
        raise IntraThreadTopicSummaryError(
            f"invalid segment record_type in {path}:{line_no}"
        )
    required = (
        "provider_id",
        "conversation_id",
        "segment_id",
        "start_index",
        "end_index",
        "message_ids",
        "message_count",
        "text_sha1",
    )
    missing = [key for key in required if key not in row]
    if missing:
        raise IntraThreadTopicSummaryError(
            f"segment row missing required field(s) in {path}:{line_no}: "
            f"{', '.join(missing)}"
        )
    message_ids = row.get("message_ids")
    if not isinstance(message_ids, list) or not message_ids:
        raise IntraThreadTopicSummaryError(
            f"segment row has invalid message_ids in {path}:{line_no}"
        )
    if any(
        not isinstance(message_id, str) or not message_id
        for message_id in message_ids
    ):
        raise IntraThreadTopicSummaryError(
            f"segment row has invalid message_ids in {path}:{line_no}"
        )
    if row.get("message_count") != len(message_ids):
        raise IntraThreadTopicSummaryError(
            f"segment row message_count mismatch in {path}:{line_no}"
        )
    return row


def _segment_text(
    *,
    parsed_path: Path,
    segment_row: dict[str, Any],
    messages_by_id: dict[str, Any],
) -> str:
    texts: list[str] = []
    for message_id in segment_row["message_ids"]:
        message = messages_by_id.get(message_id)
        if message is None:
            raise IntraThreadTopicSummaryError(
                f"segment {segment_row['segment_id']} references unknown message_id "
                f"in {parsed_path}: {message_id}"
            )
        if message.provider_id != segment_row["provider_id"]:
            raise IntraThreadTopicSummaryError(
                f"segment {segment_row['segment_id']} provider_id mismatch for "
                f"message_id={message_id}"
            )
        if message.conversation_id != segment_row["conversation_id"]:
            raise IntraThreadTopicSummaryError(
                f"segment {segment_row['segment_id']} conversation_id mismatch for "
                f"message_id={message_id}"
            )
        if message.text:
            texts.append(message.text)
    text = "\n\n".join(texts)
    text_sha1 = hashlib.sha1(text.encode("utf-8")).hexdigest()
    if text_sha1 != segment_row["text_sha1"]:
        raise IntraThreadTopicSummaryError(
            f"segment text_sha1 drift for {segment_row['segment_id']} in {parsed_path}: "
            f"expected {segment_row['text_sha1']}, got {text_sha1}"
        )
    return text


def build_intra_thread_topic_summary_row(
    *,
    segment_row: dict[str, Any],
    segment_text: str,
) -> dict[str, Any]:
    compact = _compact_text(segment_text)
    return {
        "record_type": "intra_thread_topic_summary",
        "schema_version": TOPIC_SUMMARY_SCHEMA_VERSION,
        "provider_id": segment_row["provider_id"],
        "conversation_id": segment_row["conversation_id"],
        "segment_id": segment_row["segment_id"],
        "start_index": segment_row["start_index"],
        "end_index": segment_row["end_index"],
        "message_ids": list(segment_row["message_ids"]),
        "message_count": segment_row["message_count"],
        "segment_text_sha1": segment_row["text_sha1"],
        "title": _title_from_text(segment_text),
        "summary": _truncate(compact, SUMMARY_MAX_CHARS),
        "conclusion_text": None,
        "conclusion_status": "unknown",
        "keywords": _keyword_tokens(segment_text),
        "confidence": 0.3 if compact else 0.0,
        "source": "heuristic",
    }


def build_intra_thread_topic_summary_rows(parsed_path: Path) -> list[dict[str, Any]]:
    segments_path = intra_thread_segments_artifact_path(parsed_path)
    segment_rows = [
        _coerce_segment_row(row, path=segments_path, line_no=line_no)
        for line_no, row in enumerate(_load_jsonl(segments_path), start=1)
    ]
    if not segment_rows:
        return []

    try:
        messages = reconstruct_thread_messages(parsed_path)
    except IntraThreadTopicsError as exc:
        raise IntraThreadTopicSummaryError(str(exc)) from exc
    messages_by_id = {message.message_id: message for message in messages}

    rows = [
        build_intra_thread_topic_summary_row(
            segment_row=segment_row,
            segment_text=_segment_text(
                parsed_path=parsed_path,
                segment_row=segment_row,
                messages_by_id=messages_by_id,
            ),
        )
        for segment_row in segment_rows
    ]

    validator = load_intra_thread_topic_summary_validator()
    for index, row in enumerate(rows, start=1):
        errors = list(validator.iter_errors(row))
        if errors:
            raise IntraThreadTopicSummaryError(
                f"topic summary schema validation failed for row {index}: "
                f"{errors[0].message}"
            )
    return rows


def write_intra_thread_topic_summaries(
    input_path: Path,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    parsed_files = discover_parsed_jsonl(input_path)
    written_paths: list[Path] = []
    total_rows = 0

    for parsed_path in parsed_files:
        output_path = intra_thread_topic_summaries_artifact_path(parsed_path)
        if output_path.exists() and not overwrite:
            raise IntraThreadTopicSummaryError(
                f"artifact already exists: {output_path} (rerun with --overwrite)"
            )
        rows = build_intra_thread_topic_summary_rows(parsed_path)
        written_paths.append(_write_jsonl(output_path, rows))
        total_rows += len(rows)

    return {
        "threads": len(parsed_files),
        "summaries": total_rows,
        "artifacts": written_paths,
    }
