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
    load_token_dictionary_lexical_rules_validator,
    load_token_dictionary_validator,
    load_token_stats_validator,
    load_topics_validator,
)

TOKEN_DICTIONARY_SCHEMA_VERSION = "0.1"
TOKEN_DICTIONARY_ARTIFACT_TYPE = "token_dictionary"
TOKEN_BUNDLES_ARTIFACT_TYPE = "token_bundles"
TOKEN_DICTIONARY_PROVENANCE_ARTIFACT_TYPE = "token_dictionary_provenance"
TOKEN_DICTIONARY_LEXICAL_RULES_ARTIFACT_TYPE = "token_dictionary_lexical_rules"
TOKEN_DICTIONARY_DIRNAME = "token-dictionary"
_TOKEN_RE = re.compile(r"[a-z0-9_./:-]{2,}|[一-龯ぁ-んァ-ヶー]{2,}", re.IGNORECASE)
_DEFAULT_SOFT_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "or",
        "to",
        "in",
        "not",
        "this",
        "that",
        "you",
        "are",
        "your",
        "but",
        "from",
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
        "into",
        "それ",
        "これ",
        "こと",
        "でも",
        "って",
        "つまり",
        "だから",
        "うん",
        "この",
        "いや",
        "やで",
        "これは",
        "じゃなくて",
    }
)
_TOP_COOCCURRENCE_LIMIT = 8
_MAX_COOCCURRENCE_TOKENS_PER_MESSAGE = 16
_PUNCTUATION_ONLY_RE = re.compile(r"^[^\w一-龯ぁ-んァ-ヶー]+$", re.IGNORECASE)
_NUMBERED_MARKER_RE = re.compile(r"^\d+[.)]$")
_ENTITY_RESIDUE_TOKENS = frozenset({"quot", "nbsp", "amp", "lt", "gt"})
_DOMAINISH_PLAIN_TOKENS = frozenset(
    {
        "ai",
        "api",
        "json",
        "yaml",
        "yml",
        "csv",
        "sql",
        "cli",
        "sdk",
        "html",
        "http",
        "https",
        "note",
        "notes",
        "openai",
        "chatgpt",
        "markdown",
    }
)
_OVERDISTRIBUTED_LOW_VALUE_TOKENS = frozenset(
    {
        "also",
        "actually",
        "maybe",
        "then",
        "well",
        "over",
        "under",
        "very",
        "much",
        "many",
        "because",
        "should",
        "would",
        "could",
        "うん",
        "でも",
        "って",
        "つまり",
        "だから",
        "いや",
        "この",
        "それ",
        "これ",
    }
)
_DEFAULT_SEEDED_LEXICAL_RULES = {
    "specificity_generic_tokens": [
        "this",
        "that",
        "with",
        "from",
        "about",
        "there",
        "their",
        "your",
        "please",
        "thank",
        "thanks",
        "think",
        "really",
        "maybe",
        "would",
        "could",
        "should",
        "hello",
        "today",
        "thing",
        "stuff",
        "okay",
        "おはよう",
        "こんにちは",
        "こんばんは",
        "ありがとう",
        "了解",
        "はい",
        "うん",
        "それ",
        "これ",
        "あれ",
        "ここ",
        "そこ",
        "やね",
        "やで",
        "ほんま",
        "なるほど",
        "感じ"
    ],
    "reflective_tokens": [
        "feel",
        "feels",
        "felt",
        "think",
        "thought",
        "maybe",
        "probably",
        "honestly",
        "emotion",
        "vibe",
        "relationship",
        "memory",
        "remember",
        "reflection",
        "気持ち",
        "感覚",
        "記憶",
        "思い出",
        "雰囲気",
        "ノリ",
        "心地",
        "自然"
    ],
    "task_fragment_action_tokens": [
        "add",
        "adjust",
        "apply",
        "build",
        "change",
        "check",
        "configure",
        "debug",
        "deploy",
        "edit",
        "enable",
        "disable",
        "fail",
        "failure",
        "fix",
        "investigate",
        "rerun",
        "resume",
        "retry",
        "restore",
        "review",
        "run",
        "update",
        "validate",
        "verify",
        "修正",
        "更新",
        "確認",
        "検証",
        "再実行",
        "再開",
        "再試行",
        "再確認",
        "失敗",
        "対応",
        "調査",
        "修復",
        "確認する",
        "見直す",
        "直す",
        "試す"
    ],
    "task_fragment_state_tokens": [
        "after",
        "before",
        "blocked",
        "constraint",
        "error",
        "issue",
        "problem",
        "retry",
        "rollback",
        "timeout",
        "warning",
        "while",
        "条件",
        "制約",
        "問題",
        "原因",
        "対処",
        "復旧",
        "失敗",
        "エラー",
        "警告",
        "再開",
        "再試行"
    ],
    "task_fragment_explanatory_tokens": [
        "because",
        "benefit",
        "benefits",
        "compare",
        "comparison",
        "difference",
        "differences",
        "explain",
        "explanation",
        "explains",
        "explainer",
        "example",
        "examples",
        "meaning",
        "overview",
        "style",
        "useful",
        "versus",
        "why",
        "とは",
        "なぜ",
        "違い",
        "比較",
        "整理",
        "説明",
        "解説",
        "意味",
        "便利",
        "使い所",
        "向いてる"
    ],
    "task_fragment_noise_markers": [
        "the output of this plugin was redacted",
        "according to the search results",
        "based on the search results"
    ]
}


class TokenDictionaryError(RuntimeError):
    pass


@dataclass(frozen=True)
class TokenDictionaryLexicalRules:
    specificity_generic_tokens: frozenset[str]
    reflective_tokens: frozenset[str]
    task_fragment_action_tokens: frozenset[str]
    task_fragment_state_tokens: frozenset[str]
    task_fragment_explanatory_tokens: frozenset[str]
    task_fragment_noise_markers: tuple[str, ...]


@dataclass(frozen=True)
class TokenDictionarySignals:
    token_rows: dict[str, dict[str, Any]]
    token_to_bundles: dict[str, tuple[str, ...]]
    bundle_tokens: dict[str, frozenset[str]]


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


def token_dictionary_lexical_rules_path(input_root: Path) -> Path:
    return token_dictionary_dir(input_root) / "lexical_rules.json"


def _created_at_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _coerce_seeded_lexical_rules(raw_rules: dict[str, Any]) -> TokenDictionaryLexicalRules:
    def _token_set(key: str) -> frozenset[str]:
        values = raw_rules.get(key, [])
        if not isinstance(values, list):
            return frozenset()
        return frozenset(
            normalize_analysis_text(str(value))
            for value in values
            if isinstance(value, str) and value.strip()
        )

    noise_values = raw_rules.get("task_fragment_noise_markers", [])
    if not isinstance(noise_values, list):
        noise_values = []
    return TokenDictionaryLexicalRules(
        specificity_generic_tokens=_token_set("specificity_generic_tokens"),
        reflective_tokens=_token_set("reflective_tokens"),
        task_fragment_action_tokens=_token_set("task_fragment_action_tokens"),
        task_fragment_state_tokens=_token_set("task_fragment_state_tokens"),
        task_fragment_explanatory_tokens=_token_set("task_fragment_explanatory_tokens"),
        task_fragment_noise_markers=tuple(
            normalize_analysis_text(str(value))
            for value in noise_values
            if isinstance(value, str) and value.strip()
        ),
    )


def default_token_dictionary_lexical_rules() -> TokenDictionaryLexicalRules:
    return _coerce_seeded_lexical_rules(_DEFAULT_SEEDED_LEXICAL_RULES)


def build_token_dictionary_lexical_rules_artifact(
    *,
    provider_id: str,
    created_at: str,
) -> dict[str, Any]:
    return {
        "artifact_type": TOKEN_DICTIONARY_LEXICAL_RULES_ARTIFACT_TYPE,
        "schema_version": TOKEN_DICTIONARY_SCHEMA_VERSION,
        "producer_layer": "L3",
        "provider_id": provider_id,
        "created_at": created_at,
        "source_inputs": ["seeded_lexical_rules"],
        "reproducibility_note": "Rebuildable from seeded lexical resources bundled with this build",
        "seeded_rules": _DEFAULT_SEEDED_LEXICAL_RULES,
    }


def load_token_dictionary_lexical_rules(input_root: Path | None) -> TokenDictionaryLexicalRules:
    if input_root is None:
        return default_token_dictionary_lexical_rules()
    lexical_rules_path = token_dictionary_lexical_rules_path(input_root)
    if not lexical_rules_path.exists():
        return default_token_dictionary_lexical_rules()
    try:
        payload = json.loads(lexical_rules_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TokenDictionaryError(
            f"invalid JSON in {lexical_rules_path}: {exc.msg}"
        ) from exc
    errors = list(load_token_dictionary_lexical_rules_validator().iter_errors(payload))
    if errors:
        raise TokenDictionaryError(
            f"token dictionary lexical-rules schema validation failed for {lexical_rules_path}: {errors[0].message}"
        )
    seeded_rules = payload.get("seeded_rules")
    if not isinstance(seeded_rules, dict):
        raise TokenDictionaryError(
            f"invalid lexical rules artifact in {lexical_rules_path}: seeded_rules must be an object"
        )
    return _coerce_seeded_lexical_rules(seeded_rules)


def load_token_dictionary_signals(input_root: Path | None) -> TokenDictionarySignals | None:
    if input_root is None:
        return None

    dictionary_payload: dict[str, Any] | None = None
    bundles_payload: dict[str, Any] | None = None

    dictionary_path = token_dictionary_path(input_root)
    if dictionary_path.exists():
        try:
            dictionary_payload = json.loads(dictionary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise TokenDictionaryError(
                f"invalid JSON in {dictionary_path}: {exc.msg}"
            ) from exc
        errors = list(load_token_dictionary_validator().iter_errors(dictionary_payload))
        if errors:
            raise TokenDictionaryError(
                f"token dictionary schema validation failed for {dictionary_path}: {errors[0].message}"
            )

    bundles_path = token_bundles_path(input_root)
    if bundles_path.exists():
        try:
            bundles_payload = json.loads(bundles_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise TokenDictionaryError(
                f"invalid JSON in {bundles_path}: {exc.msg}"
            ) from exc
        errors = list(load_token_bundles_validator().iter_errors(bundles_payload))
        if errors:
            raise TokenDictionaryError(
                f"token bundles schema validation failed for {bundles_path}: {errors[0].message}"
            )

    if dictionary_payload is None and bundles_payload is None:
        return None

    token_rows: dict[str, dict[str, Any]] = {}
    if dictionary_payload is not None:
        for row in dictionary_payload.get("tokens", []):
            token = row.get("normalized")
            if isinstance(token, str) and token:
                token_rows[token] = row

    token_to_bundles: dict[str, set[str]] = defaultdict(set)
    bundle_tokens: dict[str, frozenset[str]] = {}
    if bundles_payload is not None:
        for row in bundles_payload.get("bundles", []):
            bundle_id = row.get("bundle_id")
            tokens = row.get("tokens")
            if not isinstance(bundle_id, str) or not bundle_id:
                continue
            if not isinstance(tokens, list):
                continue
            normalized_tokens = frozenset(
                normalize_analysis_text(str(token))
                for token in tokens
                if isinstance(token, str) and token.strip()
            )
            if len(normalized_tokens) < 2:
                continue
            bundle_tokens[bundle_id] = normalized_tokens
            for token in normalized_tokens:
                token_to_bundles[token].add(bundle_id)

    return TokenDictionarySignals(
        token_rows=token_rows,
        token_to_bundles={
            token: tuple(sorted(bundle_ids))
            for token, bundle_ids in token_to_bundles.items()
        },
        bundle_tokens=bundle_tokens,
    )


def _is_separator_token(token: str) -> bool:
    normalized = token.strip()
    if len(normalized) < 3:
        return False
    if len(set(normalized)) == 1 and normalized[0] in "-_=~.*•":
        return True
    return normalized in {"...", "---", "----", "___", "***"}


def _is_punctuation_only(token: str) -> bool:
    return bool(_PUNCTUATION_ONLY_RE.match(token.strip()))


def _is_numbered_marker(token: str) -> bool:
    return bool(_NUMBERED_MARKER_RE.match(token.strip()))


def _is_entity_residue(token: str) -> bool:
    return token.strip().casefold() in _ENTITY_RESIDUE_TOKENS


def _is_extremely_short_non_informative_token(token: str) -> bool:
    normalized = token.strip()
    if len(normalized) > 1:
        return False
    return not any(char.isalnum() for char in normalized)


def _accept_token(
    token: str,
    *,
    soft_stopwords: frozenset[str] | None = None,
) -> bool:
    normalized = token.strip().casefold()
    if not normalized:
        return False

    if _is_separator_token(normalized):
        return False
    if _is_punctuation_only(normalized):
        return False
    if _is_numbered_marker(normalized):
        return False
    if _is_entity_residue(normalized):
        return False
    if _is_extremely_short_non_informative_token(normalized):
        return False

    effective_stopwords = soft_stopwords if soft_stopwords is not None else _DEFAULT_SOFT_STOPWORDS
    if normalized in effective_stopwords:
        return False
    return True


def _is_soft_stopword(
    token: str,
    *,
    soft_stopwords: frozenset[str] | None = None,
) -> bool:
    effective_stopwords = soft_stopwords if soft_stopwords is not None else _DEFAULT_SOFT_STOPWORDS
    return token.strip().casefold() in effective_stopwords


def _is_domainish_token(token: str) -> bool:
    normalized = token.strip().casefold()
    if not normalized:
        return False
    if normalized in _DOMAINISH_PLAIN_TOKENS:
        return True
    if any(char in "/._:-" for char in normalized):
        return True
    has_alpha = any(char.isalpha() for char in normalized)
    has_digit = any(char.isdigit() for char in normalized)
    if has_alpha and has_digit:
        return True
    return len(normalized) >= 6


def _is_overdistributed_token(
    token: str,
    *,
    conversation_count: int,
    topic_count: int,
    total_conversation_count: int,
    total_topic_count: int,
) -> bool:
    normalized = token.strip().casefold()
    if not normalized or _is_domainish_token(normalized):
        return False
    conversation_ratio = (
        conversation_count / total_conversation_count
        if total_conversation_count > 0
        else 0.0
    )
    topic_ratio = (
        topic_count / total_topic_count
        if total_topic_count > 0
        else 0.0
    )
    if normalized in _OVERDISTRIBUTED_LOW_VALUE_TOKENS:
        return (
            (total_conversation_count >= 3 and conversation_ratio >= 0.67)
            or (total_topic_count >= 3 and topic_ratio >= 0.67)
        )
    return (
        len(normalized) <= 4
        and (
            (total_conversation_count >= 4 and conversation_ratio >= 0.8)
            or (total_topic_count >= 4 and topic_ratio >= 0.8)
        )
    )


def _lexical_tokens(text: str) -> list[str]:
    normalized = normalize_analysis_text(text)
    return [
        token
        for token in _TOKEN_RE.findall(normalized)
        if token and _accept_token(token)
    ]


def _bundle_eligible_tokens(
    tokens: list[str],
    *,
    excluded_tokens: frozenset[str] | None = None,
) -> list[str]:
    eligible = [
        token
        for token in sorted(set(tokens))
        if excluded_tokens is None or token not in excluded_tokens
        if (
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
    all_conversations: set[str] = set()
    all_topics: set[str] = set()

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
            all_conversations.add(row_conversation_id)
            text, _source = resolve_message_text(row)
            if not text:
                continue
            tokens = _lexical_tokens(text)
            if not tokens:
                continue
            timestamp = row.get("ts") if isinstance(row.get("ts"), int) else None
            message_topics = topic_index.get((row_conversation_id, message_id), set())
            all_topics.update(message_topics)

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

    total_conversation_count = len(all_conversations)
    total_topic_count = len(all_topics)
    overdistributed_tokens = frozenset(
        token
        for token, aggregate in aggregates.items()
        if _is_overdistributed_token(
            token,
            conversation_count=len(aggregate.conversations),
            topic_count=len(aggregate.topics),
            total_conversation_count=total_conversation_count,
            total_topic_count=total_topic_count,
        )
    )

    for parsed_path in parsed_files:
        for row in iter_parsed_records(parsed_path):
            if row.get("record_type") != "message":
                continue
            text, _source = resolve_message_text(row)
            if not text:
                continue
            tokens = _lexical_tokens(text)
            if not tokens:
                continue
            eligible_tokens = _bundle_eligible_tokens(
                tokens,
                excluded_tokens=overdistributed_tokens,
            )
            for index, token in enumerate(eligible_tokens):
                aggregate = aggregates[token]
                for other in eligible_tokens[index + 1 :]:
                    aggregate.cooccurrence[other] += 1
                    aggregates[other].cooccurrence[token] += 1

    token_rows: list[dict[str, Any]] = []
    for token, aggregate in sorted(
        aggregates.items(),
        key=lambda item: (-item[1].count, item[0]),
    ):
        if token in overdistributed_tokens:
            continue
        top_cooccurrence = [
            other
            for other, _count in aggregate.cooccurrence.most_common(_TOP_COOCCURRENCE_LIMIT)
            if other not in overdistributed_tokens
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
    lexical_rules_path = token_dictionary_lexical_rules_path(provider_root)
    artifact_paths = [dictionary_path, bundles_path, provenance_path, lexical_rules_path]
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
            "lexical_rules_path": lexical_rules_path,
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
    lexical_rules_artifact = build_token_dictionary_lexical_rules_artifact(
        provider_id=provider_id,
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
    lexical_errors = list(load_token_dictionary_lexical_rules_validator().iter_errors(lexical_rules_artifact))
    if lexical_errors:
        raise TokenDictionaryError(
            f"token dictionary lexical-rules schema validation failed: {lexical_errors[0].message}"
        )

    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        write_json_artifact(dictionary_path, dictionary_artifact)
        write_json_artifact(bundles_path, bundles_artifact)
        write_json_artifact(provenance_path, provenance_artifact)
        write_json_artifact(lexical_rules_path, lexical_rules_artifact)

    return {
        "provider_root": provider_root,
        "threads": len(parsed_files),
        "existing_artifacts": existing_artifacts,
        "skipped": False,
        "dry_run": dry_run,
        "dictionary_path": dictionary_path,
        "bundles_path": bundles_path,
        "provenance_path": provenance_path,
        "lexical_rules_path": lexical_rules_path,
        "token_count": dictionary_artifact["token_count"],
        "bundle_count": bundles_artifact["bundle_count"],
        "source_inputs": source_inputs,
    }
