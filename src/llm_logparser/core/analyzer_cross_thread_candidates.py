from __future__ import annotations

import bisect
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .analyzer_common import (
    normalize_analysis_text,
    normalized_similarity,
    write_json_artifact,
)
from .analyzer_token_dictionary import (
    TokenDictionaryLexicalRules,
    TokenDictionarySignals,
    default_token_dictionary_lexical_rules,
    load_token_dictionary_lexical_rules,
    load_token_dictionary_signals,
)
from .l1_derivation import iter_input_message_records
from .analyzer_semantic_preview import WindowPreviewRecord, load_window_preview_index
from .embedding_backend import create_embedding_backend
from llm_logparser.resources.cross_thread_lexical import (
    CrossThreadLexicalRules,
    DEFAULT_CROSS_THREAD_LEXICAL_LOCALE,
    load_cross_thread_lexical_rules,
)
from .schema_validation import (
    load_cross_thread_candidate_validator,
    load_intra_thread_topic_summary_validator,
    load_topics_validator,
)

CROSS_THREAD_CANDIDATE_SCHEMA_VERSION = "0.3"
CROSS_THREAD_CANDIDATE_RECORD_TYPE = "cross_thread_candidate"
CROSS_THREAD_CANDIDATE_SUMMARY_ARTIFACT_TYPE = "cross_thread_candidates_summary"
DEFAULT_CROSS_THREAD_MIN_SCORE = 0.6
DEFAULT_CROSS_THREAD_TOP_PER_SOURCE = 3
DEFAULT_CROSS_THREAD_UNIT_SOURCE = "semantic-topics"
SUPPORTED_CROSS_THREAD_UNIT_SOURCES = frozenset(
    {"semantic-topics", "topic-summaries", "auto"}
)
_DAY_MS = 24 * 60 * 60 * 1000

_LABEL_MATCH_SCORE = 0.45
_RAW_LABEL_MATCH_SCORE = 0.15
_KEYWORD_OVERLAP_LOW_SCORE = 0.1
_KEYWORD_OVERLAP_HIGH_SCORE = 0.2
_TOPIC_LABEL_SIMILARITY_MEDIUM_SCORE = 0.12
_TOPIC_LABEL_SIMILARITY_HIGH_SCORE = 0.2
_EXCERPT_SIMILARITY_LOW_SCORE = 0.12
_EXCERPT_SIMILARITY_MEDIUM_SCORE = 0.2
_EXCERPT_SIMILARITY_HIGH_SCORE = 0.3
_TOPIC_EXCERPT_COMBINATION_HIGH_SCORE = 0.09
# Modest recurrence preference: enough to separate temporally distant revisits
# before top-k pruning, but not enough to overpower semantic similarity.
_TIMESTAMP_DISTANCE_MEDIUM_THRESHOLD_MS = 2 * _DAY_MS
_TIMESTAMP_DISTANCE_HIGH_THRESHOLD_MS = 7 * _DAY_MS
_TIMESTAMP_DISTANCE_MEDIUM_SCORE = 0.08
_TIMESTAMP_DISTANCE_HIGH_SCORE = 0.15
_SPECIFICITY_TOKEN_RE = re.compile(r"[a-z0-9_./:-]{3,}|[一-龯ぁ-んァ-ヶー]{2,}", re.IGNORECASE)
# Continuity masking is instrumentation only. With fewer than ~12 intervening
# messages or fewer than ~2 intervening representative spans, the pair is
# likely ongoing local continuity rather than reactivated recall.
_CONTINUITY_VOLUME_GAP_MESSAGE_THRESHOLD = 12
_CONTINUITY_VOLUME_GAP_SPAN_THRESHOLD = 2
_DORMANCY_MESSAGE_SCALE = 64
_DORMANCY_SPAN_SCALE = 8
_WEAK_RECURRENCE_TEMPORAL_GAP_SECONDS = 6 * 60 * 60
_WEAK_RECURRENCE_SPECIFICITY_THRESHOLD = 0.58
_WEAK_RECURRENCE_LOCAL_CONTEXT_DELTA_THRESHOLD = 0.18
_WEAK_RECURRENCE_TASK_LIKE_THRESHOLD = 0.52
_WEAK_RECURRENCE_SINGLE_ANCHOR_TASK_LIKE_THRESHOLD = 0.68
_WEAK_RECURRENCE_REFLECTIVE_THRESHOLD = 0.14
_WEAK_ROUTE_ANCHOR_OVERLAP_SCORE = 0.05
_WEAK_ROUTE_ANCHOR_OVERLAP_STRONG_SCORE = 0.08
_WEAK_ROUTE_DORMANT_GAP_SCORE = 0.04
_WEAK_ROUTE_SPECIFICITY_SCORE = 0.03
_WEAK_ROUTE_TASK_LIKE_SCORE = 0.03
_WEAK_ROUTE_CONTEXT_SHIFT_SCORE = 0.02
_DICTIONARY_TOKEN_OVERLAP_WEAK_SCORE = 0.02
_DICTIONARY_TOKEN_OVERLAP_DENSE_SCORE = 0.08
_BUNDLE_OVERLAP_BROAD_SCORE = 0.01
_BUNDLE_OVERLAP_CONCENTRATED_SCORE = 0.05
_NUCLEUS_OVERLAP_SPECIFIC_SCORE = 0.03
_EXPLICIT_CONCLUSION_OVERLAP_SCORE = 0.08
_SUMMARY_LOCAL_LLM_SOURCE_BONUS = 0.04
_SUMMARY_HEURISTIC_SOURCE_PENALTY = 0.04
_TOPIC_SUMMARY_TITLE_OVERLAP_HIGH_SCORE = 0.28
_TOPIC_SUMMARY_TITLE_OVERLAP_MEDIUM_SCORE = 0.16
_TOPIC_SUMMARY_TITLE_OVERLAP_LOW_SCORE = 0.08
_TOPIC_SUMMARY_KEYPHRASE_OVERLAP_HIGH_SCORE = 0.24
_TOPIC_SUMMARY_KEYPHRASE_OVERLAP_MEDIUM_SCORE = 0.14
_TOPIC_SUMMARY_KEYPHRASE_OVERLAP_LOW_SCORE = 0.07
_TOPIC_SUMMARY_KEYWORD_OVERLAP_HIGH_SCORE = 0.3
_TOPIC_SUMMARY_KEYWORD_OVERLAP_LOW_SCORE = 0.16
_TOPIC_SUMMARY_DISTINCTIVE_TOKEN_SCORE = 0.08
_TOPIC_SUMMARY_DISTINCTIVE_TOKEN_STRONG_SCORE = 0.14
_TOPIC_SUMMARY_LOCAL_LLM_PAIR_SCORE = 0.08
_TOPIC_SUMMARY_HEURISTIC_PENALTY = 0.04
_TOPIC_SUMMARY_TIMESTAMP_DISTANCE_HIGH_SCORE = 0.03
_TOPIC_SUMMARY_TIMESTAMP_DISTANCE_MEDIUM_SCORE = 0.015
_TOPIC_SUMMARY_ANCHOR_OVERLAP_SCORE = 0.02
_TOPIC_SUMMARY_ANCHOR_OVERLAP_STRONG_SCORE = 0.04
_ANCHOR_TOKEN_SYMBOLS = frozenset("/._:-")
_TOPIC_SUMMARY_GENERIC_SCORING_KEYWORDS = frozenset(
    {
        "ai",
        "chat",
        "check",
        "company",
        "conversation",
        "data",
        "daily",
        "date",
        "day",
        "entity",
        "error",
        "greeting",
        "link",
        "model",
        "models",
        "month",
        "noting",
        "open",
        "page",
        "request",
        "search",
        "shared",
        "speaker",
        "speakers",
        "suggests",
        "system",
        "time",
        "view",
        "viewing",
        "web",
        "while",
        "year",
        "www",
        "w",
        "これは",
        "これはね",
        "さん",
        "して",
        "その",
        "って",
        "うん",
        "わたし",
        "あはは",
        "あはははは",
        "おはようございます",
        "こんにちは",
        "こんばんは",
        "笑",
        "年",
        "月",
        "日",
    }
)
_TOPIC_SUMMARY_SHORT_SPECIFIC_SCORING_KEYWORDS = frozenset(
    {
        "api",
        "btc",
        "cpu",
        "css",
        "csv",
        "dca",
        "dns",
        "etl",
        "etf",
        "gpu",
        "json",
        "llm",
        "pdf",
        "pr",
        "sql",
        "ui",
        "ux",
        "yaml",
    }
)
_TOPIC_SUMMARY_DISTINCTIVE_ALLOW_TOKENS = frozenset[str]()
_TOPIC_SUMMARY_DISTINCTIVE_BLOCK_TOKENS = frozenset[str]()
_TOPIC_SUMMARY_WEAK_DISTINCTIVE_TOKENS = frozenset[str]()
_TOPIC_SUMMARY_PERSONA_WEAK_TOKENS = frozenset[str]()
_TOPIC_SUMMARY_GENERIC_SCORING_PATTERNS = (
    r"^turn\d+search\d*$",
    r"^turn\d+(?:fetch|open|view|news|finance|weather|sports)\d*$",
    r"^websearch\d*$",
)
_TOPIC_SUMMARY_TOOL_RESIDUE_PATTERNS = _TOPIC_SUMMARY_GENERIC_SCORING_PATTERNS
_TOPIC_SUMMARY_CITATION_RESIDUE_PATTERNS = (
    r"^cite$",
    r"^search$",
)
_TOPIC_SUMMARY_RITUAL_TITLE_PHRASES = (
    "morning check-in",
    "daily check-in",
)
_FALLBACK_TOPIC_SUMMARY_GENERIC_ADMISSION_ANCHORS = frozenset(
    {
        "ai",
        "gpt",
        "gpt-4o",
        "gpt4o",
        "gpt-5",
        "gpt5",
        "human-like",
        "humanlike",
        "philosophy",
        "long-term",
        "longterm",
        "prompt",
        "プロンプト",
    }
)
_FALLBACK_TOPIC_SUMMARY_GENERIC_ADMISSION_ANCHOR_PATTERNS = (
    r"^turn\d+search\d*$",
    r"^turn\d+(?:fetch|open|view|news|finance|weather|sports)\d*$",
    r"^websearch\d*$",
)
_SELECTIVE_CONTEXT_MIN_FRAGMENT_SCORE = 0.34
_SELECTIVE_CONTEXT_TOP_FRAGMENTS = 3
_SELECTIVE_CONTEXT_MAX_CHARS = 240


class CrossThreadCandidateError(RuntimeError):
    pass


@dataclass(frozen=True)
class _TopicSummaryScoringFeatures:
    title_tokens: tuple[str, ...] = ()
    summary_keyphrases: tuple[str, ...] = ()
    keyword_tokens: tuple[str, ...] = ()
    distinctive_token_profile: dict[str, dict[str, int]] = field(default_factory=dict)


@dataclass(frozen=True)
class _RepresentativeSpanUnit:
    provider_id: str
    topic_id: str
    conversation_id: str
    span_id: str
    message_ids: tuple[str, ...]
    source_window_ids: tuple[str, ...]
    excerpt: str
    task_nucleus_text: str
    task_fragment_view: str
    topic_label: str | None
    keywords: tuple[str, ...]
    normalized_label: str | None
    raw_label: str | None
    first_seen: int | None
    last_seen: int | None
    excerpt_specificity: float
    reflective_score: float
    anchor_tokens: tuple[str, ...]
    strong_anchor_tokens: tuple[str, ...]
    task_like_score: float
    dictionary_tokens: tuple[str, ...]
    bundle_ids: tuple[str, ...]
    fragment_dictionary_support: float
    nucleus_token_count: int
    dictionary_token_mass: float
    dictionary_density: float
    residue_like: bool
    unit_kind: str = "semantic_topic_span"
    segment_id: str | None = None
    summary_source: str | None = None
    summary_confidence: float | None = None
    conclusion_status: str | None = None
    explicit_conclusion_text: str | None = None
    summary_text: str = ""
    topic_summary_scoring: _TopicSummaryScoringFeatures = field(
        default_factory=_TopicSummaryScoringFeatures
    )


@dataclass(frozen=True)
class _Evidence:
    score: float
    reason_codes: tuple[str, ...]
    excerpt_similarity: float
    topic_label_similarity: float
    shared_keywords: tuple[str, ...]
    normalized_label_match: bool
    raw_label_match: bool
    timestamp_delta_ms: int | None
    volume_gap: int | None
    temporal_gap_seconds: int | None
    continuity_mask: bool
    dormancy_score: float
    specificity_score: float
    local_context_delta: float | None


@dataclass(frozen=True)
class _RecurrenceInstrumentationContext:
    message_timestamps: tuple[int, ...]
    span_timestamps: tuple[int, ...]
    previous_target_by_key: dict[tuple[str, str], _RepresentativeSpanUnit]


_SimilarityCache = dict[tuple[str, str], float]


@dataclass(frozen=True)
class _VolumeGap:
    value: int | None
    unit: str | None


@dataclass(frozen=True)
class _PairSignals:
    excerpt_similarity: float
    topic_label_similarity: float
    shared_keywords: tuple[str, ...]
    normalized_label_match: bool
    raw_label_match: bool
    timestamp_delta_ms: int | None
    temporal_gap_seconds: int | None
    volume_gap: _VolumeGap
    continuity_mask: bool
    dormancy_score: float
    specificity_score: float
    local_context_delta: float | None
    shared_anchor_tokens: tuple[str, ...]
    shared_strong_anchor_tokens: tuple[str, ...]
    shared_dictionary_tokens: tuple[str, ...]
    shared_bundle_ids: tuple[str, ...]
    shared_dictionary_token_mass: float
    dictionary_overlap_ratio: float
    bundle_concentration: float
    min_dictionary_density: float
    task_like_score: float
    reflective_score: float
    fragment_dictionary_support: float
    residue_pair: bool
    explicit_conclusion_overlap: bool
    local_llm_pair: bool
    heuristic_pair: bool


@dataclass(frozen=True)
class _TopicSummaryLoadStats:
    files_found: int = 0
    units_loaded: int = 0
    skipped_invalid: int = 0
    skipped_empty: int = 0
    skipped_low_confidence: int = 0


@dataclass(frozen=True)
class _UnitLoadResult:
    unit_source: str
    topics_artifact: dict[str, Any] | None
    units: list[_RepresentativeSpanUnit]
    topic_summary_stats: _TopicSummaryLoadStats


@dataclass(frozen=True)
class _TopicSummaryAdmissionStats:
    filtered_count: int = 0
    filter_reasons: Counter[str] | None = None


@dataclass(frozen=True)
class _WeakRecurrenceCandidate:
    evidence: _Evidence
    shared_anchor_count: int


@dataclass(frozen=True)
class _ScoredFragment:
    index: int
    text: str
    score: float


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


def _text_specificity_score(
    text: str,
    lexical_rules: TokenDictionaryLexicalRules | None = None,
) -> float:
    lexical_rules = lexical_rules or default_token_dictionary_lexical_rules()
    normalized = normalize_analysis_text(text)
    tokens = [token for token in _SPECIFICITY_TOKEN_RE.findall(normalized) if token]
    if not tokens:
        return 0.0
    unique_ratio = len(set(tokens)) / len(tokens)
    content_tokens = [
        token
        for token in tokens
        if token not in lexical_rules.specificity_generic_tokens
    ]
    content_ratio = len(content_tokens) / len(tokens)
    long_content_ratio = (
        sum(
            1
            for token in content_tokens
            if len(token) >= 6 or any(char.isdigit() for char in token)
        )
        / len(tokens)
    )
    content_length_factor = min(
        1.0,
        sum(len(token) for token in content_tokens) / 80.0,
    )
    return round(
        min(
            1.0,
            0.3 * content_ratio
            + 0.25 * unique_ratio
            + 0.25 * long_content_ratio
            + 0.2 * content_length_factor,
        ),
        4,
    )


def _token_list(text: str) -> list[str]:
    normalized = normalize_analysis_text(text)
    return [token for token in _SPECIFICITY_TOKEN_RE.findall(normalized) if token]


def _action_token_set(
    lexical_rules: TokenDictionaryLexicalRules,
    cross_thread_rules: CrossThreadLexicalRules,
) -> frozenset[str]:
    return lexical_rules.task_fragment_action_tokens | cross_thread_rules.task.verbs


def _state_token_set(
    lexical_rules: TokenDictionaryLexicalRules,
    cross_thread_rules: CrossThreadLexicalRules,
) -> frozenset[str]:
    return lexical_rules.task_fragment_state_tokens | cross_thread_rules.task.nouns


def _reflective_token_set(
    lexical_rules: TokenDictionaryLexicalRules,
    cross_thread_rules: CrossThreadLexicalRules,
) -> frozenset[str]:
    return lexical_rules.reflective_tokens | cross_thread_rules.reflective.markers


def _normalize_anchor_token(token: str) -> str:
    return token.strip(".,;!?()[]{}\"'")


def _is_strong_anchor_like_token(
    token: str,
    lexical_rules: TokenDictionaryLexicalRules | None = None,
) -> bool:
    lexical_rules = lexical_rules or default_token_dictionary_lexical_rules()
    token = _normalize_anchor_token(token)
    if token in lexical_rules.specificity_generic_tokens or len(token) < 4:
        return False
    has_symbol = any(char in _ANCHOR_TOKEN_SYMBOLS for char in token)
    has_digit = any(char.isdigit() for char in token)
    has_alpha = any(char.isalpha() for char in token)
    if has_symbol and (has_alpha or has_digit):
        return True
    if has_digit and len(token) >= 5 and has_alpha:
        return True
    if has_digit and len(token) >= 8:
        return True
    return False


def _is_anchor_like_token(
    token: str,
    lexical_rules: TokenDictionaryLexicalRules | None = None,
) -> bool:
    lexical_rules = lexical_rules or default_token_dictionary_lexical_rules()
    token = _normalize_anchor_token(token)
    if _is_strong_anchor_like_token(token, lexical_rules):
        return True
    if token in lexical_rules.specificity_generic_tokens or len(token) < 4:
        return False
    has_cjk = bool(re.search(r"[一-龯ぁ-んァ-ヶー]", token))
    if has_cjk:
        return len(token) >= 6
    return len(token) >= 12


def _anchor_tokens_for_texts(
    values: tuple[str, ...],
    lexical_rules: TokenDictionaryLexicalRules | None = None,
) -> tuple[str, ...]:
    lexical_rules = lexical_rules or default_token_dictionary_lexical_rules()
    tokens: set[str] = set()
    for value in values:
        normalized = normalize_analysis_text(value)
        for token in _SPECIFICITY_TOKEN_RE.findall(normalized):
            normalized_token = _normalize_anchor_token(token)
            if _is_anchor_like_token(normalized_token, lexical_rules):
                tokens.add(normalized_token)
    return tuple(sorted(tokens))


def _strong_anchor_tokens_for_texts(
    values: tuple[str, ...],
    lexical_rules: TokenDictionaryLexicalRules | None = None,
) -> tuple[str, ...]:
    lexical_rules = lexical_rules or default_token_dictionary_lexical_rules()
    tokens: set[str] = set()
    for value in values:
        normalized = normalize_analysis_text(value)
        for token in _SPECIFICITY_TOKEN_RE.findall(normalized):
            normalized_token = _normalize_anchor_token(token)
            if _is_strong_anchor_like_token(normalized_token, lexical_rules):
                tokens.add(normalized_token)
    return tuple(sorted(tokens))


def _task_like_text_score(
    text: str,
    anchor_tokens: tuple[str, ...],
    strong_anchor_tokens: tuple[str, ...],
    lexical_rules: TokenDictionaryLexicalRules | None = None,
    cross_thread_rules: CrossThreadLexicalRules | None = None,
) -> float:
    lexical_rules = lexical_rules or default_token_dictionary_lexical_rules()
    cross_thread_rules = cross_thread_rules or load_cross_thread_lexical_rules(
        DEFAULT_CROSS_THREAD_LEXICAL_LOCALE
    )
    tokens = _token_list(text)
    if not tokens:
        return 0.0
    reflective_tokens = _reflective_token_set(lexical_rules, cross_thread_rules)
    reflective_count = sum(1 for token in tokens if token in reflective_tokens)
    reflective_ratio = reflective_count / len(tokens)
    strong_anchor_bonus = min(0.35, 0.18 * len(strong_anchor_tokens))
    anchor_bonus = min(0.18, 0.06 * max(0, len(anchor_tokens) - len(strong_anchor_tokens)))
    text_specificity = _text_specificity_score(text, lexical_rules)
    reflective_penalty = min(0.3, 0.8 * reflective_ratio)
    if reflective_count >= 2 and len(strong_anchor_tokens) <= 2:
        reflective_penalty = min(0.45, reflective_penalty + 0.18)
    return round(
        max(
            0.0,
            min(
                1.0,
                0.72 * text_specificity
                + strong_anchor_bonus
                + anchor_bonus
                - reflective_penalty,
            ),
        ),
        4,
    )


def _reflective_text_score(
    text: str,
    lexical_rules: TokenDictionaryLexicalRules | None = None,
    cross_thread_rules: CrossThreadLexicalRules | None = None,
) -> float:
    lexical_rules = lexical_rules or default_token_dictionary_lexical_rules()
    cross_thread_rules = cross_thread_rules or load_cross_thread_lexical_rules(
        DEFAULT_CROSS_THREAD_LEXICAL_LOCALE
    )
    tokens = _token_list(text)
    if not tokens:
        return 0.0
    reflective_tokens = _reflective_token_set(lexical_rules, cross_thread_rules)
    return round(
        sum(1 for token in tokens if token in reflective_tokens)
        / len(tokens),
        4,
    )


def _dictionary_tokens_for_text(
    text: str,
    token_dictionary_signals: TokenDictionarySignals | None,
) -> tuple[str, ...]:
    if token_dictionary_signals is None:
        return ()
    return tuple(
        sorted(
            {
                token
                for token in _token_list(text)
                if token in token_dictionary_signals.token_rows
            }
        )
    )


def _dictionary_token_weight(
    token: str,
    token_dictionary_signals: TokenDictionarySignals | None,
) -> float:
    if token_dictionary_signals is None:
        return 1.0
    row = token_dictionary_signals.token_rows.get(token)
    if not isinstance(row, dict):
        return 1.0
    conversation_count = row.get("conversation_count")
    topic_count = row.get("topic_count")
    try:
        conversation_count = int(conversation_count)
    except (TypeError, ValueError):
        conversation_count = 0
    try:
        topic_count = int(topic_count)
    except (TypeError, ValueError):
        topic_count = 0
    spread = max(conversation_count, topic_count)
    if spread >= 5:
        return 0.35
    if spread >= 3:
        return 0.6
    return 1.0


def _dictionary_token_mass(
    tokens: tuple[str, ...],
    token_dictionary_signals: TokenDictionarySignals | None,
) -> float:
    return round(
        sum(
            _dictionary_token_weight(token, token_dictionary_signals)
            for token in tokens
        ),
        4,
    )


def _nucleus_dictionary_density(
    nucleus_text: str,
    dictionary_tokens: tuple[str, ...],
    token_dictionary_signals: TokenDictionarySignals | None,
) -> float:
    nucleus_token_count = len(set(_token_list(nucleus_text)))
    if nucleus_token_count <= 0:
        return 0.0
    return round(
        _dictionary_token_mass(dictionary_tokens, token_dictionary_signals)
        / nucleus_token_count,
        4,
    )


def _bundle_ids_for_tokens(
    tokens: tuple[str, ...],
    token_dictionary_signals: TokenDictionarySignals | None,
) -> tuple[str, ...]:
    if token_dictionary_signals is None or not tokens:
        return ()
    bundle_ids: set[str] = set()
    for token in tokens:
        bundle_ids.update(token_dictionary_signals.token_to_bundles.get(token, ()))
    return tuple(sorted(bundle_ids))


def _fragment_dictionary_support(
    text: str,
    token_dictionary_signals: TokenDictionarySignals | None,
) -> float:
    if token_dictionary_signals is None:
        return 0.0
    dictionary_tokens = _dictionary_tokens_for_text(text, token_dictionary_signals)
    if not dictionary_tokens:
        return 0.0
    bundle_hits = 0
    for bundle_id in _bundle_ids_for_tokens(dictionary_tokens, token_dictionary_signals):
        bundle_tokens = token_dictionary_signals.bundle_tokens.get(bundle_id, frozenset())
        if len(bundle_tokens & set(dictionary_tokens)) >= 2:
            bundle_hits += 1
    return round(
        min(
            1.0,
            0.18 * min(4, len(dictionary_tokens)) + 0.24 * min(2, bundle_hits),
        ),
        4,
    )


def _is_meta_structural_fragment(
    fragment: str,
    *,
    lexical_rules: TokenDictionaryLexicalRules,
    cross_thread_rules: CrossThreadLexicalRules,
) -> bool:
    normalized = normalize_analysis_text(fragment)
    if not normalized:
        return False
    broad_overlap_markers = cross_thread_rules.broad_overlap.markers
    broad_overlap_phrase_markers = tuple(
        marker
        for marker in broad_overlap_markers
        if " " in marker or "_" in marker
    )
    broad_overlap_token_markers = frozenset(broad_overlap_markers) - set(broad_overlap_phrase_markers)
    if any(marker in normalized for marker in broad_overlap_phrase_markers):
        return True
    tokens = _token_list(fragment)
    if not tokens:
        return False
    meta_hits = sum(1 for token in tokens if token in broad_overlap_token_markers)
    action_tokens = _action_token_set(lexical_rules, cross_thread_rules)
    state_tokens = _state_token_set(lexical_rules, cross_thread_rules)
    action_hits = sum(
        1 for token in tokens if token in action_tokens
    )
    state_hits = sum(
        1 for token in tokens if token in state_tokens
    )
    strong_anchor_tokens = _strong_anchor_tokens_for_texts((fragment,), lexical_rules)
    return meta_hits >= 2 and action_hits == 0 and state_hits == 0 and not strong_anchor_tokens


def _is_prompt_residue_fragment(
    fragment: str,
    *,
    cross_thread_rules: CrossThreadLexicalRules | None = None,
) -> bool:
    cross_thread_rules = cross_thread_rules or load_cross_thread_lexical_rules(
        DEFAULT_CROSS_THREAD_LEXICAL_LOCALE
    )
    normalized = normalize_analysis_text(fragment)
    if not normalized:
        return False
    prompt_markers = cross_thread_rules.residue.prompt_exact_markers
    marker_hits = sum(1 for marker in prompt_markers if marker in normalized)
    if marker_hits >= 2:
        return True
    infrastructure_terms = cross_thread_rules.residue.composite.infrastructure_terms
    control_terms = cross_thread_rules.residue.composite.control_terms
    has_wrapper = any(
        term in normalized
        for term in infrastructure_terms
        if "plugin" not in term and "tool" not in term and "file_search" not in term
    )
    has_turn_control = any(term in normalized for term in control_terms)
    if has_wrapper and has_turn_control:
        return True
    imperative_hits = sum(
        1
        for marker in control_terms
        if marker in normalized
    )
    return imperative_hits >= 2 and (
        "image" in normalized
        or "turn" in normalized
        or "returned" in normalized
        or "followup" in normalized
    )


def _is_system_tool_residue_fragment(
    fragment: str,
    *,
    cross_thread_rules: CrossThreadLexicalRules | None = None,
) -> bool:
    cross_thread_rules = cross_thread_rules or load_cross_thread_lexical_rules(
        DEFAULT_CROSS_THREAD_LEXICAL_LOCALE
    )
    normalized = normalize_analysis_text(fragment)
    if not normalized:
        return False
    system_tool_markers = cross_thread_rules.residue.system_tool_exact_markers
    marker_hits = sum(1 for marker in system_tool_markers if marker in normalized)
    if marker_hits >= 2:
        return True
    infrastructure_terms = cross_thread_rules.residue.composite.infrastructure_terms
    control_terms = cross_thread_rules.residue.composite.control_terms
    return (
        any(term in normalized for term in infrastructure_terms)
        and (
            "redacted" in normalized
            or "truncated" in normalized
            or "accessible via" in normalized
            or any(term in normalized for term in control_terms)
        )
    )


def _is_residue_fragment(
    fragment: str,
    *,
    cross_thread_rules: CrossThreadLexicalRules | None = None,
) -> bool:
    return _is_prompt_residue_fragment(
        fragment,
        cross_thread_rules=cross_thread_rules,
    ) or _is_system_tool_residue_fragment(
        fragment,
        cross_thread_rules=cross_thread_rules,
    )


def _has_action_object_pattern(
    *,
    action_hits: int,
    strong_anchor_tokens: tuple[str, ...],
    dictionary_support: float,
) -> bool:
    return action_hits > 0 and (
        bool(strong_anchor_tokens) or dictionary_support >= 0.3
    )


def _has_state_target_pattern(
    *,
    state_hits: int,
    anchor_tokens: tuple[str, ...],
    dictionary_support: float,
) -> bool:
    return state_hits > 0 and (bool(anchor_tokens) or dictionary_support >= 0.3)


def _has_concrete_task_shape(
    *,
    action_hits: int,
    state_hits: int,
    anchor_tokens: tuple[str, ...],
    strong_anchor_tokens: tuple[str, ...],
    dictionary_support: float,
) -> bool:
    if _has_action_object_pattern(
        action_hits=action_hits,
        strong_anchor_tokens=strong_anchor_tokens,
        dictionary_support=dictionary_support,
    ):
        return True
    if _has_state_target_pattern(
        state_hits=state_hits,
        anchor_tokens=anchor_tokens,
        dictionary_support=dictionary_support,
    ):
        return True
    return (
        action_hits > 0
        and state_hits > 0
        and (bool(anchor_tokens) or dictionary_support >= 0.22)
    )


def _split_span_into_fragments(text: str) -> list[str]:
    normalized_text = " ".join(text.split())
    if not normalized_text:
        return []
    return [
        fragment.strip(" \t-:;,.!?/|")
        for fragment in re.split(r"[\n\r]+|(?<=[。！？.!?;；])\s+", normalized_text)
        if fragment.strip()
    ]


def _score_fragment(
    fragment: str,
    *,
    lexical_rules: TokenDictionaryLexicalRules,
    token_dictionary_signals: TokenDictionarySignals | None,
    cross_thread_rules: CrossThreadLexicalRules | None = None,
) -> float:
    cross_thread_rules = cross_thread_rules or load_cross_thread_lexical_rules(
        DEFAULT_CROSS_THREAD_LEXICAL_LOCALE
    )
    normalized_fragment = normalize_analysis_text(fragment)
    if not normalized_fragment:
        return 0.0
    if any(marker in normalized_fragment for marker in lexical_rules.task_fragment_noise_markers):
        return -1.0
    if _is_residue_fragment(fragment, cross_thread_rules=cross_thread_rules):
        return -1.25
    if _is_meta_structural_fragment(
        fragment,
        lexical_rules=lexical_rules,
        cross_thread_rules=cross_thread_rules,
    ):
        return -0.35

    tokens = _token_list(fragment)
    if not tokens:
        return 0.0
    anchor_tokens = _anchor_tokens_for_texts((fragment,), lexical_rules)
    strong_anchor_tokens = _strong_anchor_tokens_for_texts((fragment,), lexical_rules)
    dictionary_support = _fragment_dictionary_support(fragment, token_dictionary_signals)
    action_tokens = _action_token_set(lexical_rules, cross_thread_rules)
    state_tokens = _state_token_set(lexical_rules, cross_thread_rules)
    action_hits = sum(
        1 for token in tokens
        if token in action_tokens
    )
    state_hits = sum(
        1 for token in tokens
        if token in state_tokens
    )
    action_hits += sum(
        1 for phrase in cross_thread_rules.task.phrases if phrase in normalized_fragment
    )
    explanatory_hits = sum(
        1 for token in tokens
        if token in lexical_rules.task_fragment_explanatory_tokens
    )
    reflective_score = _reflective_text_score(
        fragment,
        lexical_rules,
        cross_thread_rules,
    )
    if (
        explanatory_hits > 0
        and strong_anchor_tokens
        and action_hits == 0
        and state_hits == 0
        ):
        return -0.5

    score = 0.0
    has_concrete_task_shape = _has_concrete_task_shape(
        action_hits=action_hits,
        state_hits=state_hits,
        anchor_tokens=anchor_tokens,
        strong_anchor_tokens=strong_anchor_tokens,
        dictionary_support=dictionary_support,
    )
    if strong_anchor_tokens:
        score += 0.45
    elif anchor_tokens:
        score += 0.22
    score += min(0.36, 0.18 * action_hits)
    score += min(0.24, 0.12 * state_hits)
    score += min(0.18, 0.22 * _text_specificity_score(fragment, lexical_rules))
    if has_concrete_task_shape:
        score += 0.16
        score += min(0.18, 0.24 * dictionary_support)
    else:
        score += min(0.04, 0.08 * dictionary_support)
    # Phrase-level broad-overlap markers are handled by the earlier
    # _is_meta_structural_fragment() fast path. The incremental meta penalty
    # here only applies token-level broad-overlap vocabulary.
    broad_overlap_token_markers = {
        marker
        for marker in cross_thread_rules.broad_overlap.markers
        if " " not in marker and "_" not in marker
    }
    meta_hits = sum(1 for token in tokens if token in broad_overlap_token_markers)
    if meta_hits > 0 and not has_concrete_task_shape:
        score -= min(0.28, 0.1 * meta_hits)
    score -= min(0.32, 0.16 * explanatory_hits)
    score -= min(0.3, 0.75 * reflective_score)
    return round(score, 4)


def _select_fragments(
    fragments: list[str],
    *,
    lexical_rules: TokenDictionaryLexicalRules,
    token_dictionary_signals: TokenDictionarySignals | None,
    cross_thread_rules: CrossThreadLexicalRules,
) -> list[_ScoredFragment]:
    scored = [
        _ScoredFragment(
            index=index,
            text=fragment,
            score=_score_fragment(
                fragment,
                lexical_rules=lexical_rules,
                token_dictionary_signals=token_dictionary_signals,
                cross_thread_rules=cross_thread_rules,
            ),
        )
        for index, fragment in enumerate(fragments)
    ]
    selected = [
        fragment
        for fragment in sorted(
            scored,
            key=lambda item: (-item.score, item.index),
        )[:_SELECTIVE_CONTEXT_TOP_FRAGMENTS]
        if fragment.score >= _SELECTIVE_CONTEXT_MIN_FRAGMENT_SCORE
    ]
    return sorted(selected, key=lambda item: item.index)


def _build_task_nucleus(
    text: str,
    *,
    lexical_rules: TokenDictionaryLexicalRules,
    token_dictionary_signals: TokenDictionarySignals | None,
    cross_thread_rules: CrossThreadLexicalRules | None = None,
) -> str:
    cross_thread_rules = cross_thread_rules or load_cross_thread_lexical_rules(
        DEFAULT_CROSS_THREAD_LEXICAL_LOCALE
    )
    normalized_text = " ".join(text.split())
    if not normalized_text:
        return ""

    fragments = _split_span_into_fragments(normalized_text)
    if not fragments:
        return ""

    retained = _select_fragments(
        fragments,
        lexical_rules=lexical_rules,
        token_dictionary_signals=token_dictionary_signals,
        cross_thread_rules=cross_thread_rules,
    )
    if retained:
        merged: list[str] = []
        current_block = retained[0].text
        previous_index = retained[0].index
        for fragment in retained[1:]:
            if fragment.index == previous_index + 1:
                current_block = f"{current_block} {fragment.text}"
            else:
                merged.append(current_block)
                current_block = fragment.text
            previous_index = fragment.index
        merged.append(current_block)
        nucleus = " | ".join(merged)
        return nucleus[:_SELECTIVE_CONTEXT_MAX_CHARS].rstrip()

    return ""


def _task_nucleus_text(
    text: str,
    *,
    lexical_rules: TokenDictionaryLexicalRules,
    token_dictionary_signals: TokenDictionarySignals | None,
    cross_thread_rules: CrossThreadLexicalRules | None = None,
) -> str:
    cross_thread_rules = cross_thread_rules or load_cross_thread_lexical_rules(
        DEFAULT_CROSS_THREAD_LEXICAL_LOCALE
    )
    if _is_residue_fragment(text, cross_thread_rules=cross_thread_rules):
        return ""
    nucleus = _build_task_nucleus(
        text,
        lexical_rules=lexical_rules,
        token_dictionary_signals=token_dictionary_signals,
        cross_thread_rules=cross_thread_rules,
    )
    return nucleus or " ".join(text.split())


def _task_fragment_view(
    text: str,
    lexical_rules: TokenDictionaryLexicalRules | None = None,
    token_dictionary_signals: TokenDictionarySignals | None = None,
    cross_thread_rules: CrossThreadLexicalRules | None = None,
) -> str:
    lexical_rules = lexical_rules or default_token_dictionary_lexical_rules()
    cross_thread_rules = cross_thread_rules or load_cross_thread_lexical_rules(
        DEFAULT_CROSS_THREAD_LEXICAL_LOCALE
    )
    return _build_task_nucleus(
        text,
        lexical_rules=lexical_rules,
        token_dictionary_signals=token_dictionary_signals,
        cross_thread_rules=cross_thread_rules,
    )


def _representative_units(
    topics_artifact: dict[str, Any],
    *,
    lexical_rules: TokenDictionaryLexicalRules | None = None,
    token_dictionary_signals: TokenDictionarySignals | None = None,
    cross_thread_rules: CrossThreadLexicalRules | None = None,
) -> list[_RepresentativeSpanUnit]:
    lexical_rules = lexical_rules or default_token_dictionary_lexical_rules()
    cross_thread_rules = cross_thread_rules or load_cross_thread_lexical_rules(
        DEFAULT_CROSS_THREAD_LEXICAL_LOCALE
    )
    provider_id = str(topics_artifact["provider_id"])
    units: list[_RepresentativeSpanUnit] = []
    for topic in topics_artifact["topics"]:
        topic_id = str(topic["topic_id"])
        first_seen = topic.get("first_seen")
        topic_first_seen = first_seen if isinstance(first_seen, int) else None
        last_seen = topic.get("last_seen")
        topic_last_seen = last_seen if isinstance(last_seen, int) else None
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
            source_window_ids = tuple(
                str(window_id)
                for window_id in span.get("source_window_ids", [])
                if isinstance(window_id, str) and window_id
            )
            if not source_window_ids:
                window_id = span.get("window_id")
                if isinstance(window_id, str) and window_id:
                    source_window_ids = (window_id,)
            anchor_source_values = tuple(
                value
                for value in (
                    normalized_topic_label or "",
                    _task_fragment_view(
                        str(span["excerpt"]),
                        lexical_rules,
                        token_dictionary_signals,
                        cross_thread_rules,
                    ),
                    *keywords,
                )
                if value
            )
            anchor_tokens = _anchor_tokens_for_texts(anchor_source_values, lexical_rules)
            strong_anchor_tokens = _strong_anchor_tokens_for_texts(anchor_source_values, lexical_rules)
            task_fragment_view = _task_fragment_view(
                str(span["excerpt"]),
                lexical_rules,
                token_dictionary_signals,
                cross_thread_rules,
            )
            task_nucleus_text = _task_nucleus_text(
                str(span["excerpt"]),
                lexical_rules=lexical_rules,
                token_dictionary_signals=token_dictionary_signals,
                cross_thread_rules=cross_thread_rules,
            )
            dictionary_source_text = task_nucleus_text or str(span["excerpt"])
            dictionary_tokens = _dictionary_tokens_for_text(
                dictionary_source_text,
                token_dictionary_signals,
            )
            bundle_ids = _bundle_ids_for_tokens(dictionary_tokens, token_dictionary_signals)
            nucleus_token_count = len(set(_token_list(task_nucleus_text)))
            dictionary_token_mass = _dictionary_token_mass(
                dictionary_tokens,
                token_dictionary_signals,
            )
            dictionary_density = _nucleus_dictionary_density(
                task_nucleus_text,
                dictionary_tokens,
                token_dictionary_signals,
            )
            fragment_dictionary_support = _fragment_dictionary_support(
                dictionary_source_text,
                token_dictionary_signals,
            )
            residue_like = _is_residue_fragment(
                str(span["excerpt"]),
                cross_thread_rules=cross_thread_rules,
            )
            units.append(
                _RepresentativeSpanUnit(
                    provider_id=provider_id,
                    topic_id=topic_id,
                    conversation_id=str(span["conversation_id"]),
                    span_id=str(span["span_id"]),
                    message_ids=tuple(str(message_id) for message_id in span["message_ids"]),
                    source_window_ids=source_window_ids,
                    excerpt=str(span["excerpt"]),
                    task_nucleus_text=task_nucleus_text,
                    task_fragment_view=task_fragment_view,
                    topic_label=normalized_topic_label,
                    keywords=keywords,
                    normalized_label=normalized_label,
                    raw_label=raw_label,
                    first_seen=topic_first_seen,
                    last_seen=topic_last_seen,
                    excerpt_specificity=_text_specificity_score(task_fragment_view, lexical_rules),
                    reflective_score=_reflective_text_score(
                        task_fragment_view,
                        lexical_rules,
                        cross_thread_rules,
                    ),
                    anchor_tokens=anchor_tokens,
                    strong_anchor_tokens=strong_anchor_tokens,
                    task_like_score=_task_like_text_score(
                        task_fragment_view,
                        anchor_tokens,
                        strong_anchor_tokens,
                        lexical_rules,
                        cross_thread_rules,
                    ),
                    dictionary_tokens=dictionary_tokens,
                    bundle_ids=bundle_ids,
                    fragment_dictionary_support=fragment_dictionary_support,
                    nucleus_token_count=nucleus_token_count,
                    dictionary_token_mass=dictionary_token_mass,
                    dictionary_density=dictionary_density,
                    residue_like=residue_like,
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


def _topic_summary_matching_text(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("title", "summary"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(" ".join(value.split()))
    keywords = row.get("keywords")
    if isinstance(keywords, list):
        keyword_text = " ".join(
            str(keyword).strip()
            for keyword in keywords
            if isinstance(keyword, str) and keyword.strip()
        )
        if keyword_text:
            parts.append(keyword_text)
    conclusion_text = row.get("conclusion_text")
    if row.get("conclusion_status") == "explicit" and isinstance(conclusion_text, str):
        conclusion = " ".join(conclusion_text.split())
        if conclusion:
            parts.append(conclusion)
    return "\n".join(parts)


def _message_timestamp_bounds(
    parsed_path: Path,
    message_ids: tuple[str, ...],
) -> tuple[int | None, int | None]:
    wanted = set(message_ids)
    timestamps: list[int] = []
    try:
        with parsed_path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if row.get("record_type") != "message":
                    continue
                if row.get("message_id") not in wanted:
                    continue
                ts = row.get("ts")
                if isinstance(ts, int):
                    timestamps.append(ts)
    except (FileNotFoundError, json.JSONDecodeError):
        return None, None
    if not timestamps:
        return None, None
    return min(timestamps), max(timestamps)


def _topic_summary_units(
    input_root: Path,
    *,
    lexical_rules: TokenDictionaryLexicalRules | None = None,
    token_dictionary_signals: TokenDictionarySignals | None = None,
    cross_thread_rules: CrossThreadLexicalRules | None = None,
) -> tuple[list[_RepresentativeSpanUnit], _TopicSummaryLoadStats]:
    lexical_rules = lexical_rules or default_token_dictionary_lexical_rules()
    cross_thread_rules = cross_thread_rules or load_cross_thread_lexical_rules(
        DEFAULT_CROSS_THREAD_LEXICAL_LOCALE
    )
    validator = load_intra_thread_topic_summary_validator()
    summary_paths = sorted(
        input_root.glob("thread-*/l3/intra-thread-topics/topic-summaries.jsonl")
    )
    units: list[_RepresentativeSpanUnit] = []
    skipped_invalid = 0
    skipped_empty = 0
    skipped_low_confidence = 0

    for summary_path in summary_paths:
        parsed_path = summary_path.parents[2] / "parsed.jsonl"
        try:
            raw_lines = summary_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            skipped_invalid += 1
            continue
        for line in raw_lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                skipped_invalid += 1
                continue
            if not isinstance(row, dict):
                skipped_invalid += 1
                continue
            if list(validator.iter_errors(row)):
                skipped_invalid += 1
                continue

            title = row.get("title")
            summary = row.get("summary")
            if not (
                isinstance(title, str)
                and title.strip()
                or isinstance(summary, str)
                and summary.strip()
            ):
                skipped_empty += 1
                continue

            source = str(row.get("source"))
            confidence_raw = row.get("confidence")
            confidence = float(confidence_raw) if isinstance(confidence_raw, (int, float)) else 0.0
            if source == "local_llm" and confidence <= 0.1:
                skipped_low_confidence += 1
                continue

            message_ids = tuple(
                str(message_id)
                for message_id in row.get("message_ids", [])
                if isinstance(message_id, str) and message_id
            )
            matching_text = _topic_summary_matching_text(row)
            if not matching_text.strip() or not message_ids:
                skipped_empty += 1
                continue
            keywords = tuple(
                str(keyword)
                for keyword in row.get("keywords", [])
                if isinstance(keyword, str) and keyword.strip()
            )
            first_seen, last_seen = _message_timestamp_bounds(parsed_path, message_ids)
            topic_label = " ".join(title.split()) if isinstance(title, str) and title.strip() else None
            summary_text = (
                " ".join(summary.split())
                if isinstance(summary, str) and summary.strip()
                else ""
            )
            task_nucleus_text = _task_nucleus_text(
                matching_text,
                lexical_rules=lexical_rules,
                token_dictionary_signals=token_dictionary_signals,
                cross_thread_rules=cross_thread_rules,
            )
            task_fragment_view = _task_fragment_view(
                matching_text,
                lexical_rules,
                token_dictionary_signals,
                cross_thread_rules,
            )
            dictionary_source_text = task_nucleus_text or matching_text
            dictionary_tokens = _dictionary_tokens_for_text(
                dictionary_source_text,
                token_dictionary_signals,
            )
            bundle_ids = _bundle_ids_for_tokens(dictionary_tokens, token_dictionary_signals)
            anchor_source_values = tuple(
                value
                for value in (
                    topic_label or "",
                    task_fragment_view,
                    *keywords,
                )
                if value
            )
            anchor_tokens = _anchor_tokens_for_texts(anchor_source_values, lexical_rules)
            strong_anchor_tokens = _strong_anchor_tokens_for_texts(anchor_source_values, lexical_rules)
            segment_id = str(row["segment_id"])
            explicit_conclusion = row.get("conclusion_text")
            explicit_conclusion_text = (
                " ".join(explicit_conclusion.split())
                if row.get("conclusion_status") == "explicit"
                and isinstance(explicit_conclusion, str)
                and explicit_conclusion.strip()
                else None
            )
            topic_summary_scoring = _topic_summary_scoring_features(
                topic_label=topic_label,
                summary_text=summary_text or matching_text,
                keywords=keywords,
                cross_thread_rules=cross_thread_rules,
            )
            units.append(
                _RepresentativeSpanUnit(
                    provider_id=str(row["provider_id"]),
                    topic_id=f"{row['conversation_id']}:{segment_id}",
                    conversation_id=str(row["conversation_id"]),
                    span_id=segment_id,
                    message_ids=message_ids,
                    source_window_ids=(),
                    excerpt=matching_text,
                    task_nucleus_text=task_nucleus_text,
                    task_fragment_view=task_fragment_view,
                    topic_label=topic_label,
                    keywords=keywords,
                    normalized_label=None,
                    raw_label=None,
                    first_seen=first_seen,
                    last_seen=last_seen,
                    excerpt_specificity=_text_specificity_score(
                        task_fragment_view,
                        lexical_rules,
                    ),
                    reflective_score=_reflective_text_score(
                        task_fragment_view,
                        lexical_rules,
                        cross_thread_rules,
                    ),
                    anchor_tokens=anchor_tokens,
                    strong_anchor_tokens=strong_anchor_tokens,
                    task_like_score=_task_like_text_score(
                        task_fragment_view,
                        anchor_tokens,
                        strong_anchor_tokens,
                        lexical_rules,
                        cross_thread_rules,
                    ),
                    dictionary_tokens=dictionary_tokens,
                    bundle_ids=bundle_ids,
                    fragment_dictionary_support=_fragment_dictionary_support(
                        dictionary_source_text,
                        token_dictionary_signals,
                    ),
                    nucleus_token_count=len(set(_token_list(task_nucleus_text))),
                    dictionary_token_mass=_dictionary_token_mass(
                        dictionary_tokens,
                        token_dictionary_signals,
                    ),
                    dictionary_density=_nucleus_dictionary_density(
                        task_nucleus_text,
                        dictionary_tokens,
                        token_dictionary_signals,
                    ),
                    residue_like=_is_residue_fragment(
                        matching_text,
                        cross_thread_rules=cross_thread_rules,
                    ),
                    unit_kind="topic_summary",
                    segment_id=segment_id,
                    summary_source=source,
                    summary_confidence=round(confidence, 4),
                    conclusion_status=str(row.get("conclusion_status")),
                    explicit_conclusion_text=explicit_conclusion_text,
                    summary_text=summary_text,
                    topic_summary_scoring=topic_summary_scoring,
                )
            )

    units.sort(
        key=lambda item: (
            item.conversation_id,
            item.topic_id,
            item.span_id,
        )
    )
    return units, _TopicSummaryLoadStats(
        files_found=len(summary_paths),
        units_loaded=len(units),
        skipped_invalid=skipped_invalid,
        skipped_empty=skipped_empty,
        skipped_low_confidence=skipped_low_confidence,
    )


def _load_units(
    input_root: Path,
    *,
    requested_unit_source: str,
    lexical_rules: TokenDictionaryLexicalRules,
    token_dictionary_signals: TokenDictionarySignals | None,
    cross_thread_rules: CrossThreadLexicalRules,
) -> _UnitLoadResult:
    if requested_unit_source not in SUPPORTED_CROSS_THREAD_UNIT_SOURCES:
        raise CrossThreadCandidateError(
            "--unit-source must be semantic-topics, topic-summaries, or auto"
        )

    topic_units: list[_RepresentativeSpanUnit] = []
    topic_stats = _TopicSummaryLoadStats()
    if requested_unit_source in {"topic-summaries", "auto"}:
        topic_units, topic_stats = _topic_summary_units(
            input_root,
            lexical_rules=lexical_rules,
            token_dictionary_signals=token_dictionary_signals,
            cross_thread_rules=cross_thread_rules,
        )
        if requested_unit_source == "topic-summaries":
            if not topic_units:
                raise CrossThreadCandidateError(
                    "no usable topic summary units found under provider root"
                )
            return _UnitLoadResult(
                unit_source="topic-summaries",
                topics_artifact=None,
                units=topic_units,
                topic_summary_stats=topic_stats,
            )
        if topic_units:
            return _UnitLoadResult(
                unit_source="topic-summaries",
                topics_artifact=None,
                units=topic_units,
                topic_summary_stats=topic_stats,
            )

    topics_artifact = _load_topics_artifact(input_root)
    return _UnitLoadResult(
        unit_source="semantic-topics",
        topics_artifact=topics_artifact,
        units=_representative_units(
            topics_artifact,
            lexical_rules=lexical_rules,
            token_dictionary_signals=token_dictionary_signals,
            cross_thread_rules=cross_thread_rules,
        ),
        topic_summary_stats=topic_stats,
    )


def _build_recurrence_instrumentation_context(
    input_root: Path,
    units: list[_RepresentativeSpanUnit],
) -> _RecurrenceInstrumentationContext:
    message_timestamps: list[int] = []
    try:
        for row in iter_input_message_records(input_root):
            ts = row.get("ts")
            if isinstance(ts, int):
                message_timestamps.append(ts)
    except (FileNotFoundError, ValueError):
        message_timestamps = []
    message_timestamps.sort()

    span_timestamps = sorted(
        unit.first_seen
        for unit in units
        if unit.first_seen is not None
    )
    previous_target_by_key: dict[tuple[str, str], _RepresentativeSpanUnit] = {}
    units_by_conversation: dict[str, list[_RepresentativeSpanUnit]] = {}
    for unit in units:
        if unit.first_seen is None:
            continue
        units_by_conversation.setdefault(unit.conversation_id, []).append(unit)
    for conversation_units in units_by_conversation.values():
        conversation_units.sort(
            key=lambda item: (
                item.first_seen if item.first_seen is not None else -1,
                item.last_seen if item.last_seen is not None else -1,
                item.topic_id,
                item.span_id,
            )
        )
        previous: _RepresentativeSpanUnit | None = None
        for unit in conversation_units:
            previous_target_by_key[(unit.conversation_id, unit.span_id)] = previous
            previous = unit

    return _RecurrenceInstrumentationContext(
        message_timestamps=tuple(message_timestamps),
        span_timestamps=tuple(span_timestamps),
        previous_target_by_key=previous_target_by_key,
    )


def _volume_gap(
    source: _RepresentativeSpanUnit,
    target: _RepresentativeSpanUnit,
    context: _RecurrenceInstrumentationContext,
) -> _VolumeGap:
    if source.first_seen is None or target.first_seen is None:
        return _VolumeGap(value=None, unit=None)

    earlier, later = sorted(
        (source, target),
        key=lambda item: (
            item.first_seen if item.first_seen is not None else -1,
            item.last_seen if item.last_seen is not None else -1,
            item.conversation_id,
            item.span_id,
        ),
    )
    earlier_end = (
        earlier.last_seen
        if earlier.last_seen is not None
        else earlier.first_seen
    )
    later_start = later.first_seen
    if earlier_end is None or later_start is None:
        return _VolumeGap(value=None, unit=None)

    # This currently counts all intervening canonical messages under the input
    # root, not only messages from the two conversations being compared. It is
    # therefore a coarse dormancy proxy and may include unrelated conversations.
    if context.message_timestamps:
        start = bisect.bisect_right(context.message_timestamps, earlier_end)
        end = bisect.bisect_left(context.message_timestamps, later_start)
        return _VolumeGap(value=max(0, end - start), unit="message")

    if context.span_timestamps:
        start = bisect.bisect_right(context.span_timestamps, earlier_end)
        end = bisect.bisect_left(context.span_timestamps, later_start)
        return _VolumeGap(value=max(0, end - start), unit="span")

    return _VolumeGap(value=None, unit=None)


def _continuity_mask(volume_gap: _VolumeGap) -> bool:
    if volume_gap.value is None or volume_gap.unit is None:
        return False
    if volume_gap.unit == "message":
        return volume_gap.value <= _CONTINUITY_VOLUME_GAP_MESSAGE_THRESHOLD
    return volume_gap.value <= _CONTINUITY_VOLUME_GAP_SPAN_THRESHOLD


def _dormancy_score(volume_gap: _VolumeGap) -> float:
    if volume_gap.value is None or volume_gap.unit is None:
        return 0.0
    scale = (
        _DORMANCY_MESSAGE_SCALE
        if volume_gap.unit == "message"
        else _DORMANCY_SPAN_SCALE
    )
    if scale <= 1:
        return 0.0
    return round(
        min(1.0, math.log1p(volume_gap.value) / math.log1p(scale)),
        4,
    )


def _cached_normalized_similarity(
    left: str,
    right: str,
    cache: _SimilarityCache | None = None,
) -> float:
    if cache is None:
        return normalized_similarity(left, right)
    key = (left, right)
    cached = cache.get(key)
    if cached is not None:
        return cached
    value = normalized_similarity(left, right)
    cache[key] = value
    return value


def _local_context_delta(
    *,
    source: _RepresentativeSpanUnit,
    target: _RepresentativeSpanUnit,
    excerpt_similarity: float,
    topic_label_similarity: float,
    context: _RecurrenceInstrumentationContext,
    similarity_cache: _SimilarityCache | None = None,
) -> float | None:
    # This is a lightweight proxy for local context re-entry. It only compares
    # the current target against the immediately previous representative span in
    # the target conversation; it does not yet model fuller local context windows.
    previous_target = context.previous_target_by_key.get((target.conversation_id, target.span_id))
    if previous_target is None:
        return None
    prior_excerpt_similarity = _cached_normalized_similarity(
        source.task_nucleus_text or source.excerpt,
        previous_target.task_nucleus_text or previous_target.excerpt,
        similarity_cache,
    )
    prior_topic_label_similarity = _cached_normalized_similarity(
        source.topic_label or "",
        previous_target.topic_label or "",
        similarity_cache,
    )
    current_signal = max(excerpt_similarity, topic_label_similarity)
    prior_signal = max(round(prior_excerpt_similarity, 4), round(prior_topic_label_similarity, 4))
    return round(max(0.0, current_signal - prior_signal), 4)


def _signals_with_local_context_delta(
    signals: _PairSignals,
    *,
    source: _RepresentativeSpanUnit,
    target: _RepresentativeSpanUnit,
    context: _RecurrenceInstrumentationContext,
    similarity_cache: _SimilarityCache | None = None,
) -> _PairSignals:
    if signals.local_context_delta is not None:
        return signals
    return replace(
        signals,
        local_context_delta=_local_context_delta(
            source=source,
            target=target,
            excerpt_similarity=signals.excerpt_similarity,
            topic_label_similarity=signals.topic_label_similarity,
            context=context,
            similarity_cache=similarity_cache,
        ),
    )


def _evidence_with_local_context_delta(
    evidence: _Evidence,
    local_context_delta: float | None,
) -> _Evidence:
    if evidence.local_context_delta == local_context_delta:
        return evidence
    return replace(evidence, local_context_delta=local_context_delta)


def _intervening_temporal_gap_seconds(
    source: _RepresentativeSpanUnit,
    target: _RepresentativeSpanUnit,
) -> int | None:
    if source.first_seen is None or target.first_seen is None:
        return None

    earlier, later = sorted(
        (source, target),
        key=lambda item: (
            item.first_seen if item.first_seen is not None else -1,
            item.last_seen if item.last_seen is not None else -1,
            item.conversation_id,
            item.span_id,
        ),
    )
    earlier_end = earlier.last_seen if earlier.last_seen is not None else earlier.first_seen
    later_start = later.first_seen
    if earlier_end is None or later_start is None:
        return None

    # We align this with volume_gap semantics by measuring the intervening time
    # window from the earlier span end to the later span start. When an explicit
    # span end is unavailable, last_seen falls back to the best available
    # approximation (effectively max source timestamp / min target timestamp).
    gap_ms = max(0, later_start - earlier_end)
    return int(math.ceil(gap_ms / 1000.0))


def _pair_signals(
    source: _RepresentativeSpanUnit,
    target: _RepresentativeSpanUnit,
    *,
    recurrence_context: _RecurrenceInstrumentationContext,
    token_dictionary_signals: TokenDictionarySignals | None = None,
    similarity_cache: _SimilarityCache | None = None,
    compute_local_context_delta: bool = True,
) -> _PairSignals:
    excerpt_similarity = round(
        _cached_normalized_similarity(
            source.task_nucleus_text or source.excerpt,
            target.task_nucleus_text or target.excerpt,
            similarity_cache,
        ),
        4,
    )
    topic_label_similarity = round(
        _cached_normalized_similarity(
            source.topic_label or "",
            target.topic_label or "",
            similarity_cache,
        ),
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
    timestamp_delta_ms: int | None = None
    if source.first_seen is not None and target.first_seen is not None:
        timestamp_delta_ms = abs(source.first_seen - target.first_seen)
    temporal_gap_seconds = _intervening_temporal_gap_seconds(source, target)
    volume_gap = _volume_gap(source, target, recurrence_context)
    continuity_mask = _continuity_mask(volume_gap)
    dormancy_score = _dormancy_score(volume_gap)
    specificity_score = round(
        (source.excerpt_specificity + target.excerpt_specificity) / 2.0,
        4,
    )
    local_context_delta = (
        _local_context_delta(
            source=source,
            target=target,
            excerpt_similarity=excerpt_similarity,
            topic_label_similarity=topic_label_similarity,
            context=recurrence_context,
            similarity_cache=similarity_cache,
        )
        if compute_local_context_delta
        else None
    )
    shared_anchor_tokens = tuple(
        sorted(set(source.anchor_tokens) & set(target.anchor_tokens))
    )
    shared_strong_anchor_tokens = tuple(
        sorted(set(source.strong_anchor_tokens) & set(target.strong_anchor_tokens))
    )
    shared_dictionary_tokens = tuple(
        sorted(set(source.dictionary_tokens) & set(target.dictionary_tokens))
    )
    shared_bundle_ids = tuple(
        sorted(set(source.bundle_ids) & set(target.bundle_ids))
    )
    shared_dictionary_token_mass = _dictionary_token_mass(
        shared_dictionary_tokens,
        token_dictionary_signals,
    )
    overlap_denominator = min(source.dictionary_token_mass, target.dictionary_token_mass)
    dictionary_overlap_ratio = round(
        (shared_dictionary_token_mass / overlap_denominator)
        if overlap_denominator > 0
        else 0.0,
        4,
    )
    bundle_denominator = min(len(source.bundle_ids), len(target.bundle_ids))
    bundle_concentration = round(
        (len(shared_bundle_ids) / bundle_denominator)
        if bundle_denominator > 0
        else 0.0,
        4,
    )
    min_dictionary_density = round(
        min(source.dictionary_density, target.dictionary_density),
        4,
    )
    explicit_conclusion_overlap = (
        source.explicit_conclusion_text is not None
        and target.explicit_conclusion_text is not None
        and _cached_normalized_similarity(
            source.explicit_conclusion_text,
            target.explicit_conclusion_text,
            similarity_cache,
        )
        >= 0.72
    )
    return _PairSignals(
        excerpt_similarity=excerpt_similarity,
        topic_label_similarity=topic_label_similarity,
        shared_keywords=shared_keywords,
        normalized_label_match=normalized_label_match,
        raw_label_match=raw_label_match,
        timestamp_delta_ms=timestamp_delta_ms,
        temporal_gap_seconds=temporal_gap_seconds,
        volume_gap=volume_gap,
        continuity_mask=continuity_mask,
        dormancy_score=dormancy_score,
        specificity_score=specificity_score,
        local_context_delta=local_context_delta,
        shared_anchor_tokens=shared_anchor_tokens,
        shared_strong_anchor_tokens=shared_strong_anchor_tokens,
        shared_dictionary_tokens=shared_dictionary_tokens,
        shared_bundle_ids=shared_bundle_ids,
        shared_dictionary_token_mass=shared_dictionary_token_mass,
        dictionary_overlap_ratio=dictionary_overlap_ratio,
        bundle_concentration=bundle_concentration,
        min_dictionary_density=min_dictionary_density,
        task_like_score=round(
            (source.task_like_score + target.task_like_score) / 2.0,
            4,
        ),
        reflective_score=round(
            (source.reflective_score + target.reflective_score) / 2.0,
            4,
        ),
        fragment_dictionary_support=round(
            (source.fragment_dictionary_support + target.fragment_dictionary_support) / 2.0,
            4,
        ),
        residue_pair=source.residue_like and target.residue_like,
        explicit_conclusion_overlap=explicit_conclusion_overlap,
        local_llm_pair=(
            source.summary_source == "local_llm"
            and target.summary_source == "local_llm"
        ),
        heuristic_pair=(
            source.summary_source == "heuristic"
            or target.summary_source == "heuristic"
        ),
    )


def _evidence_from_signals(
    *,
    signals: _PairSignals,
    score: float,
    reason_codes: tuple[str, ...],
) -> _Evidence:
    return _Evidence(
        score=round(max(0.0, min(score, 1.0)), 4),
        reason_codes=reason_codes,
        excerpt_similarity=signals.excerpt_similarity,
        topic_label_similarity=signals.topic_label_similarity,
        shared_keywords=signals.shared_keywords,
        normalized_label_match=signals.normalized_label_match,
        raw_label_match=signals.raw_label_match,
        timestamp_delta_ms=signals.timestamp_delta_ms,
        volume_gap=signals.volume_gap.value,
        temporal_gap_seconds=signals.temporal_gap_seconds,
        continuity_mask=signals.continuity_mask,
        dormancy_score=signals.dormancy_score,
        specificity_score=signals.specificity_score,
        local_context_delta=signals.local_context_delta,
    )


def _dedupe_reason_codes(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _similarity_score_and_reasons(
    signals: _PairSignals,
) -> tuple[float, tuple[str, ...], bool]:
    score = 0.0
    reason_codes: list[str] = []
    if signals.normalized_label_match:
        score += _LABEL_MATCH_SCORE
        reason_codes.append("normalized_label_match")
    if signals.raw_label_match:
        score += _RAW_LABEL_MATCH_SCORE
        reason_codes.append("raw_label_match")

    if len(signals.shared_keywords) >= 2:
        score += _KEYWORD_OVERLAP_HIGH_SCORE
        reason_codes.append("shared_keywords_high")
    elif len(signals.shared_keywords) == 1:
        score += _KEYWORD_OVERLAP_LOW_SCORE
        reason_codes.append("shared_keywords_low")

    if signals.topic_label_similarity >= 0.88:
        score += _TOPIC_LABEL_SIMILARITY_HIGH_SCORE
        reason_codes.append("topic_label_similarity_high")
    elif signals.topic_label_similarity >= 0.72:
        score += _TOPIC_LABEL_SIMILARITY_MEDIUM_SCORE
        reason_codes.append("topic_label_similarity_medium")

    if signals.excerpt_similarity >= 0.78:
        score += _EXCERPT_SIMILARITY_HIGH_SCORE
        reason_codes.append("excerpt_similarity_high")
    elif signals.excerpt_similarity >= 0.64:
        score += _EXCERPT_SIMILARITY_MEDIUM_SCORE
        reason_codes.append("excerpt_similarity_medium")
    elif signals.excerpt_similarity >= 0.52:
        score += _EXCERPT_SIMILARITY_LOW_SCORE
        reason_codes.append("excerpt_similarity_low")

    if (
        signals.topic_label_similarity >= 0.88
        and signals.excerpt_similarity >= 0.78
    ):
        score += _TOPIC_EXCERPT_COMBINATION_HIGH_SCORE
        reason_codes.append("topic_excerpt_combination_high")

    if (
        signals.shared_dictionary_token_mass >= 1.5
        and signals.dictionary_overlap_ratio >= 0.55
        and signals.min_dictionary_density >= 0.18
    ):
        score += _DICTIONARY_TOKEN_OVERLAP_DENSE_SCORE
        reason_codes.append("dictionary_token_overlap_dense")
    elif (
        signals.shared_dictionary_token_mass >= 0.9
        and signals.dictionary_overlap_ratio >= 0.25
        and signals.min_dictionary_density >= 0.08
    ):
        score += _DICTIONARY_TOKEN_OVERLAP_WEAK_SCORE
        reason_codes.append("dictionary_token_overlap_weak")

    if (
        signals.shared_bundle_ids
        and signals.bundle_concentration >= 0.6
        and signals.shared_dictionary_token_mass >= 1.2
    ):
        score += _BUNDLE_OVERLAP_CONCENTRATED_SCORE
        reason_codes.append("bundle_overlap_concentrated")
    elif (
        signals.shared_bundle_ids
        and signals.bundle_concentration >= 0.25
        and signals.shared_dictionary_token_mass >= 0.9
    ):
        score += _BUNDLE_OVERLAP_BROAD_SCORE
        reason_codes.append("bundle_overlap_broad")

    if (
        signals.dictionary_overlap_ratio >= 0.55
        and signals.min_dictionary_density >= 0.18
        and (
            signals.bundle_concentration >= 0.5
            or signals.shared_dictionary_token_mass >= 2.0
        )
    ):
        score += _NUCLEUS_OVERLAP_SPECIFIC_SCORE
        reason_codes.append("nucleus_overlap_specific")

    if signals.explicit_conclusion_overlap:
        score += _EXPLICIT_CONCLUSION_OVERLAP_SCORE
        reason_codes.append("explicit_conclusion_overlap")

    if signals.local_llm_pair:
        score += _SUMMARY_LOCAL_LLM_SOURCE_BONUS
        reason_codes.append("summary_source_local_llm_pair")
    elif signals.heuristic_pair:
        score -= _SUMMARY_HEURISTIC_SOURCE_PENALTY
        reason_codes.append("summary_source_heuristic_penalty")

    if signals.timestamp_delta_ms is not None:
        if signals.timestamp_delta_ms >= _TIMESTAMP_DISTANCE_HIGH_THRESHOLD_MS:
            score += _TIMESTAMP_DISTANCE_HIGH_SCORE
            reason_codes.append("timestamp_distance_high")
        elif signals.timestamp_delta_ms >= _TIMESTAMP_DISTANCE_MEDIUM_THRESHOLD_MS:
            score += _TIMESTAMP_DISTANCE_MEDIUM_SCORE
            reason_codes.append("timestamp_distance_medium")

    has_strong_signal = (
        signals.excerpt_similarity >= 0.52
        or signals.topic_label_similarity >= 0.72
        or len(signals.shared_keywords) >= 1
        or signals.shared_dictionary_token_mass >= 0.9
        or (
            bool(signals.shared_bundle_ids)
            and signals.bundle_concentration >= 0.25
        )
        or signals.explicit_conclusion_overlap
    )
    return score, _dedupe_reason_codes(reason_codes), has_strong_signal


def _topic_summary_semantic_tokens(
    text: str,
    *,
    cross_thread_rules: CrossThreadLexicalRules,
) -> tuple[str, ...]:
    tokens: list[str] = []
    for token in _token_list(text):
        normalized = _normalize_anchor_token(token)
        if len(normalized) < 3 and not _has_cjk(normalized):
            continue
        if _is_topic_summary_generic_scoring_token(normalized, cross_thread_rules):
            continue
        tokens.append(normalized)
        if re.search(r"[一-龯ぁ-んァ-ヶー]", normalized) and len(normalized) >= 4:
            for size in (2, 3):
                tokens.extend(
                    normalized[index : index + size]
                    for index in range(0, len(normalized) - size + 1)
                )
    return tuple(dict.fromkeys(tokens))


def _topic_summary_semantic_token_counts(
    text: str,
    *,
    cross_thread_rules: CrossThreadLexicalRules,
    include_cjk_ngrams: bool = True,
) -> Counter[str]:
    counts: Counter[str] = Counter()
    for token in _token_list(text):
        normalized = _normalize_anchor_token(token)
        if len(normalized) < 3 and not _has_cjk(normalized):
            continue
        if _is_topic_summary_generic_scoring_token(normalized, cross_thread_rules):
            continue
        counts[normalized] += 1
        if include_cjk_ngrams and _has_cjk(normalized) and len(normalized) >= 4:
            for size in (2, 3):
                for index in range(0, len(normalized) - size + 1):
                    ngram = normalized[index : index + size]
                    if not _is_topic_summary_generic_scoring_token(
                        ngram,
                        cross_thread_rules,
                    ):
                        counts[ngram] += 1
    return counts


def _token_overlap_ratio(left: tuple[str, ...], right: tuple[str, ...]) -> float:
    if not left or not right:
        return 0.0
    left_set = set(left)
    right_set = set(right)
    denominator = min(len(left_set), len(right_set))
    if denominator == 0:
        return 0.0
    return round(len(left_set & right_set) / denominator, 4)


def _top_topic_summary_keyphrases(
    text: str,
    *,
    cross_thread_rules: CrossThreadLexicalRules,
    limit: int = 16,
) -> tuple[str, ...]:
    counts: Counter[str] = Counter(
        _topic_summary_semantic_tokens(text, cross_thread_rules=cross_thread_rules)
    )
    ranked = sorted(
        counts,
        key=lambda token: (
            -counts[token],
            -len(token),
            token,
        ),
    )
    return tuple(ranked[:limit])


def _topic_summary_keyword_scoring_tokens(
    keywords: tuple[str, ...],
    *,
    cross_thread_rules: CrossThreadLexicalRules,
) -> tuple[str, ...]:
    return tuple(
        keyword
        for keyword in _normalized_keywords(keywords)
        if not _is_topic_summary_generic_scoring_token(keyword, cross_thread_rules)
    )


def _topic_summary_scoring_features(
    *,
    topic_label: str | None,
    summary_text: str,
    keywords: tuple[str, ...],
    cross_thread_rules: CrossThreadLexicalRules,
) -> _TopicSummaryScoringFeatures:
    return _TopicSummaryScoringFeatures(
        title_tokens=_topic_summary_semantic_tokens(
            topic_label or "",
            cross_thread_rules=cross_thread_rules,
        ),
        summary_keyphrases=_top_topic_summary_keyphrases(
            summary_text,
            cross_thread_rules=cross_thread_rules,
        ),
        keyword_tokens=_topic_summary_keyword_scoring_tokens(
            keywords,
            cross_thread_rules=cross_thread_rules,
        ),
        distinctive_token_profile=_topic_summary_token_profile_from_values(
            topic_label=topic_label,
            summary_text=summary_text,
            keywords=keywords,
            cross_thread_rules=cross_thread_rules,
        ),
    )


def _topic_summary_title_tokens_for_unit(
    unit: _RepresentativeSpanUnit,
    *,
    cross_thread_rules: CrossThreadLexicalRules,
) -> tuple[str, ...]:
    if unit.topic_summary_scoring.title_tokens:
        return unit.topic_summary_scoring.title_tokens
    return _topic_summary_semantic_tokens(
        unit.topic_label or "",
        cross_thread_rules=cross_thread_rules,
    )


def _topic_summary_keyphrases_for_unit(
    unit: _RepresentativeSpanUnit,
    *,
    cross_thread_rules: CrossThreadLexicalRules,
) -> tuple[str, ...]:
    if unit.topic_summary_scoring.summary_keyphrases:
        return unit.topic_summary_scoring.summary_keyphrases
    return _top_topic_summary_keyphrases(
        unit.summary_text or unit.excerpt,
        cross_thread_rules=cross_thread_rules,
    )


def _topic_summary_keyword_tokens_for_unit(
    unit: _RepresentativeSpanUnit,
    *,
    cross_thread_rules: CrossThreadLexicalRules,
) -> tuple[str, ...]:
    if unit.topic_summary_scoring.keyword_tokens:
        return unit.topic_summary_scoring.keyword_tokens
    return _topic_summary_keyword_scoring_tokens(
        unit.keywords,
        cross_thread_rules=cross_thread_rules,
    )


def _topic_summary_score_and_reasons(
    source: _RepresentativeSpanUnit,
    target: _RepresentativeSpanUnit,
    signals: _PairSignals,
    *,
    cross_thread_rules: CrossThreadLexicalRules,
) -> tuple[float, tuple[str, ...], bool]:
    score = 0.0
    reason_codes: list[str] = []

    title_overlap = _token_overlap_ratio(
        _topic_summary_title_tokens_for_unit(
            source,
            cross_thread_rules=cross_thread_rules,
        ),
        _topic_summary_title_tokens_for_unit(
            target,
            cross_thread_rules=cross_thread_rules,
        ),
    )
    if title_overlap >= 0.55:
        score += _TOPIC_SUMMARY_TITLE_OVERLAP_HIGH_SCORE
        reason_codes.append("topic_summary_title_overlap_high")
    elif title_overlap >= 0.35:
        score += _TOPIC_SUMMARY_TITLE_OVERLAP_MEDIUM_SCORE
        reason_codes.append("topic_summary_title_overlap_medium")
    elif title_overlap >= 0.18:
        score += _TOPIC_SUMMARY_TITLE_OVERLAP_LOW_SCORE
        reason_codes.append("topic_summary_title_overlap_low")

    keyphrase_overlap = _token_overlap_ratio(
        _topic_summary_keyphrases_for_unit(
            source,
            cross_thread_rules=cross_thread_rules,
        ),
        _topic_summary_keyphrases_for_unit(
            target,
            cross_thread_rules=cross_thread_rules,
        ),
    )
    if keyphrase_overlap >= 0.36:
        score += _TOPIC_SUMMARY_KEYPHRASE_OVERLAP_HIGH_SCORE
        reason_codes.append("topic_summary_keyphrase_overlap_high")
    elif keyphrase_overlap >= 0.22:
        score += _TOPIC_SUMMARY_KEYPHRASE_OVERLAP_MEDIUM_SCORE
        reason_codes.append("topic_summary_keyphrase_overlap_medium")
    elif keyphrase_overlap >= 0.1:
        score += _TOPIC_SUMMARY_KEYPHRASE_OVERLAP_LOW_SCORE
        reason_codes.append("topic_summary_keyphrase_overlap_low")

    specific_keywords = _specific_shared_scoring_keywords(
        source,
        target,
        cross_thread_rules=cross_thread_rules,
    )
    if len(specific_keywords) >= 2:
        score += _TOPIC_SUMMARY_KEYWORD_OVERLAP_HIGH_SCORE
        reason_codes.append("shared_keywords_high")
        reason_codes.append("topic_summary_keyword_overlap_high")
    elif len(specific_keywords) == 1:
        score += _TOPIC_SUMMARY_KEYWORD_OVERLAP_LOW_SCORE
        reason_codes.append("shared_keywords_low")
        reason_codes.append("topic_summary_keyword_overlap_low")
    elif len(signals.shared_keywords) >= 2:
        reason_codes.append("shared_keywords_high")
    elif len(signals.shared_keywords) == 1:
        reason_codes.append("shared_keywords_low")

    distinctive_boost = _topic_summary_distinctive_token_boost(
        source,
        target,
        cross_thread_rules=cross_thread_rules,
    )
    if distinctive_boost >= _TOPIC_SUMMARY_DISTINCTIVE_TOKEN_STRONG_SCORE:
        score += distinctive_boost
        reason_codes.append("topic_summary_distinctive_token_overlap_strong")
    elif distinctive_boost > 0:
        score += distinctive_boost
        reason_codes.append("topic_summary_distinctive_token_overlap")

    if (
        signals.shared_dictionary_token_mass >= 1.5
        and signals.dictionary_overlap_ratio >= 0.55
        and signals.min_dictionary_density >= 0.18
    ):
        score += 0.05
        reason_codes.append("dictionary_token_overlap_dense")
    elif (
        signals.shared_dictionary_token_mass >= 0.9
        and signals.dictionary_overlap_ratio >= 0.25
        and signals.min_dictionary_density >= 0.08
    ):
        score += 0.01
        reason_codes.append("dictionary_token_overlap_weak")

    if (
        signals.shared_bundle_ids
        and signals.bundle_concentration >= 0.6
        and signals.shared_dictionary_token_mass >= 1.2
    ):
        score += 0.04
        reason_codes.append("bundle_overlap_concentrated")
    elif (
        signals.shared_bundle_ids
        and signals.bundle_concentration >= 0.25
        and signals.shared_dictionary_token_mass >= 0.9
    ):
        reason_codes.append("bundle_overlap_broad")

    if (
        signals.dictionary_overlap_ratio >= 0.55
        and signals.min_dictionary_density >= 0.18
        and (
            signals.bundle_concentration >= 0.5
            or signals.shared_dictionary_token_mass >= 2.0
        )
    ):
        score += _NUCLEUS_OVERLAP_SPECIFIC_SCORE
        reason_codes.append("nucleus_overlap_specific")

    if signals.explicit_conclusion_overlap:
        score += 0.1
        reason_codes.append("explicit_conclusion_overlap")

    if signals.local_llm_pair:
        score += _TOPIC_SUMMARY_LOCAL_LLM_PAIR_SCORE
        reason_codes.append("summary_source_local_llm_pair")
    elif signals.heuristic_pair:
        score -= _TOPIC_SUMMARY_HEURISTIC_PENALTY
        reason_codes.append("summary_source_heuristic_penalty")

    if signals.timestamp_delta_ms is not None:
        if signals.timestamp_delta_ms >= _TIMESTAMP_DISTANCE_HIGH_THRESHOLD_MS:
            score += _TOPIC_SUMMARY_TIMESTAMP_DISTANCE_HIGH_SCORE
            reason_codes.append("timestamp_distance_high")
        elif signals.timestamp_delta_ms >= _TIMESTAMP_DISTANCE_MEDIUM_THRESHOLD_MS:
            score += _TOPIC_SUMMARY_TIMESTAMP_DISTANCE_MEDIUM_SCORE
            reason_codes.append("timestamp_distance_medium")

    if len(signals.shared_strong_anchor_tokens) >= 2:
        score += _TOPIC_SUMMARY_ANCHOR_OVERLAP_STRONG_SCORE
        reason_codes.append("anchor_overlap_strong")
    elif signals.shared_anchor_tokens:
        score += _TOPIC_SUMMARY_ANCHOR_OVERLAP_SCORE
        reason_codes.append("anchor_overlap")

    has_strong_signal = any(
        reason in reason_codes
        for reason in (
            "topic_summary_title_overlap_high",
            "topic_summary_title_overlap_medium",
            "topic_summary_keyphrase_overlap_high",
            "topic_summary_keyphrase_overlap_medium",
            "topic_summary_keyword_overlap_high",
            "topic_summary_keyword_overlap_low",
            "dictionary_token_overlap_dense",
            "bundle_overlap_concentrated",
            "explicit_conclusion_overlap",
        )
    ) or len(signals.shared_keywords) >= 1
    return score, _dedupe_reason_codes(reason_codes), has_strong_signal


def _structural_signal_score_and_reasons(
    signals: _PairSignals,
) -> tuple[float, tuple[str, ...]]:
    score = 0.0
    reason_codes: list[str] = []
    if len(signals.shared_strong_anchor_tokens) >= 2:
        score += _WEAK_ROUTE_ANCHOR_OVERLAP_STRONG_SCORE
        reason_codes.append("anchor_overlap_strong")
    else:
        score += _WEAK_ROUTE_ANCHOR_OVERLAP_SCORE
        reason_codes.append("anchor_overlap")
    score += _WEAK_ROUTE_DORMANT_GAP_SCORE
    reason_codes.append("dormant_gap")
    if signals.specificity_score >= _WEAK_RECURRENCE_SPECIFICITY_THRESHOLD:
        score += _WEAK_ROUTE_SPECIFICITY_SCORE
        reason_codes.append("specificity_signal")
    score += _WEAK_ROUTE_TASK_LIKE_SCORE
    reason_codes.append("task_like_signal")
    if (
        signals.local_context_delta is not None
        and signals.local_context_delta >= _WEAK_RECURRENCE_LOCAL_CONTEXT_DELTA_THRESHOLD
    ):
        score += _WEAK_ROUTE_CONTEXT_SHIFT_SCORE
        reason_codes.append("context_shift_signal")
    return round(score, 4), _dedupe_reason_codes(reason_codes)


def _score_and_reasons_for_pair(
    source: _RepresentativeSpanUnit,
    target: _RepresentativeSpanUnit,
    signals: _PairSignals,
    *,
    cross_thread_rules: CrossThreadLexicalRules,
) -> tuple[float, tuple[str, ...], bool]:
    if _topic_summary_pair(source, target):
        return _topic_summary_score_and_reasons(
            source,
            target,
            signals,
            cross_thread_rules=cross_thread_rules,
        )
    return _similarity_score_and_reasons(signals)


def _topic_summary_pair(
    source: _RepresentativeSpanUnit,
    target: _RepresentativeSpanUnit,
) -> bool:
    return source.unit_kind == "topic_summary" and target.unit_kind == "topic_summary"


def _admission_anchor_token_key(token: str) -> str:
    normalized = normalize_analysis_text(token)
    return re.sub(r"[^a-z0-9一-龯ぁ-んァ-ヶー]+", "", normalized)


def _has_cjk(token: str) -> bool:
    return bool(re.search(r"[一-龯ぁ-んァ-ヶー]", token))


def _topic_summary_scoring_tokens(
    cross_thread_rules: CrossThreadLexicalRules,
    attr_name: str,
    fallback: frozenset[str] | tuple[str, ...],
) -> tuple[str, ...]:
    tokens = getattr(cross_thread_rules, attr_name, ())
    if tokens:
        return tuple(tokens)
    return tuple(fallback)


def _topic_summary_scoring_token_keys(
    cross_thread_rules: CrossThreadLexicalRules,
    attr_name: str,
    fallback: frozenset[str] | tuple[str, ...],
) -> set[str]:
    keys = {
        _admission_anchor_token_key(token)
        for token in _topic_summary_scoring_tokens(
            cross_thread_rules,
            attr_name,
            fallback,
        )
    }
    return {key for key in keys if key}


def _topic_summary_scoring_patterns(
    cross_thread_rules: CrossThreadLexicalRules,
    attr_name: str,
    fallback: tuple[str, ...],
) -> tuple[str, ...]:
    patterns = getattr(cross_thread_rules, attr_name, ())
    if patterns:
        return tuple(patterns)
    return fallback


def _topic_summary_short_specific_token_keys(
    cross_thread_rules: CrossThreadLexicalRules,
) -> set[str]:
    return _topic_summary_scoring_token_keys(
        cross_thread_rules,
        "topic_summary_scoring_short_specific_tokens",
        _TOPIC_SUMMARY_SHORT_SPECIFIC_SCORING_KEYWORDS,
    )


def _is_topic_summary_distinctive_block_token(
    token: str,
    cross_thread_rules: CrossThreadLexicalRules,
) -> bool:
    return _admission_anchor_token_key(token) in _topic_summary_scoring_token_keys(
        cross_thread_rules,
        "topic_summary_scoring_distinctive_block_tokens",
        _TOPIC_SUMMARY_DISTINCTIVE_BLOCK_TOKENS,
    )


def _is_topic_summary_distinctive_allow_token(
    token: str,
    cross_thread_rules: CrossThreadLexicalRules,
) -> bool:
    return _admission_anchor_token_key(token) in _topic_summary_scoring_token_keys(
        cross_thread_rules,
        "topic_summary_scoring_distinctive_allow_tokens",
        _TOPIC_SUMMARY_DISTINCTIVE_ALLOW_TOKENS,
    )


def _is_topic_summary_weak_distinctive_token(
    token: str,
    cross_thread_rules: CrossThreadLexicalRules,
) -> bool:
    key = _admission_anchor_token_key(token)
    weak_keys = _topic_summary_scoring_token_keys(
        cross_thread_rules,
        "topic_summary_scoring_weak_distinctive_tokens",
        _TOPIC_SUMMARY_WEAK_DISTINCTIVE_TOKENS,
    ) | _topic_summary_scoring_token_keys(
        cross_thread_rules,
        "topic_summary_scoring_persona_weak_tokens",
        _TOPIC_SUMMARY_PERSONA_WEAK_TOKENS,
    )
    return key in weak_keys


def _is_topic_summary_persona_distinctive_token(
    token: str,
    cross_thread_rules: CrossThreadLexicalRules,
) -> bool:
    return _is_topic_summary_weak_distinctive_token(token, cross_thread_rules)


def _topic_summary_generic_admission_anchor_tokens(
    cross_thread_rules: CrossThreadLexicalRules,
) -> tuple[str, ...]:
    tokens = getattr(
        cross_thread_rules,
        "topic_summary_admission_generic_anchor_tokens",
        (),
    )
    if tokens:
        return tuple(tokens)
    return tuple(_FALLBACK_TOPIC_SUMMARY_GENERIC_ADMISSION_ANCHORS)


def _topic_summary_generic_admission_anchor_keys(
    cross_thread_rules: CrossThreadLexicalRules,
) -> set[str]:
    generic_keys = {
        _admission_anchor_token_key(token)
        for token in _topic_summary_generic_admission_anchor_tokens(cross_thread_rules)
    }
    return {key for key in generic_keys if key}


def _topic_summary_generic_admission_anchor_patterns(
    cross_thread_rules: CrossThreadLexicalRules,
) -> tuple[str, ...]:
    patterns = getattr(
        cross_thread_rules,
        "topic_summary_admission_generic_anchor_patterns",
        (),
    )
    if patterns:
        return tuple(patterns)
    return _FALLBACK_TOPIC_SUMMARY_GENERIC_ADMISSION_ANCHOR_PATTERNS


def _is_topic_summary_generic_admission_anchor(
    token: str,
    cross_thread_rules: CrossThreadLexicalRules,
) -> bool:
    key = _admission_anchor_token_key(token)
    if not key:
        return True
    if key in _topic_summary_generic_admission_anchor_keys(cross_thread_rules):
        return True
    return any(
        re.fullmatch(pattern, key)
        for pattern in _topic_summary_generic_admission_anchor_patterns(
            cross_thread_rules
        )
    )


def _is_topic_summary_generic_scoring_token(
    token: str,
    cross_thread_rules: CrossThreadLexicalRules,
) -> bool:
    key = _admission_anchor_token_key(token)
    if not key:
        return True
    if _is_topic_summary_generic_admission_anchor(token, cross_thread_rules):
        return True
    generic_keys = _topic_summary_scoring_token_keys(
        cross_thread_rules,
        "topic_summary_scoring_generic_tokens",
        _TOPIC_SUMMARY_GENERIC_SCORING_KEYWORDS,
    )
    if key in generic_keys:
        return True
    generic_patterns = (
        _topic_summary_scoring_patterns(
            cross_thread_rules,
            "topic_summary_scoring_generic_patterns",
            _TOPIC_SUMMARY_GENERIC_SCORING_PATTERNS,
        )
        + _topic_summary_scoring_patterns(
            cross_thread_rules,
            "topic_summary_scoring_tool_residue_patterns",
            _TOPIC_SUMMARY_TOOL_RESIDUE_PATTERNS,
        )
        + _topic_summary_scoring_patterns(
            cross_thread_rules,
            "topic_summary_scoring_citation_residue_patterns",
            _TOPIC_SUMMARY_CITATION_RESIDUE_PATTERNS,
        )
    )
    if any(re.fullmatch(pattern, key) for pattern in generic_patterns):
        return True
    if (
        len(key) <= 2
        and not _has_cjk(key)
        and key not in _topic_summary_short_specific_token_keys(cross_thread_rules)
    ):
        return True
    return False


def _is_topic_summary_distinctive_scoring_token(
    token: str,
    cross_thread_rules: CrossThreadLexicalRules,
) -> bool:
    key = _admission_anchor_token_key(token)
    if _is_topic_summary_distinctive_block_token(token, cross_thread_rules):
        return False
    if _is_topic_summary_distinctive_allow_token(token, cross_thread_rules):
        return True
    if _is_topic_summary_generic_scoring_token(token, cross_thread_rules):
        return False
    if _has_cjk(key):
        return len(key) >= 2
    if key in _topic_summary_short_specific_token_keys(cross_thread_rules):
        return True
    if any(char.isdigit() for char in key):
        return len(key) >= 3
    return len(key) >= 5


def _topic_summary_token_profile(
    unit: _RepresentativeSpanUnit,
    *,
    cross_thread_rules: CrossThreadLexicalRules,
) -> dict[str, dict[str, int]]:
    if unit.topic_summary_scoring.distinctive_token_profile:
        return unit.topic_summary_scoring.distinctive_token_profile
    return _topic_summary_token_profile_from_values(
        topic_label=unit.topic_label,
        summary_text=unit.summary_text or unit.excerpt,
        keywords=unit.keywords,
        cross_thread_rules=cross_thread_rules,
    )


def _topic_summary_token_profile_from_values(
    *,
    topic_label: str | None,
    summary_text: str,
    keywords: tuple[str, ...],
    cross_thread_rules: CrossThreadLexicalRules,
) -> dict[str, dict[str, int]]:
    profile: dict[str, dict[str, int]] = {}

    def add(location: str, text: str) -> None:
        for token, count in _topic_summary_semantic_token_counts(
            text,
            cross_thread_rules=cross_thread_rules,
            include_cjk_ngrams=False,
        ).items():
            if not _is_topic_summary_distinctive_scoring_token(
                token,
                cross_thread_rules,
            ):
                continue
            bucket = profile.setdefault(token, {})
            bucket[location] = bucket.get(location, 0) + count

    add("title", topic_label or "")
    add("summary", summary_text)
    for keyword in keywords:
        add("keyword", keyword)
    return profile


def _topic_summary_distinctive_token_boost(
    source: _RepresentativeSpanUnit,
    target: _RepresentativeSpanUnit,
    *,
    cross_thread_rules: CrossThreadLexicalRules,
) -> float:
    source_profile = _topic_summary_token_profile(
        source,
        cross_thread_rules=cross_thread_rules,
    )
    target_profile = _topic_summary_token_profile(
        target,
        cross_thread_rules=cross_thread_rules,
    )
    shared_tokens = set(source_profile) & set(target_profile)
    if not shared_tokens:
        return 0.0

    eligible_tokens: list[tuple[str, dict[str, int], dict[str, int]]] = []
    for token in shared_tokens:
        source_locations = source_profile[token]
        target_locations = target_profile[token]
        if not _has_distinctive_strong_location_evidence(
            token,
            source_locations,
            target_locations,
        ):
            continue
        eligible_tokens.append((token, source_locations, target_locations))

    if not eligible_tokens:
        return 0.0
    non_persona_tokens = [
        item
        for item in eligible_tokens
        if not _is_topic_summary_persona_distinctive_token(
            item[0],
            cross_thread_rules,
        )
    ]
    if not non_persona_tokens:
        return 0.0

    boost = 0.0
    for token, source_locations, target_locations in non_persona_tokens:
        location_score = 0.0
        if "title" in source_locations and "title" in target_locations:
            location_score += 0.04
        if (
            {"title", "summary"} <= set(source_locations)
            or {"title", "summary"} <= set(target_locations)
        ):
            location_score += 0.03
        if "summary" in source_locations and "summary" in target_locations:
            location_score += 0.03
        if "keyword" in source_locations and "keyword" in target_locations:
            location_score += 0.03
        total_count = sum(source_locations.values()) + sum(target_locations.values())
        if total_count >= 4:
            location_score += 0.03
        elif total_count >= 3:
            location_score += 0.015
        if _has_cjk(token):
            location_score += 0.02
        boost += min(0.08, location_score)

    if boost >= 0.14:
        return _TOPIC_SUMMARY_DISTINCTIVE_TOKEN_STRONG_SCORE
    if boost > 0:
        return _TOPIC_SUMMARY_DISTINCTIVE_TOKEN_SCORE
    return 0.0


def _has_distinctive_strong_location_evidence(
    token: str,
    source_locations: dict[str, int],
    target_locations: dict[str, int],
) -> bool:
    source_has_keyword = "keyword" in source_locations
    target_has_keyword = "keyword" in target_locations
    is_short_cjk = _has_cjk(token) and len(_admission_anchor_token_key(token)) == 2
    if source_has_keyword and target_has_keyword:
        return True
    if is_short_cjk:
        return False
    return (
        source_has_keyword
        and ("title" in target_locations or "summary" in target_locations)
    ) or (
        target_has_keyword
        and ("title" in source_locations or "summary" in source_locations)
    )


def _non_generic_strong_anchor_overlap(
    signals: _PairSignals,
    cross_thread_rules: CrossThreadLexicalRules,
) -> tuple[str, ...]:
    return tuple(
        token
        for token in signals.shared_strong_anchor_tokens
        if not _is_topic_summary_generic_admission_anchor(token, cross_thread_rules)
    )


def _non_generic_shared_keywords(
    signals: _PairSignals,
    cross_thread_rules: CrossThreadLexicalRules,
) -> tuple[str, ...]:
    return tuple(
        keyword
        for keyword in signals.shared_keywords
        if not _is_topic_summary_generic_admission_anchor(keyword, cross_thread_rules)
    )


def _specific_shared_scoring_keywords(
    source: _RepresentativeSpanUnit,
    target: _RepresentativeSpanUnit,
    *,
    cross_thread_rules: CrossThreadLexicalRules,
) -> tuple[str, ...]:
    return tuple(
        sorted(
            set(
                _topic_summary_keyword_tokens_for_unit(
                    source,
                    cross_thread_rules=cross_thread_rules,
                )
            )
            & set(
                _topic_summary_keyword_tokens_for_unit(
                    target,
                    cross_thread_rules=cross_thread_rules,
                )
            )
        )
    )


def _topic_summary_admission_filter_reason(
    source: _RepresentativeSpanUnit,
    target: _RepresentativeSpanUnit,
    evidence: _Evidence,
    signals: _PairSignals,
    *,
    cross_thread_rules: CrossThreadLexicalRules,
) -> str | None:
    if not _topic_summary_pair(source, target):
        return None

    reason_codes = set(evidence.reason_codes)
    high_similarity = bool(
        reason_codes
        & {
            "topic_label_similarity_high",
            "excerpt_similarity_high",
            "topic_excerpt_combination_high",
        }
    )
    strong_keyword_reason = "shared_keywords_high" in reason_codes
    strong_keyword = strong_keyword_reason and bool(
        _non_generic_shared_keywords(signals, cross_thread_rules)
    )
    dense_dictionary = "dictionary_token_overlap_dense" in reason_codes
    concentrated_bundle = "bundle_overlap_concentrated" in reason_codes
    explicit_conclusion = "explicit_conclusion_overlap" in reason_codes
    strong_anchor_reason = "anchor_overlap_strong" in reason_codes
    non_generic_strong_anchor = bool(
        _non_generic_strong_anchor_overlap(signals, cross_thread_rules)
    )
    strong_anchor = strong_anchor_reason and non_generic_strong_anchor

    has_direct_signal = any(
        (
            high_similarity,
            strong_keyword,
            dense_dictionary,
            concentrated_bundle,
            explicit_conclusion,
            strong_anchor,
        )
    )
    if has_direct_signal:
        return None

    if strong_keyword_reason and not strong_keyword:
        return "generic_shared_keywords_only"
    if strong_anchor_reason and not non_generic_strong_anchor:
        return "generic_strong_anchor_only"
    if source.summary_source == "heuristic" or target.summary_source == "heuristic":
        return "heuristic_requires_strong_direct_signal"
    return "missing_direct_semantic_signal"


def _weak_recurrence_evidence_for_pair(
    source: _RepresentativeSpanUnit,
    target: _RepresentativeSpanUnit,
    *,
    recurrence_context: _RecurrenceInstrumentationContext,
    token_dictionary_signals: TokenDictionarySignals | None = None,
    cross_thread_rules: CrossThreadLexicalRules,
    base_evidence: _Evidence | None = None,
    signals: _PairSignals | None = None,
    similarity_cache: _SimilarityCache | None = None,
) -> _WeakRecurrenceCandidate | None:
    if signals is None:
        signals = _pair_signals(
            source,
            target,
            recurrence_context=recurrence_context,
            token_dictionary_signals=token_dictionary_signals,
        )
    if signals.residue_pair:
        return None
    if not signals.shared_anchor_tokens:
        return None

    has_meaningful_separation = (
        signals.volume_gap.value is not None
        and signals.volume_gap.unit is not None
        and not signals.continuity_mask
    ) or (
        signals.temporal_gap_seconds is not None
        and signals.temporal_gap_seconds >= _WEAK_RECURRENCE_TEMPORAL_GAP_SECONDS
    )
    if not has_meaningful_separation:
        return None

    has_anchor_support = (
        len(signals.shared_strong_anchor_tokens) >= 2
        or (
            len(signals.shared_strong_anchor_tokens) == 1
            and len(signals.shared_anchor_tokens) >= 2
        )
    )
    if (
        not has_anchor_support
        and len(signals.shared_strong_anchor_tokens) == 1
        and signals.task_like_score >= _WEAK_RECURRENCE_SINGLE_ANCHOR_TASK_LIKE_THRESHOLD
        and signals.specificity_score >= _WEAK_RECURRENCE_SPECIFICITY_THRESHOLD
    ):
        signals = _signals_with_local_context_delta(
            signals,
            source=source,
            target=target,
            context=recurrence_context,
            similarity_cache=similarity_cache,
        )
    has_single_anchor_exception = (
        len(signals.shared_strong_anchor_tokens) == 1
        and signals.task_like_score >= _WEAK_RECURRENCE_SINGLE_ANCHOR_TASK_LIKE_THRESHOLD
        and signals.specificity_score >= _WEAK_RECURRENCE_SPECIFICITY_THRESHOLD
        and (
            signals.local_context_delta is not None
            and signals.local_context_delta >= _WEAK_RECURRENCE_LOCAL_CONTEXT_DELTA_THRESHOLD
        )
    )
    if not (has_anchor_support or has_single_anchor_exception):
        return None

    has_taskish_signal = (
        signals.task_like_score >= _WEAK_RECURRENCE_TASK_LIKE_THRESHOLD
        and signals.specificity_score >= _WEAK_RECURRENCE_SPECIFICITY_THRESHOLD
    )
    if not has_taskish_signal:
        return None
    if signals.reflective_score >= _WEAK_RECURRENCE_REFLECTIVE_THRESHOLD:
        return None

    signals = _signals_with_local_context_delta(
        signals,
        source=source,
        target=target,
        context=recurrence_context,
        similarity_cache=similarity_cache,
    )
    score, base_reason_codes, _has_strong_signal = _score_and_reasons_for_pair(
        source,
        target,
        signals,
        cross_thread_rules=cross_thread_rules,
    )
    structural_score, structural_reason_codes = _structural_signal_score_and_reasons(signals)

    if base_evidence is not None:
        base_evidence = _evidence_with_local_context_delta(
            base_evidence,
            signals.local_context_delta,
        )
        evidence = _Evidence(
            score=base_evidence.score,
            reason_codes=_dedupe_reason_codes(
                list(base_evidence.reason_codes) + list(structural_reason_codes)
            ),
            excerpt_similarity=base_evidence.excerpt_similarity,
            topic_label_similarity=base_evidence.topic_label_similarity,
            shared_keywords=base_evidence.shared_keywords,
            normalized_label_match=base_evidence.normalized_label_match,
            raw_label_match=base_evidence.raw_label_match,
            timestamp_delta_ms=base_evidence.timestamp_delta_ms,
            volume_gap=base_evidence.volume_gap,
            temporal_gap_seconds=base_evidence.temporal_gap_seconds,
            continuity_mask=base_evidence.continuity_mask,
            dormancy_score=base_evidence.dormancy_score,
            specificity_score=base_evidence.specificity_score,
            local_context_delta=base_evidence.local_context_delta,
        )
    else:
        evidence = _evidence_from_signals(
            signals=signals,
            score=max(score, structural_score),
            reason_codes=_dedupe_reason_codes(
                list(base_reason_codes) + list(structural_reason_codes)
            ),
        )

    return _WeakRecurrenceCandidate(
        evidence=evidence,
        shared_anchor_count=len(signals.shared_strong_anchor_tokens),
    )


def _evidence_for_pair(
    source: _RepresentativeSpanUnit,
    target: _RepresentativeSpanUnit,
    *,
    recurrence_context: _RecurrenceInstrumentationContext,
    token_dictionary_signals: TokenDictionarySignals | None = None,
    cross_thread_rules: CrossThreadLexicalRules,
) -> _Evidence | None:
    signals = _pair_signals(
        source,
        target,
        recurrence_context=recurrence_context,
        token_dictionary_signals=token_dictionary_signals,
    )
    return _evidence_for_pair_from_signals(
        source,
        target,
        signals,
        cross_thread_rules=cross_thread_rules,
    )


def _evidence_for_pair_from_signals(
    source: _RepresentativeSpanUnit,
    target: _RepresentativeSpanUnit,
    signals: _PairSignals,
    *,
    cross_thread_rules: CrossThreadLexicalRules,
) -> _Evidence | None:
    if signals.residue_pair:
        return None
    score, reason_codes, has_strong_signal = _score_and_reasons_for_pair(
        source,
        target,
        signals,
        cross_thread_rules=cross_thread_rules,
    )
    if not has_strong_signal or not reason_codes:
        return None
    return _evidence_from_signals(
        signals=signals,
        score=score,
        reason_codes=reason_codes,
    )


def _candidate_row(
    *,
    source: _RepresentativeSpanUnit,
    target: _RepresentativeSpanUnit,
    evidence: _Evidence,
    rank: int,
    embedding_similarity: float | None = None,
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
        "timestamp_delta_ms": evidence.timestamp_delta_ms,
        "volume_gap": evidence.volume_gap,
        "temporal_gap_seconds": evidence.temporal_gap_seconds,
        "continuity_mask": evidence.continuity_mask,
        "dormancy_score": evidence.dormancy_score,
        "specificity_score": evidence.specificity_score,
        "local_context_delta": evidence.local_context_delta,
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
            "volume_gap": evidence.volume_gap,
            "temporal_gap_seconds": evidence.temporal_gap_seconds,
            "continuity_mask": evidence.continuity_mask,
            "dormancy_score": evidence.dormancy_score,
            "specificity_score": evidence.specificity_score,
            "local_context_delta": evidence.local_context_delta,
        },
    }
    if embedding_similarity is not None:
        row["embedding_similarity"] = embedding_similarity
    if source.unit_kind != "semantic_topic_span" or target.unit_kind != "semantic_topic_span":
        row["source_unit_kind"] = source.unit_kind
        row["target_unit_kind"] = target.unit_kind
    if source.segment_id is not None:
        row["source_segment_id"] = source.segment_id
    if target.segment_id is not None:
        row["target_segment_id"] = target.segment_id
    if source.summary_source is not None:
        row["source_summary_source"] = source.summary_source
    if target.summary_source is not None:
        row["target_summary_source"] = target.summary_source
    if source.summary_confidence is not None:
        row["source_summary_confidence"] = source.summary_confidence
    if target.summary_confidence is not None:
        row["target_summary_confidence"] = target.summary_confidence
    errors = list(load_cross_thread_candidate_validator().iter_errors(row))
    if errors:
        raise CrossThreadCandidateError(
            f"cross-thread candidate schema validation failed: {errors[0].message}"
        )
    return row


def _unit_key(unit: _RepresentativeSpanUnit) -> tuple[str, str]:
    return (unit.conversation_id, unit.span_id)


def _candidate_unit_key(row: dict[str, Any], side: str) -> tuple[str, str, str]:
    return (
        str(row[f"{side}_conversation_id"]),
        str(row.get(f"{side}_unit_kind") or "semantic_topic_span"),
        str(row.get(f"{side}_segment_id") or row[f"{side}_span_id"]),
    )


def _candidate_pair_key(row: dict[str, Any]) -> tuple[tuple[str, str, str], tuple[str, str, str]]:
    source_key = _candidate_unit_key(row, "source")
    target_key = _candidate_unit_key(row, "target")
    if source_key <= target_key:
        return (source_key, target_key)
    return (target_key, source_key)


def _summary_source_rank(value: Any) -> int:
    return 1 if value == "local_llm" else 0


def _candidate_dedupe_sort_key(row: dict[str, Any]) -> tuple[float, int, int, int]:
    timestamp_delta = row.get("timestamp_delta_ms")
    timestamp_rank = (
        -int(timestamp_delta)
        if isinstance(timestamp_delta, int)
        else -1_000_000_000_000_000
    )
    source_rank = _summary_source_rank(row.get("source_summary_source"))
    target_rank = _summary_source_rank(row.get("target_summary_source"))
    return (
        float(row["score"]),
        source_rank + target_rank,
        timestamp_rank,
        -int(row.get("rank", 0)),
    )


def _dedupe_undirected_candidate_rows(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    selected_by_pair: dict[
        tuple[tuple[str, str, str], tuple[str, str, str]],
        dict[str, Any],
    ] = {}
    for row in rows:
        pair_key = _candidate_pair_key(row)
        existing = selected_by_pair.get(pair_key)
        if existing is None or _candidate_dedupe_sort_key(row) > _candidate_dedupe_sort_key(existing):
            selected_by_pair[pair_key] = row
    deduped = list(selected_by_pair.values())
    return deduped, len(rows) - len(deduped)


def _is_low_value_artifact_instruction_text(
    text: str,
    *,
    cross_thread_rules: CrossThreadLexicalRules,
) -> bool:
    text_norm = " ".join(text.lower().split())
    if len(text_norm) < 48:
        return False
    prompt_markers = cross_thread_rules.residue.prompt_exact_markers
    has_wrapper = any(
        marker in text_norm for marker in prompt_markers if "returned" in marker
    )
    has_turn_control = any(
        marker in text_norm
        for marker in prompt_markers
        if "end this turn" in marker or marker.startswith("do not ") or "from now on" in marker
    )
    return has_wrapper and has_turn_control


def _should_filter_low_value_pair(
    source: _RepresentativeSpanUnit,
    target: _RepresentativeSpanUnit,
    evidence: _Evidence,
    *,
    cross_thread_rules: CrossThreadLexicalRules,
) -> bool:
    return (
        evidence.excerpt_similarity >= 0.78
        and _is_low_value_artifact_instruction_text(
            source.excerpt,
            cross_thread_rules=cross_thread_rules,
        )
        and _is_low_value_artifact_instruction_text(
            target.excerpt,
            cross_thread_rules=cross_thread_rules,
        )
    )


def _reconstructed_unit_text(
    unit: _RepresentativeSpanUnit,
    windows: dict[tuple[str, str], WindowPreviewRecord],
) -> str | None:
    if not unit.source_window_ids:
        return None
    message_lookup: dict[str, Any] = {}
    for window_id in unit.source_window_ids:
        record = windows.get((unit.conversation_id, window_id))
        if record is None:
            return None
        for message in record.messages:
            message_lookup.setdefault(message.message_id, message)
    texts: list[str] = []
    for message_id in unit.message_ids:
        message = message_lookup.get(message_id)
        if message is None:
            return None
        if message.text:
            texts.append(message.text)
    return "\n\n".join(texts)


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return max(
        -1.0,
        min(
            1.0,
            sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm),
        ),
    )


def _embedding_similarity_by_pair(
    *,
    input_root: Path,
    ranked_candidates: list[tuple[_RepresentativeSpanUnit, list[tuple[_RepresentativeSpanUnit, _Evidence]]]],
    embedding_model: str,
    embedding_base_url: str,
    embedding_timeout_seconds: float,
) -> dict[tuple[tuple[str, str], tuple[str, str]], float]:
    try:
        windows = load_window_preview_index(input_root)
        backend = create_embedding_backend(
            backend_name="ollama",
            model=embedding_model,
            backend_options={
                "base_url": embedding_base_url,
                "timeout_seconds": embedding_timeout_seconds,
            },
        )
    except Exception:
        return {}

    units_by_key: dict[tuple[str, str], _RepresentativeSpanUnit] = {}
    for source, targets in ranked_candidates:
        units_by_key.setdefault(_unit_key(source), source)
        for target, _ in targets:
            units_by_key.setdefault(_unit_key(target), target)

    ordered_unit_keys = sorted(units_by_key)
    texts: list[str] = []
    text_keys: list[tuple[str, str]] = []
    for key in ordered_unit_keys:
        text = _reconstructed_unit_text(units_by_key[key], windows)
        if not text:
            continue
        text_keys.append(key)
        texts.append(text)
    if not texts:
        return {}

    try:
        vectors = backend.embed(texts)
    except Exception:
        return {}
    if len(vectors) != len(texts):
        return {}

    vectors_by_key = {
        key: vector
        for key, vector in zip(text_keys, vectors, strict=True)
    }
    similarities: dict[tuple[tuple[str, str], tuple[str, str]], float] = {}
    for source, targets in ranked_candidates:
        source_key = _unit_key(source)
        source_vector = vectors_by_key.get(source_key)
        if source_vector is None:
            continue
        for target, _ in targets:
            target_key = _unit_key(target)
            target_vector = vectors_by_key.get(target_key)
            if target_vector is None:
                continue
            similarities[(source_key, target_key)] = round(
                _cosine_similarity(source_vector, target_vector),
                4,
            )
    return similarities


def _build_cross_thread_candidate_rows_with_stats(
    input_root: Path,
    *,
    min_score: float = DEFAULT_CROSS_THREAD_MIN_SCORE,
    top_per_source: int = DEFAULT_CROSS_THREAD_TOP_PER_SOURCE,
    unit_source: str = DEFAULT_CROSS_THREAD_UNIT_SOURCE,
    embedding_model: str | None = None,
    embedding_base_url: str = "http://localhost:11434",
    embedding_timeout_seconds: float = 30.0,
    locale: str = DEFAULT_CROSS_THREAD_LEXICAL_LOCALE,
) -> tuple[
    list[dict[str, Any]],
    int,
    int,
    _UnitLoadResult,
    _TopicSummaryAdmissionStats,
]:
    if top_per_source < 1:
        raise CrossThreadCandidateError("top_per_source must be at least 1")
    if min_score < 0 or min_score > 1:
        raise CrossThreadCandidateError("min_score must be between 0 and 1")

    lexical_rules = load_token_dictionary_lexical_rules(input_root)
    token_dictionary_signals = load_token_dictionary_signals(input_root)
    cross_thread_rules = load_cross_thread_lexical_rules(locale)
    unit_load_result = _load_units(
        input_root,
        requested_unit_source=unit_source,
        lexical_rules=lexical_rules,
        token_dictionary_signals=token_dictionary_signals,
        cross_thread_rules=cross_thread_rules,
    )
    units = unit_load_result.units
    recurrence_context = _build_recurrence_instrumentation_context(input_root, units)
    filtered_low_value_pair_count = 0
    topic_summary_admission_filtered_count = 0
    topic_summary_admission_filter_reasons: Counter[str] = Counter()
    selected_by_source: list[
        tuple[_RepresentativeSpanUnit, list[tuple[_RepresentativeSpanUnit, _Evidence]]]
    ] = []
    min_score_rounded = round(min_score, 4)
    topic_summary_mode = unit_load_result.unit_source == "topic-summaries"
    similarity_cache: _SimilarityCache | None = {} if topic_summary_mode else None
    for source in units:
        ranked_similarity: list[tuple[_RepresentativeSpanUnit, _Evidence]] = []
        ranked_weak: list[tuple[_RepresentativeSpanUnit, _Evidence, int]] = []
        for target in units:
            if source.conversation_id == target.conversation_id:
                continue
            if source.topic_id == target.topic_id and source.span_id == target.span_id:
                continue
            if topic_summary_mode:
                pair_signals = _pair_signals(
                    source,
                    target,
                    recurrence_context=recurrence_context,
                    token_dictionary_signals=token_dictionary_signals,
                    similarity_cache=similarity_cache,
                    compute_local_context_delta=False,
                )
                evidence = _evidence_for_pair_from_signals(
                    source,
                    target,
                    pair_signals,
                    cross_thread_rules=cross_thread_rules,
                )
                weak_candidate = _weak_recurrence_evidence_for_pair(
                    source,
                    target,
                    recurrence_context=recurrence_context,
                    token_dictionary_signals=token_dictionary_signals,
                    cross_thread_rules=cross_thread_rules,
                    base_evidence=evidence,
                    signals=pair_signals,
                    similarity_cache=similarity_cache,
                )
                admission_signals = pair_signals
            else:
                evidence = _evidence_for_pair(
                    source,
                    target,
                    recurrence_context=recurrence_context,
                    token_dictionary_signals=token_dictionary_signals,
                    cross_thread_rules=cross_thread_rules,
                )
                weak_candidate = _weak_recurrence_evidence_for_pair(
                    source,
                    target,
                    recurrence_context=recurrence_context,
                    token_dictionary_signals=token_dictionary_signals,
                    cross_thread_rules=cross_thread_rules,
                    base_evidence=evidence,
                )
                admission_signals = None
            similarity_evidence = weak_candidate.evidence if weak_candidate is not None else evidence
            if similarity_evidence is None and weak_candidate is None:
                continue
            candidate_for_filter = similarity_evidence or weak_candidate.evidence
            assert candidate_for_filter is not None
            if _should_filter_low_value_pair(
                source,
                target,
                candidate_for_filter,
                cross_thread_rules=cross_thread_rules,
            ):
                filtered_low_value_pair_count += 1
                continue
            if admission_signals is None:
                admission_signals = _pair_signals(
                    source,
                    target,
                    recurrence_context=recurrence_context,
                    token_dictionary_signals=token_dictionary_signals,
                )
            admission_filter_reason = _topic_summary_admission_filter_reason(
                source,
                target,
                candidate_for_filter,
                admission_signals,
                cross_thread_rules=cross_thread_rules,
            )
            if admission_filter_reason is not None:
                topic_summary_admission_filtered_count += 1
                topic_summary_admission_filter_reasons[admission_filter_reason] += 1
                continue
            if similarity_evidence is not None and similarity_evidence.score >= min_score_rounded:
                if topic_summary_mode:
                    pair_signals = _signals_with_local_context_delta(
                        pair_signals,
                        source=source,
                        target=target,
                        context=recurrence_context,
                        similarity_cache=similarity_cache,
                    )
                    similarity_evidence = _evidence_with_local_context_delta(
                        similarity_evidence,
                        pair_signals.local_context_delta,
                    )
                ranked_similarity.append((target, similarity_evidence))
            elif weak_candidate is not None:
                ranked_weak.append(
                    (
                        target,
                        weak_candidate.evidence,
                        weak_candidate.shared_anchor_count,
                    )
                )
        ranked_similarity.sort(
            key=lambda item: (
                -item[1].score,
                -item[1].excerpt_similarity,
                item[0].conversation_id,
                item[0].topic_id,
                item[0].span_id,
            )
        )
        ranked_weak.sort(
            key=lambda item: (
                -item[2],
                -item[1].dormancy_score,
                -(item[1].local_context_delta if item[1].local_context_delta is not None else -1.0),
                -item[1].specificity_score,
                -item[1].score,
                item[0].conversation_id,
                item[0].topic_id,
                item[0].span_id,
            )
        )
        selected = ranked_similarity[:top_per_source]
        selected_target_keys = {_unit_key(target) for target, _evidence in selected}
        weak_selected = [
            (target, evidence)
            for target, evidence, _shared_anchor_count in ranked_weak
            if _unit_key(target) not in selected_target_keys
        ][:top_per_source]
        selected.extend(weak_selected)
        if selected:
            selected_by_source.append((source, selected))

    embedding_similarities: dict[tuple[tuple[str, str], tuple[str, str]], float] = {}
    if embedding_model:
        embedding_similarities = _embedding_similarity_by_pair(
            input_root=input_root,
            ranked_candidates=selected_by_source,
            embedding_model=embedding_model,
            embedding_base_url=embedding_base_url,
            embedding_timeout_seconds=embedding_timeout_seconds,
        )

    rows: list[dict[str, Any]] = []
    for source, ranked in selected_by_source:
        ranked.sort(
            key=lambda item: (
                -item[1].score,
                -embedding_similarities.get((_unit_key(source), _unit_key(item[0])), -1.0),
                -item[1].excerpt_similarity,
                item[0].conversation_id,
                item[0].topic_id,
                item[0].span_id,
            )
        )
        for rank, (target, evidence) in enumerate(ranked, start=1):
            embedding_similarity = embedding_similarities.get(
                (_unit_key(source), _unit_key(target))
            )
            rows.append(
                _candidate_row(
                    source=source,
                    target=target,
                    evidence=evidence,
                    rank=rank,
                    embedding_similarity=embedding_similarity,
                )
            )

    rows, duplicate_pairs_removed = _dedupe_undirected_candidate_rows(rows)
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
    return (
        rows,
        filtered_low_value_pair_count,
        duplicate_pairs_removed,
        unit_load_result,
        _TopicSummaryAdmissionStats(
            filtered_count=topic_summary_admission_filtered_count,
            filter_reasons=topic_summary_admission_filter_reasons,
        ),
    )


def build_cross_thread_candidate_rows(
    input_root: Path,
    *,
    min_score: float = DEFAULT_CROSS_THREAD_MIN_SCORE,
    top_per_source: int = DEFAULT_CROSS_THREAD_TOP_PER_SOURCE,
    unit_source: str = DEFAULT_CROSS_THREAD_UNIT_SOURCE,
    embedding_model: str | None = None,
    embedding_base_url: str = "http://localhost:11434",
    embedding_timeout_seconds: float = 30.0,
    locale: str = DEFAULT_CROSS_THREAD_LEXICAL_LOCALE,
) -> list[dict[str, Any]]:
    (
        rows,
        _filtered_low_value_pair_count,
        _duplicate_pairs_removed,
        _unit_load_result,
        _topic_summary_admission_stats,
    ) = _build_cross_thread_candidate_rows_with_stats(
        input_root,
        min_score=min_score,
        top_per_source=top_per_source,
        unit_source=unit_source,
        embedding_model=embedding_model,
        embedding_base_url=embedding_base_url,
        embedding_timeout_seconds=embedding_timeout_seconds,
        locale=locale,
    )
    return rows


def _score_band(score: float, *, unit_source: str) -> str:
    if unit_source == "topic-summaries":
        if score >= 0.7:
            return "high"
        if score >= 0.45:
            return "medium"
        return "low"
    if score >= 0.9:
        return "high"
    if score >= 0.75:
        return "medium"
    return "low"


def _summary(
    *,
    provider_id: str,
    generated_from: str,
    source_unit_count: int,
    unit_source: str,
    topic_summary_stats: _TopicSummaryLoadStats,
    rows: list[dict[str, Any]],
    min_score: float,
    top_per_source: int,
    filtered_low_value_pair_count: int = 0,
    duplicate_pairs_removed: int = 0,
    topic_summary_admission_stats: _TopicSummaryAdmissionStats | None = None,
) -> dict[str, Any]:
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
        score_bands[_score_band(float(row["score"]), unit_source=unit_source)] += 1
    summary = {
        "artifact_type": CROSS_THREAD_CANDIDATE_SUMMARY_ARTIFACT_TYPE,
        "schema_version": CROSS_THREAD_CANDIDATE_SCHEMA_VERSION,
        "provider_id": provider_id,
        "generated_from": generated_from,
        "unit_source": unit_source,
        "source_unit_count": source_unit_count,
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
    summary["topic_summary_units"] = {
        "files_found": topic_summary_stats.files_found,
        "units_loaded": topic_summary_stats.units_loaded,
        "skipped_invalid": topic_summary_stats.skipped_invalid,
        "skipped_empty": topic_summary_stats.skipped_empty,
        "skipped_low_confidence": topic_summary_stats.skipped_low_confidence,
    }
    if topic_summary_admission_stats is not None:
        filter_reasons = topic_summary_admission_stats.filter_reasons or Counter()
        summary["topic_summary_admission_filtered_count"] = (
            topic_summary_admission_stats.filtered_count
        )
        summary["topic_summary_admission_filter_reasons"] = dict(
            sorted(filter_reasons.items())
        )
    if filtered_low_value_pair_count:
        summary["filtered_low_value_pair_count"] = filtered_low_value_pair_count
    summary["duplicate_pairs_removed"] = duplicate_pairs_removed
    return summary


def write_cross_thread_candidates_artifact(
    input_root: Path,
    *,
    min_score: float = DEFAULT_CROSS_THREAD_MIN_SCORE,
    top_per_source: int = DEFAULT_CROSS_THREAD_TOP_PER_SOURCE,
    unit_source: str = DEFAULT_CROSS_THREAD_UNIT_SOURCE,
    embedding_model: str | None = None,
    embedding_base_url: str = "http://localhost:11434",
    embedding_timeout_seconds: float = 30.0,
    locale: str = DEFAULT_CROSS_THREAD_LEXICAL_LOCALE,
) -> dict[str, Any]:
    provider_root = input_root.expanduser()
    if not provider_root.exists() or not provider_root.is_dir():
        raise CrossThreadCandidateError(f"provider root not found: {provider_root}")

    (
        rows,
        filtered_low_value_pair_count,
        duplicate_pairs_removed,
        unit_load_result,
        topic_summary_admission_stats,
    ) = _build_cross_thread_candidate_rows_with_stats(
        provider_root,
        min_score=min_score,
        top_per_source=top_per_source,
        unit_source=unit_source,
        embedding_model=embedding_model,
        embedding_base_url=embedding_base_url,
        embedding_timeout_seconds=embedding_timeout_seconds,
        locale=locale,
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

    provider_id = (
        unit_load_result.topics_artifact["provider_id"]
        if unit_load_result.topics_artifact is not None
        else unit_load_result.units[0].provider_id
        if unit_load_result.units
        else "unknown"
    )
    generated_from = (
        str((provider_root / "l3" / "semantic-topics" / "topics.json").resolve())
        if unit_load_result.unit_source == "semantic-topics"
        else "thread-*/l3/intra-thread-topics/topic-summaries.jsonl"
    )
    summary = _summary(
        provider_id=provider_id,
        generated_from=generated_from,
        source_unit_count=len(unit_load_result.units),
        unit_source=unit_load_result.unit_source,
        topic_summary_stats=unit_load_result.topic_summary_stats,
        rows=rows,
        min_score=min_score,
        top_per_source=top_per_source,
        filtered_low_value_pair_count=filtered_low_value_pair_count,
        duplicate_pairs_removed=duplicate_pairs_removed,
        topic_summary_admission_stats=(
            topic_summary_admission_stats
            if unit_load_result.unit_source == "topic-summaries"
            else None
        ),
    )
    write_json_artifact(summary_path, summary)

    return {
        "candidate_count": len(rows),
        "duplicate_pairs_removed": duplicate_pairs_removed,
        "unit_source": unit_load_result.unit_source,
        "candidates_path": candidates_path,
        "summary_path": summary_path,
    }
