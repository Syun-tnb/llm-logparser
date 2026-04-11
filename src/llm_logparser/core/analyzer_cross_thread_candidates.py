from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .analyzer_common import (
    normalize_analysis_text,
    normalized_similarity,
    write_json_artifact,
)
from .schema_validation import load_cross_thread_candidate_validator, load_topics_validator

CROSS_THREAD_CANDIDATE_SCHEMA_VERSION = "0.1"
CROSS_THREAD_CANDIDATE_RECORD_TYPE = "cross_thread_candidate"
CROSS_THREAD_CANDIDATE_SUMMARY_ARTIFACT_TYPE = "cross_thread_candidates_summary"
DEFAULT_CROSS_THREAD_MIN_SCORE = 0.6
DEFAULT_CROSS_THREAD_TOP_PER_SOURCE = 3

_LABEL_MATCH_SCORE = 0.45
_RAW_LABEL_MATCH_SCORE = 0.15
_KEYWORD_OVERLAP_LOW_SCORE = 0.1
_KEYWORD_OVERLAP_HIGH_SCORE = 0.2
_TOPIC_LABEL_SIMILARITY_MEDIUM_SCORE = 0.12
_TOPIC_LABEL_SIMILARITY_HIGH_SCORE = 0.2
_EXCERPT_SIMILARITY_LOW_SCORE = 0.12
_EXCERPT_SIMILARITY_MEDIUM_SCORE = 0.2
_EXCERPT_SIMILARITY_HIGH_SCORE = 0.3


class CrossThreadCandidateError(RuntimeError):
    pass


@dataclass(frozen=True)
class _RepresentativeSpanUnit:
    provider_id: str
    topic_id: str
    conversation_id: str
    span_id: str
    message_ids: tuple[str, ...]
    excerpt: str
    topic_label: str | None
    keywords: tuple[str, ...]
    normalized_label: str | None
    raw_label: str | None


@dataclass(frozen=True)
class _Evidence:
    score: float
    reason_codes: tuple[str, ...]
    excerpt_similarity: float
    topic_label_similarity: float
    shared_keywords: tuple[str, ...]
    normalized_label_match: bool
    raw_label_match: bool


def cross_thread_candidates_path(input_root: Path) -> Path:
    return input_root / "l3" / "cross-thread-candidates" / "candidates.jsonl"


def _load_topics_artifact(input_root: Path) -> dict[str, Any]:
    path = input_root / "l3" / "semantic-topics" / "topics.json"
    if not path.exists():
        raise CrossThreadCandidateError(
            f"semantic-topics artifact not found: {path}"
        )
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CrossThreadCandidateError(
            f"invalid JSON in {path}: {exc.msg}"
        ) from exc
    if not isinstance(artifact, dict):
        raise CrossThreadCandidateError(
            f"invalid topics artifact in {path}: expected object"
        )
    errors = list(load_topics_validator().iter_errors(artifact))
    if errors:
        raise CrossThreadCandidateError(
            f"topics schema validation failed for {path}: {errors[0].message}"
        )
    return artifact


def _normalized_keywords(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized = [
        normalize_analysis_text(value)
        for value in values
        if isinstance(value, str) and value.strip()
    ]
    return tuple(value for value in normalized if value)


def _representative_units(topics_artifact: dict[str, Any]) -> list[_RepresentativeSpanUnit]:
    provider_id = str(topics_artifact["provider_id"])
    units: list[_RepresentativeSpanUnit] = []
    for topic in topics_artifact["topics"]:
        topic_id = str(topic["topic_id"])
        topic_label = topic.get("label")
        normalized_topic_label = (
            " ".join(str(topic_label).split()) if isinstance(topic_label, str) and topic_label.strip() else None
        )
        keywords = tuple(
            str(keyword)
            for keyword in topic.get("keywords", [])
            if isinstance(keyword, str) and keyword.strip()
        )
        for span in topic["representative_spans"]:
            normalization = span.get("semantic_normalization")
            normalized_label = None
            raw_label = None
            if isinstance(normalization, dict):
                raw = normalization.get("raw_label")
                normalized = normalization.get("normalized_label")
                raw_label = str(raw) if isinstance(raw, str) and raw else None
                normalized_label = (
                    str(normalized) if isinstance(normalized, str) and normalized else None
                )
            units.append(
                _RepresentativeSpanUnit(
                    provider_id=provider_id,
                    topic_id=topic_id,
                    conversation_id=str(span["conversation_id"]),
                    span_id=str(span["span_id"]),
                    message_ids=tuple(str(message_id) for message_id in span["message_ids"]),
                    excerpt=str(span["excerpt"]),
                    topic_label=normalized_topic_label,
                    keywords=keywords,
                    normalized_label=normalized_label,
                    raw_label=raw_label,
                )
            )
    units.sort(
        key=lambda item: (
            item.conversation_id,
            item.topic_id,
            item.span_id,
        )
    )
    return units


def _evidence_for_pair(
    source: _RepresentativeSpanUnit,
    target: _RepresentativeSpanUnit,
) -> _Evidence | None:
    excerpt_similarity = round(
        normalized_similarity(source.excerpt, target.excerpt),
        4,
    )
    topic_label_similarity = round(
        normalized_similarity(source.topic_label or "", target.topic_label or ""),
        4,
    )
    source_keywords = set(_normalized_keywords(source.keywords))
    target_keywords = set(_normalized_keywords(target.keywords))
    shared_keywords = tuple(sorted(source_keywords & target_keywords))
    normalized_label_match = (
        source.normalized_label is not None
        and target.normalized_label is not None
        and source.normalized_label == target.normalized_label
    )
    raw_label_match = (
        source.raw_label is not None
        and target.raw_label is not None
        and source.raw_label == target.raw_label
    )

    score = 0.0
    reason_codes: list[str] = []
    if normalized_label_match:
        score += _LABEL_MATCH_SCORE
        reason_codes.append("normalized_label_match")
    if raw_label_match:
        score += _RAW_LABEL_MATCH_SCORE
        reason_codes.append("raw_label_match")

    if len(shared_keywords) >= 2:
        score += _KEYWORD_OVERLAP_HIGH_SCORE
        reason_codes.append("shared_keywords_high")
    elif len(shared_keywords) == 1:
        score += _KEYWORD_OVERLAP_LOW_SCORE
        reason_codes.append("shared_keywords_low")

    if topic_label_similarity >= 0.88:
        score += _TOPIC_LABEL_SIMILARITY_HIGH_SCORE
        reason_codes.append("topic_label_similarity_high")
    elif topic_label_similarity >= 0.72:
        score += _TOPIC_LABEL_SIMILARITY_MEDIUM_SCORE
        reason_codes.append("topic_label_similarity_medium")

    if excerpt_similarity >= 0.78:
        score += _EXCERPT_SIMILARITY_HIGH_SCORE
        reason_codes.append("excerpt_similarity_high")
    elif excerpt_similarity >= 0.64:
        score += _EXCERPT_SIMILARITY_MEDIUM_SCORE
        reason_codes.append("excerpt_similarity_medium")
    elif excerpt_similarity >= 0.52:
        score += _EXCERPT_SIMILARITY_LOW_SCORE
        reason_codes.append("excerpt_similarity_low")

    has_strong_signal = (
        excerpt_similarity >= 0.52
        or topic_label_similarity >= 0.72
        or len(shared_keywords) >= 1
    )
    if not has_strong_signal or not reason_codes:
        return None

    return _Evidence(
        score=round(min(score, 1.0), 4),
        reason_codes=tuple(reason_codes),
        excerpt_similarity=excerpt_similarity,
        topic_label_similarity=topic_label_similarity,
        shared_keywords=shared_keywords,
        normalized_label_match=normalized_label_match,
        raw_label_match=raw_label_match,
    )


def _candidate_row(
    *,
    source: _RepresentativeSpanUnit,
    target: _RepresentativeSpanUnit,
    evidence: _Evidence,
    rank: int,
) -> dict[str, Any]:
    row = {
        "record_type": CROSS_THREAD_CANDIDATE_RECORD_TYPE,
        "schema_version": CROSS_THREAD_CANDIDATE_SCHEMA_VERSION,
        "provider_id": source.provider_id,
        "source_conversation_id": source.conversation_id,
        "target_conversation_id": target.conversation_id,
        "source_topic_id": source.topic_id,
        "target_topic_id": target.topic_id,
        "source_span_id": source.span_id,
        "target_span_id": target.span_id,
        "source_message_ids": list(source.message_ids),
        "target_message_ids": list(target.message_ids),
        "source_excerpt": source.excerpt,
        "target_excerpt": target.excerpt,
        "source_topic_label": source.topic_label,
        "target_topic_label": target.topic_label,
        "source_normalized_label": source.normalized_label,
        "target_normalized_label": target.normalized_label,
        "source_raw_label": source.raw_label,
        "target_raw_label": target.raw_label,
        "score": evidence.score,
        "rank": rank,
        "evidence": {
            "reason_codes": list(evidence.reason_codes),
            "excerpt_similarity": evidence.excerpt_similarity,
            "topic_label_similarity": evidence.topic_label_similarity,
            "keyword_overlap_count": len(evidence.shared_keywords),
            "shared_keywords": list(evidence.shared_keywords),
            "normalized_label_match": evidence.normalized_label_match,
            "raw_label_match": evidence.raw_label_match,
        },
    }
    errors = list(load_cross_thread_candidate_validator().iter_errors(row))
    if errors:
        raise CrossThreadCandidateError(
            f"cross-thread candidate schema validation failed: {errors[0].message}"
        )
    return row


def build_cross_thread_candidate_rows(
    input_root: Path,
    *,
    min_score: float = DEFAULT_CROSS_THREAD_MIN_SCORE,
    top_per_source: int = DEFAULT_CROSS_THREAD_TOP_PER_SOURCE,
) -> list[dict[str, Any]]:
    if top_per_source < 1:
        raise CrossThreadCandidateError("top_per_source must be at least 1")
    if min_score < 0 or min_score > 1:
        raise CrossThreadCandidateError("min_score must be between 0 and 1")

    topics_artifact = _load_topics_artifact(input_root)
    units = _representative_units(topics_artifact)
    rows: list[dict[str, Any]] = []
    for source in units:
        ranked: list[tuple[_RepresentativeSpanUnit, _Evidence]] = []
        for target in units:
            if source.conversation_id == target.conversation_id:
                continue
            if source.topic_id == target.topic_id and source.span_id == target.span_id:
                continue
            evidence = _evidence_for_pair(source, target)
            if evidence is None or evidence.score < round(min_score, 4):
                continue
            ranked.append((target, evidence))

        ranked.sort(
            key=lambda item: (
                -item[1].score,
                -item[1].excerpt_similarity,
                item[0].conversation_id,
                item[0].topic_id,
                item[0].span_id,
            )
        )
        for rank, (target, evidence) in enumerate(ranked[:top_per_source], start=1):
            rows.append(
                _candidate_row(
                    source=source,
                    target=target,
                    evidence=evidence,
                    rank=rank,
                )
            )

    rows.sort(
        key=lambda row: (
            row["source_conversation_id"],
            row["source_topic_id"],
            row["source_span_id"],
            row["rank"],
            row["target_conversation_id"],
            row["target_topic_id"],
            row["target_span_id"],
        )
    )
    return rows


def _score_band(score: float) -> str:
    if score >= 0.9:
        return "high"
    if score >= 0.75:
        return "medium"
    return "low"


def _summary(
    *,
    topics_artifact: dict[str, Any],
    rows: list[dict[str, Any]],
    input_root: Path,
    min_score: float,
    top_per_source: int,
) -> dict[str, Any]:
    units = _representative_units(topics_artifact)
    reason_counts: Counter[str] = Counter()
    score_bands: Counter[str] = Counter()
    source_keys = {
        (
            row["source_conversation_id"],
            row["source_topic_id"],
            row["source_span_id"],
        )
        for row in rows
    }
    threads_involved = {
        row["source_conversation_id"] for row in rows
    } | {
        row["target_conversation_id"] for row in rows
    }
    for row in rows:
        for reason_code in row["evidence"]["reason_codes"]:
            reason_counts[str(reason_code)] += 1
        score_bands[_score_band(float(row["score"]))] += 1
    return {
        "artifact_type": CROSS_THREAD_CANDIDATE_SUMMARY_ARTIFACT_TYPE,
        "schema_version": CROSS_THREAD_CANDIDATE_SCHEMA_VERSION,
        "provider_id": topics_artifact["provider_id"],
        "generated_from": str((input_root / "l3" / "semantic-topics" / "topics.json").resolve()),
        "source_unit_count": len(units),
        "source_unit_with_candidates_count": len(source_keys),
        "candidate_link_count": len(rows),
        "thread_count_with_candidates": len(threads_involved),
        "guardrails": {
            "min_score": round(min_score, 4),
            "top_per_source": top_per_source,
        },
        "reason_counts": dict(sorted(reason_counts.items())),
        "score_band_counts": {
            band: score_bands.get(band, 0)
            for band in ("high", "medium", "low")
        },
    }


def write_cross_thread_candidates_artifact(
    input_root: Path,
    *,
    min_score: float = DEFAULT_CROSS_THREAD_MIN_SCORE,
    top_per_source: int = DEFAULT_CROSS_THREAD_TOP_PER_SOURCE,
) -> dict[str, Any]:
    provider_root = input_root.expanduser()
    if not provider_root.exists() or not provider_root.is_dir():
        raise CrossThreadCandidateError(f"provider root not found: {provider_root}")

    topics_artifact = _load_topics_artifact(provider_root)
    rows = build_cross_thread_candidate_rows(
        provider_root,
        min_score=min_score,
        top_per_source=top_per_source,
    )
    output_dir = provider_root / "l3" / "cross-thread-candidates"
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates_path = output_dir / "candidates.jsonl"
    summary_path = output_dir / "summary.json"

    tmp_candidates_path = candidates_path.with_suffix(".tmp")
    with tmp_candidates_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp_candidates_path.replace(candidates_path)

    summary = _summary(
        topics_artifact=topics_artifact,
        rows=rows,
        input_root=provider_root,
        min_score=min_score,
        top_per_source=top_per_source,
    )
    write_json_artifact(summary_path, summary)

    return {
        "candidate_count": len(rows),
        "candidates_path": candidates_path,
        "summary_path": summary_path,
    }
