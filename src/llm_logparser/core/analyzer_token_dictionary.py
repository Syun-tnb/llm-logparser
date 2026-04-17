from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .analyzer_common import (
    detect_header_metadata,
    normalize_analysis_text,
    string_or_none,
    write_json_artifact,
)
from .l1_derivation import discover_parsed_jsonl, iter_parsed_records, resolve_message_text
from .schema_validation import (
    load_token_bundles_validator,
    load_token_dictionary_provenance_validator,
    load_token_dictionary_validator,
    load_token_stats_validator,
    load_topics_validator,
)

TOKEN_DICTIONARY_SCHEMA_VERSION = "0.1"
TOKEN_DICTIONARY_ARTIFACT_TYPE = "token_dictionary"
TOKEN_BUNDLES_ARTIFACT_TYPE = "token_bundles"
TOKEN_DICTIONARY_PROVENANCE_ARTIFACT_TYPE = "token_dictionary_provenance"
TOKEN_DICTIONARY_DIRNAME = "token-dictionary"
_TOKEN_RE = re.compile(r"[a-z0-9_./:-]{2,}|[一-龯ぁ-んァ-ヶー]{2,}", re.IGNORECASE)
_GENERIC_TOKENS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "your",
        "have",
        "will",
        "just",
        "about",
        "really",
        "some",
        "more",
        "please",
        "thanks",
        "thank",
        "okay",
        "feel",
        "feels",
        "think",
        "thought",
        "what",
        "when",
        "where",
        "which",
        "there",
        "their",
        "they",
        "them",
        "explain",
        "comparison",
        "differences",
        "compare",
        "useful",
        "おはよう",
        "こんにちは",
        "こんばんは",
        "ありがとう",
        "了解",
        "なるほど",
        "それ",
        "これ",
        "あれ",
        "こと",
        "感じ",
        "説明",
        "比較",
    }
)
_TOP_COOCCURRENCE_LIMIT = 8
_MAX_COOCCURRENCE_TOKENS_PER_MESSAGE = 16


class TokenDictionaryError(RuntimeError):
    pass


@dataclass
class _TokenAggregate:
    token: str
    count: int = 0
    first_seen: int | None = None
    last_seen: int | None = None
    conversations: set[str] = field(default_factory=set)
    topics: set[str] = field(default_factory=set)
    role_counts: Counter[str] = field(default_factory=Counter)
    cooccurrence: Counter[str] = field(default_factory=Counter)


def token_dictionary_dir(input_root: Path) -> Path:
    return input_root / "l3" / TOKEN_DICTIONARY_DIRNAME


def token_dictionary_path(input_root: Path) -> Path:
    return token_dictionary_dir(input_root) / "dictionary.json"


def token_bundles_path(input_root: Path) -> Path:
    return token_dictionary_dir(input_root) / "bundles.json"


def token_dictionary_provenance_path(input_root: Path) -> Path:
    return token_dictionary_dir(input_root) / "provenance.json"


def _created_at_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _lexical_tokens(text: str) -> list[str]:
    normalized = normalize_analysis_text(text)
    return [token for token in _TOKEN_RE.findall(normalized) if token]


def _bundle_eligible_tokens(tokens: list[str]) -> list[str]:
    eligible = [
        token
        for token in sorted(set(tokens))
        if token not in _GENERIC_TOKENS
        and (
            len(token) >= 5
            or any(char.isdigit() for char in token)
            or any(char in "/._:-" for char in token)
        )
    ]
    return eligible[:_MAX_COOCCURRENCE_TOKENS_PER_MESSAGE]


def _load_topics_message_index(input_root: Path) -> dict[tuple[str, str], set[str]]:
    topics_path = input_root / "l3" / "semantic-topics" / "topics.json"
    if not topics_path.exists():
        return {}
    try:
        payload = json.loads(topics_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TokenDictionaryError(
            f"invalid JSON in {topics_path}: {exc.msg}"
        ) from exc
    errors = list(load_topics_validator().iter_errors(payload))
    if errors:
        raise TokenDictionaryError(
            f"topics schema validation failed for {topics_path}: {errors[0].message}"
        )
    index: dict[tuple[str, str], set[str]] = defaultdict(set)
    for topic in payload.get("topics", []):
        topic_id = topic.get("topic_id")
        if not isinstance(topic_id, str) or not topic_id:
            continue
        for ref in topic.get("message_refs", []):
            conversation_id = ref.get("conversation_id")
            message_id = ref.get("message_id")
            if isinstance(conversation_id, str) and conversation_id and isinstance(message_id, str) and message_id:
                index[(conversation_id, message_id)].add(topic_id)
    return index


def _validate_optional_token_stats(parsed_files: list[Path]) -> bool:
    found = False
    validator = load_token_stats_validator()
    for parsed_path in parsed_files:
        candidate = parsed_path.with_name("token_stats.json")
        if not candidate.exists():
            continue
        found = True
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise TokenDictionaryError(
                f"invalid JSON in {candidate}: {exc.msg}"
            ) from exc
        errors = list(validator.iter_errors(payload))
        if errors:
            raise TokenDictionaryError(
                f"token_stats schema validation failed for {candidate}: {errors[0].message}"
            )
    return found


def _provider_id_from_inputs(parsed_files: list[Path]) -> str:
    for parsed_path in parsed_files:
        provider_id, _conversation_id = detect_header_metadata(parsed_path)
        if provider_id:
            return provider_id
    return "unknown"


def _build_dictionary_artifact(
    *,
    input_root: Path,
    provider_id: str,
    source_inputs: list[str],
    created_at: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    parsed_files = discover_parsed_jsonl(input_root)
    topic_index = _load_topics_message_index(input_root)
    aggregates: dict[str, _TokenAggregate] = {}

    for parsed_path in parsed_files:
        conversation_id: str | None = None
        for row in iter_parsed_records(parsed_path):
            record_type = row.get("record_type")
            if record_type == "thread":
                conversation_id = string_or_none(row.get("conversation_id")) or conversation_id
                continue
            if record_type != "message":
                continue

            row_conversation_id = string_or_none(row.get("conversation_id")) or conversation_id or "unknown"
            message_id = string_or_none(row.get("message_id")) or ""
            role = string_or_none(row.get("role")) or "unknown"
            text, _source = resolve_message_text(row)
            if not text:
                continue
            tokens = _lexical_tokens(text)
            if not tokens:
                continue
            timestamp = row.get("ts") if isinstance(row.get("ts"), int) else None
            message_topics = topic_index.get((row_conversation_id, message_id), set())

            for token in tokens:
                aggregate = aggregates.setdefault(token, _TokenAggregate(token=token))
                aggregate.count += 1
                aggregate.conversations.add(row_conversation_id)
                aggregate.topics.update(message_topics)
                aggregate.role_counts[role] += 1
                if timestamp is not None:
                    aggregate.first_seen = (
                        timestamp
                        if aggregate.first_seen is None
                        else min(aggregate.first_seen, timestamp)
                    )
                    aggregate.last_seen = (
                        timestamp
                        if aggregate.last_seen is None
                        else max(aggregate.last_seen, timestamp)
                    )

            eligible_tokens = _bundle_eligible_tokens(tokens)
            for index, token in enumerate(eligible_tokens):
                aggregate = aggregates.setdefault(token, _TokenAggregate(token=token))
                for other in eligible_tokens[index + 1 :]:
                    aggregate.cooccurrence[other] += 1
                    other_aggregate = aggregates.setdefault(other, _TokenAggregate(token=other))
                    other_aggregate.cooccurrence[token] += 1

    token_rows: list[dict[str, Any]] = []
    for token, aggregate in sorted(
        aggregates.items(),
        key=lambda item: (-item[1].count, item[0]),
    ):
        top_cooccurrence = [
            other
            for other, _count in aggregate.cooccurrence.most_common(_TOP_COOCCURRENCE_LIMIT)
        ]
        token_rows.append(
            {
                "token": token,
                "normalized": token,
                "count": aggregate.count,
                "first_seen": aggregate.first_seen,
                "last_seen": aggregate.last_seen,
                "conversations": sorted(aggregate.conversations),
                "topics": sorted(aggregate.topics),
                "role_hints": dict(sorted(aggregate.role_counts.items())),
                "cooccurrence": top_cooccurrence,
                "conversation_count": len(aggregate.conversations),
                "topic_count": len(aggregate.topics),
            }
        )

    bundles = _build_token_bundles(token_rows, aggregates)
    reproducibility_note = "Rebuildable from canonical inputs"
    dictionary_artifact = {
        "artifact_type": TOKEN_DICTIONARY_ARTIFACT_TYPE,
        "schema_version": TOKEN_DICTIONARY_SCHEMA_VERSION,
        "producer_layer": "L3",
        "provider_id": provider_id,
        "created_at": created_at,
        "source_inputs": source_inputs,
        "reproducibility_note": reproducibility_note,
        "token_count": len(token_rows),
        "tokens": token_rows,
    }
    bundles_artifact = {
        "artifact_type": TOKEN_BUNDLES_ARTIFACT_TYPE,
        "schema_version": TOKEN_DICTIONARY_SCHEMA_VERSION,
        "producer_layer": "L3",
        "provider_id": provider_id,
        "created_at": created_at,
        "source_inputs": source_inputs,
        "reproducibility_note": reproducibility_note,
        "bundle_count": len(bundles),
        "bundles": bundles,
    }
    provenance_artifact = {
        "artifact_type": TOKEN_DICTIONARY_PROVENANCE_ARTIFACT_TYPE,
        "schema_version": TOKEN_DICTIONARY_SCHEMA_VERSION,
        "producer_layer": "L3",
        "provider_id": provider_id,
        "created_at": created_at,
        "source_inputs": source_inputs,
        "reproducibility_note": reproducibility_note,
        "token_count": len(token_rows),
        "bundle_count": len(bundles),
    }
    return dictionary_artifact, bundles_artifact, provenance_artifact


def _build_token_bundles(
    token_rows: list[dict[str, Any]],
    aggregates: dict[str, _TokenAggregate],
) -> list[dict[str, Any]]:
    bundles: list[dict[str, Any]] = []
    seen_sets: set[tuple[str, ...]] = set()
    for row in token_rows:
        token = row["token"]
        aggregate = aggregates[token]
        if aggregate.count < 2:
            continue
        candidate_neighbors: list[tuple[str, float, int]] = []
        for other, pair_count in aggregate.cooccurrence.items():
            other_aggregate = aggregates.get(other)
            if other_aggregate is None or other_aggregate.count < 2:
                continue
            weight = round(pair_count / max(1, min(aggregate.count, other_aggregate.count)), 4)
            if weight < 0.45:
                continue
            candidate_neighbors.append((other, weight, pair_count))
        candidate_neighbors.sort(key=lambda item: (-item[1], -item[2], item[0]))
        if not candidate_neighbors:
            continue
        bundle_tokens = sorted({token, *(neighbor for neighbor, _weight, _pair_count in candidate_neighbors[:2])})
        if len(bundle_tokens) < 2:
            continue
        bundle_key = tuple(bundle_tokens)
        if bundle_key in seen_sets:
            continue
        seen_sets.add(bundle_key)
        top_weights = [weight for _neighbor, weight, _pair_count in candidate_neighbors[: max(1, len(bundle_tokens) - 1)]]
        bundles.append(
            {
                "bundle_id": f"bundle_{len(bundles) + 1:03d}",
                "tokens": bundle_tokens,
                "weight": round(sum(top_weights) / len(top_weights), 4),
            }
        )
    bundles.sort(key=lambda item: (-item["weight"], item["bundle_id"]))
    return bundles


def write_token_dictionary_artifacts(
    input_root: Path,
    *,
    overwrite: bool = False,
    skip_existing: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    del overwrite  # default behavior already rebuilds existing derived artifacts
    provider_root = input_root.expanduser()
    if not provider_root.exists() or not provider_root.is_dir():
        raise TokenDictionaryError(f"provider root not found: {provider_root}")

    parsed_files = discover_parsed_jsonl(provider_root)
    source_inputs = ["parsed.jsonl"]
    if _validate_optional_token_stats(parsed_files):
        source_inputs.append("token_stats.json")
    if (provider_root / "l3" / "semantic-topics" / "topics.json").exists():
        source_inputs.append("topics.json")

    output_dir = token_dictionary_dir(provider_root)
    dictionary_path = token_dictionary_path(provider_root)
    bundles_path = token_bundles_path(provider_root)
    provenance_path = token_dictionary_provenance_path(provider_root)
    artifact_paths = [dictionary_path, bundles_path, provenance_path]
    existing_artifacts = [path for path in artifact_paths if path.exists()]
    complete_existing = len(existing_artifacts) == len(artifact_paths)

    if skip_existing and complete_existing:
        return {
            "provider_root": provider_root,
            "threads": len(parsed_files),
            "existing_artifacts": existing_artifacts,
            "skipped": True,
            "dry_run": dry_run,
            "dictionary_path": dictionary_path,
            "bundles_path": bundles_path,
            "provenance_path": provenance_path,
            "token_count": None,
            "bundle_count": None,
            "source_inputs": source_inputs,
        }

    provider_id = _provider_id_from_inputs(parsed_files)
    created_at = _created_at_iso()
    dictionary_artifact, bundles_artifact, provenance_artifact = _build_dictionary_artifact(
        input_root=provider_root,
        provider_id=provider_id,
        source_inputs=source_inputs,
        created_at=created_at,
    )

    dict_errors = list(load_token_dictionary_validator().iter_errors(dictionary_artifact))
    if dict_errors:
        raise TokenDictionaryError(
            f"token dictionary schema validation failed: {dict_errors[0].message}"
        )
    bundle_errors = list(load_token_bundles_validator().iter_errors(bundles_artifact))
    if bundle_errors:
        raise TokenDictionaryError(
            f"token bundles schema validation failed: {bundle_errors[0].message}"
        )
    prov_errors = list(load_token_dictionary_provenance_validator().iter_errors(provenance_artifact))
    if prov_errors:
        raise TokenDictionaryError(
            f"token dictionary provenance schema validation failed: {prov_errors[0].message}"
        )

    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        write_json_artifact(dictionary_path, dictionary_artifact)
        write_json_artifact(bundles_path, bundles_artifact)
        write_json_artifact(provenance_path, provenance_artifact)

    return {
        "provider_root": provider_root,
        "threads": len(parsed_files),
        "existing_artifacts": existing_artifacts,
        "skipped": False,
        "dry_run": dry_run,
        "dictionary_path": dictionary_path,
        "bundles_path": bundles_path,
        "provenance_path": provenance_path,
        "token_count": dictionary_artifact["token_count"],
        "bundle_count": bundles_artifact["bundle_count"],
        "source_inputs": source_inputs,
    }
