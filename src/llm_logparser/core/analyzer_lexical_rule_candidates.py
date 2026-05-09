from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .analyzer_common import normalize_analysis_text, write_json_artifact
from .analyzer_token_dictionary import (
    legacy_token_dictionary_path,
    observed_tokens_path,
    resolve_existing_token_dictionary_path,
    token_bundles_path,
)
from .schema_validation import load_token_bundles_validator, load_token_dictionary_validator
from llm_logparser.resources.cross_thread_lexical import (
    CrossThreadLexicalRules,
    CrossThreadLexicalRulesError,
    cross_thread_lexical_rules_diagnostics,
    load_cross_thread_lexical_rules,
)

LEXICAL_RULE_CANDIDATE_SCHEMA_VERSION = "0.1"
LEXICAL_RULE_CANDIDATE_RECORD_TYPE = "lexical_rule_candidate"
LEXICAL_RULE_CANDIDATE_DIAGNOSTICS_ARTIFACT_TYPE = "lexical_rule_candidates_diagnostics"
LEXICAL_RULE_CANDIDATE_METHOD = "token_dictionary_spread_v0"
PERSONA_WEAK_TOKEN_CANDIDATE_METHOD = "token_dictionary_persona_spread_v0"
DISTINCTIVE_ALLOW_TOKEN_CANDIDATE_METHOD = "token_dictionary_distinctive_allow_v0"
DEFAULT_LEXICAL_RULE_CANDIDATE_MAX_PER_TYPE = 100
DEFAULT_LEXICAL_RULE_CANDIDATE_SAMPLE_LIMIT = 5
GENERIC_MIN_CONVERSATION_COUNT = 8
GENERIC_MIN_DOCUMENT_COUNT = 25
PERSONA_MIN_CONVERSATION_COUNT = 8
PERSONA_MIN_DOCUMENT_COUNT = 25
DISTINCTIVE_MIN_CONVERSATION_COUNT = 4
DISTINCTIVE_MIN_DOCUMENT_COUNT = 8
TOPIC_SUMMARY_EXCERPT_MAX_CHARS = 240
_TOKEN_SYMBOL_RE = re.compile(r"[./_:-]")
_URL_LIKE_RE = re.compile(r"(?:://|^www\.|^[a-z][a-z0-9+.-]*://)", re.IGNORECASE)
_PATH_LIKE_RE = re.compile(r"(?:[/\\]|^\.\.?[/\\]|^~[/\\]|^[a-zA-Z]:[\\/])")
_DATE_LIKE_RE = re.compile(
    r"^(?:\d{4}[-_/年]\d{1,2}(?:[-_/月]\d{1,2}日?)?|\d{1,2}[-_/]\d{1,2}[-_/]\d{2,4})$"
)
_HASH_OR_ID_RE = re.compile(r"^(?:[a-f0-9]{12,}|[a-z]+[_-]?[a-f0-9]{8,})$", re.IGNORECASE)
_FILE_EXTENSION_LIKE = {
    "avi",
    "bak",
    "bin",
    "bmp",
    "bz2",
    "cfg",
    "class",
    "conf",
    "db",
    "dll",
    "doc",
    "docx",
    "dylib",
    "exe",
    "gif",
    "gz",
    "heic",
    "ico",
    "ini",
    "jpeg",
    "jpg",
    "lock",
    "log",
    "md",
    "mov",
    "mp3",
    "mp4",
    "obj",
    "png",
    "ppt",
    "pptx",
    "pyc",
    "rar",
    "rtf",
    "so",
    "svg",
    "tmp",
    "toml",
    "tsv",
    "txt",
    "wav",
    "webp",
    "xls",
    "xlsx",
    "xml",
    "zip",
}
_DISTINCTIVE_DOMAIN_TERMS = {
    "adapter",
    "analyzer",
    "artifact",
    "canonical",
    "candidate",
    "cluster",
    "cross",
    "dictionary",
    "heuristic",
    "lexical",
    "normalization",
    "parsed",
    "policy",
    "roadmap",
    "schema",
    "segment",
    "semantic",
    "summary",
    "thread",
    "token",
    "topic",
    "window",
}
_HARD_SHAPE_SKIP_REASONS = {
    "malformed_token",
    "shape_url_like",
    "shape_path_like",
    "shape_date_like",
    "shape_numeric",
    "shape_symbol_like",
    "shape_identifier_like",
    "shape_too_long",
}


class LexicalRuleCandidateError(RuntimeError):
    pass


def lexical_rule_candidates_dir(input_root: Path) -> Path:
    return input_root / "l3" / "lexical-rules"


def lexical_rule_candidates_path(input_root: Path) -> Path:
    return lexical_rule_candidates_dir(input_root) / "candidates.jsonl"


def lexical_rule_candidate_diagnostics_path(input_root: Path) -> Path:
    return lexical_rule_candidates_dir(input_root) / "diagnostics.json"


def lexical_rule_candidate_review_path(input_root: Path) -> Path:
    return lexical_rule_candidates_dir(input_root) / "review.md"


def _admission_key(value: str) -> str:
    normalized = normalize_analysis_text(value)
    return re.sub(r"[^a-z0-9一-龯ぁ-んァ-ヶー]+", "", normalized)


def _has_cjk(value: str) -> bool:
    return bool(re.search(r"[一-龯ぁ-んァ-ヶー]", value))


def _is_all_cjk(value: str) -> bool:
    return bool(value) and all(re.fullmatch(r"[一-龯ぁ-んァ-ヶー]", char) for char in value)


def _alnum_or_cjk_count(value: str) -> int:
    return sum(1 for char in value if char.isalnum() or _has_cjk(char))


def _load_dictionary_payload(provider_root: Path) -> dict[str, Any]:
    path = resolve_existing_token_dictionary_path(provider_root)
    if not path.exists():
        raise LexicalRuleCandidateError(
            "observed token artifact not found: "
            f"{observed_tokens_path(provider_root)} "
            f"(legacy alias: {legacy_token_dictionary_path(provider_root)})"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LexicalRuleCandidateError(f"invalid JSON in {path}: {exc.msg}") from exc
    errors = list(load_token_dictionary_validator().iter_errors(payload))
    if errors:
        raise LexicalRuleCandidateError(
            f"token dictionary schema validation failed for {path}: {errors[0].message}"
        )
    return payload


def _load_token_bundle_counts(provider_root: Path) -> tuple[dict[str, int], bool]:
    path = token_bundles_path(provider_root)
    if not path.exists():
        return {}, False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LexicalRuleCandidateError(f"invalid JSON in {path}: {exc.msg}") from exc
    errors = list(load_token_bundles_validator().iter_errors(payload))
    if errors:
        raise LexicalRuleCandidateError(
            f"token bundles schema validation failed for {path}: {errors[0].message}"
        )
    counts: dict[str, int] = defaultdict(int)
    for bundle in payload.get("bundles", []):
        tokens = bundle.get("tokens", [])
        if not isinstance(tokens, list):
            continue
        for token in tokens:
            if isinstance(token, str) and token.strip():
                counts[normalize_analysis_text(token)] += 1
    return dict(counts), True


def _topic_summary_paths(provider_root: Path) -> list[Path]:
    return sorted(
        provider_root.glob("thread-*/l3/intra-thread-topics/topic-summaries.jsonl")
    )


def _short_excerpt(value: str, *, max_chars: int = TOPIC_SUMMARY_EXCERPT_MAX_CHARS) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[: max_chars - 3].rstrip()}..."


def _string_values(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item.strip()]
    return []


def _topic_summary_field_values(row: dict[str, Any]) -> list[tuple[str, str, str]]:
    fields: list[tuple[str, str, str]] = []
    for value in _string_values(row.get("title")):
        fields.append(("title", "topic_summary.title", value))
    for value in _string_values(row.get("summary")):
        fields.append(("summary", "topic_summary.summary", value))
    for key in ("keywords", "keyphrases"):
        for value in _string_values(row.get(key)):
            fields.append(("keyword", f"topic_summary.{key}", value))
    for key in ("conclusion_text", "conclusion"):
        for value in _string_values(row.get(key)):
            fields.append(("conclusion", f"topic_summary.{key}", value))
    for key in ("topic_label", "label"):
        for value in _string_values(row.get(key)):
            fields.append(("title", f"topic_summary.{key}", value))
    return fields


def _load_topic_summary_evidence(
    provider_root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    paths = _topic_summary_paths(provider_root)
    diagnostics: dict[str, Any] = {
        "status": "not_found",
        "files_found": len(paths),
        "rows_loaded": 0,
        "rows_malformed": 0,
        "fields_indexed": {},
    }
    if not paths:
        return [], diagnostics

    records: list[dict[str, Any]] = []
    field_counts: Counter[str] = Counter()
    for path in paths:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    row = json.loads(stripped)
                except json.JSONDecodeError:
                    diagnostics["rows_malformed"] += 1
                    continue
                if not isinstance(row, dict):
                    diagnostics["rows_malformed"] += 1
                    continue
                fields = _topic_summary_field_values(row)
                if not fields:
                    diagnostics["rows_malformed"] += 1
                    continue
                diagnostics["rows_loaded"] += 1
                conversation_id = str(row.get("conversation_id") or "")
                segment_id = str(row.get("segment_id") or "")
                for category, field_name, value in fields:
                    field_counts[field_name] += 1
                    records.append(
                        {
                            "conversation_id": conversation_id,
                            "segment_id": segment_id,
                            "category": category,
                            "field": field_name,
                            "text": value,
                            "normalized_text": normalize_analysis_text(value),
                        }
                    )
    diagnostics["status"] = "loaded"
    diagnostics["fields_indexed"] = dict(sorted(field_counts.items()))
    return records, diagnostics


def _active_token_keys(cross_thread_rules: CrossThreadLexicalRules) -> set[str]:
    values: list[str] = []
    values.extend(cross_thread_rules.topic_summary_admission.generic_anchor_tokens)
    values.extend(cross_thread_rules.topic_summary_scoring.generic_tokens)
    values.extend(cross_thread_rules.topic_summary_scoring.short_specific_tokens)
    values.extend(cross_thread_rules.topic_summary_scoring.distinctive_allow_tokens)
    values.extend(cross_thread_rules.topic_summary_scoring.distinctive_block_tokens)
    values.extend(cross_thread_rules.topic_summary_scoring.weak_distinctive_tokens)
    values.extend(cross_thread_rules.topic_summary_scoring.persona_weak_tokens)
    return {key for value in values if (key := _admission_key(value))}


def _active_patterns(cross_thread_rules: CrossThreadLexicalRules) -> tuple[str, ...]:
    return tuple(
        list(cross_thread_rules.topic_summary_admission.generic_anchor_patterns)
        + list(cross_thread_rules.topic_summary_scoring.generic_patterns)
        + list(cross_thread_rules.topic_summary_scoring.tool_residue_patterns)
        + list(cross_thread_rules.topic_summary_scoring.citation_residue_patterns)
    )


def _is_active_token(
    normalized_value: str,
    *,
    active_keys: set[str],
    active_patterns: tuple[str, ...],
) -> bool:
    key = _admission_key(normalized_value)
    if not key:
        return False
    if key in active_keys:
        return True
    return any(re.fullmatch(pattern, key) for pattern in active_patterns)


def _document_count(row: dict[str, Any]) -> int:
    # Phase 1 maps the current dictionary schema's closest available spread
    # fields into a document-count proxy. Future dictionary schemas can add an
    # explicit document_count without changing candidate rows.
    explicit = row.get("document_count")
    if isinstance(explicit, int):
        return explicit
    return max(_int_field(row, "conversation_count"), _int_field(row, "topic_count"))


def _int_field(row: dict[str, Any], key: str) -> int:
    value = row.get(key)
    if isinstance(value, int):
        return value
    return 0


def _low_specificity_shape(token: str) -> bool:
    if _has_cjk(token):
        return len(token) <= 3
    if _TOKEN_SYMBOL_RE.search(token):
        return False
    if any(char.isdigit() for char in token):
        return False
    return len(token) <= 8


def _shape_skip_reason(
    token: str,
    *,
    count: int,
    conversation_count: int,
    document_count: int,
) -> str | None:
    stripped = token.strip()
    if not stripped:
        return "malformed_token"
    if _URL_LIKE_RE.search(stripped):
        return "shape_url_like"
    if _PATH_LIKE_RE.search(stripped):
        return "shape_path_like"
    if _DATE_LIKE_RE.fullmatch(stripped):
        return "shape_date_like"
    if stripped.isdecimal():
        return "shape_numeric"
    if _alnum_or_cjk_count(stripped) * 2 < len(stripped):
        return "shape_symbol_like"
    if _HASH_OR_ID_RE.fullmatch(stripped):
        return "shape_identifier_like"

    has_cjk = _has_cjk(stripped)
    latin_or_digit = bool(re.search(r"[a-z0-9]", stripped, re.IGNORECASE))
    if not has_cjk and len(stripped) < 3:
        return "shape_too_short"
    if has_cjk:
        if _is_all_cjk(stripped) and len(stripped) == 1:
            if count < 500 or conversation_count < 50 or document_count < 100:
                return "shape_too_short"
        if _is_all_cjk(stripped) and len(stripped) == 2:
            if count < 80 or conversation_count < 10:
                return "below_threshold"
        return None

    if re.search(r"[a-z]", stripped, re.IGNORECASE) and re.search(r"\d", stripped):
        return "shape_identifier_like"
    if len(stripped) > 40:
        return "shape_too_long"
    if len(stripped) > 24 and re.fullmatch(r"[a-z][a-z0-9_-]+", stripped, re.IGNORECASE):
        return "shape_identifier_like"
    if latin_or_digit and stripped in _FILE_EXTENSION_LIKE:
        return "shape_identifier_like"
    return None


def _candidate_score(
    *,
    count: int,
    conversation_count: int,
    document_count: int,
    topic_summary_total_count: int,
    low_specificity: bool,
) -> tuple[float, dict[str, float]]:
    conversation_score = min(1.0, math.log1p(conversation_count) / math.log1p(80))
    document_score = min(1.0, math.log1p(document_count) / math.log1p(200))
    frequency_score = min(1.0, math.log1p(count) / math.log1p(2000))
    topic_summary_score = min(
        1.0,
        math.log1p(topic_summary_total_count) / math.log1p(50),
    )
    shape_score = 1.0 if low_specificity else 0.5
    spread_score = 0.6 * conversation_score + 0.4 * document_score
    score = (
        0.45 * spread_score
        + 0.30 * frequency_score
        + 0.15 * topic_summary_score
        + 0.10 * shape_score
    )
    components = {
        "conversation_score": round(conversation_score, 4),
        "document_score": round(document_score, 4),
        "frequency_score": round(frequency_score, 4),
        "topic_summary_score": round(topic_summary_score, 4),
        "shape_score": round(shape_score, 4),
        "spread_score": round(spread_score, 4),
    }
    return round(min(1.0, score), 4), components


def _candidate_id(
    provider_id: str,
    normalized_value: str,
    *,
    candidate_type: str = "generic_scoring_token",
    method: str = LEXICAL_RULE_CANDIDATE_METHOD,
) -> str:
    digest = hashlib.sha1(
        "|".join(
            (
                provider_id,
                candidate_type,
                normalized_value,
                method,
            )
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"lexcand_{digest}"


def _sample_refs(row: dict[str, Any], *, sample_limit: int) -> list[dict[str, Any]]:
    conversations = row.get("conversations", [])
    if not isinstance(conversations, list):
        conversations = []
    refs: list[dict[str, Any]] = []
    for conversation_id in conversations[:sample_limit]:
        if not isinstance(conversation_id, str) or not conversation_id:
            continue
        refs.append(
            {
                "conversation_id": conversation_id,
                "field": "token_dictionary.token",
            }
        )
    return refs


def _topic_summary_evidence_for_token(
    normalized_value: str,
    *,
    topic_summary_records: list[dict[str, Any]],
    sample_limit: int,
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    counts = {
        "topic_summary_title_count": 0,
        "topic_summary_summary_count": 0,
        "topic_summary_keyword_count": 0,
        "topic_summary_conclusion_count": 0,
        "topic_summary_total_count": 0,
    }
    if not topic_summary_records:
        return counts, []

    refs: list[dict[str, Any]] = []
    matches_topic_summary_text = _topic_summary_text_matcher(normalized_value)
    for record in topic_summary_records:
        normalized_text = str(record.get("normalized_text") or "")
        if not matches_topic_summary_text(normalized_text):
            continue
        category = str(record.get("category") or "")
        count_key = f"topic_summary_{category}_count"
        if count_key not in counts:
            continue
        counts[count_key] += 1
        counts["topic_summary_total_count"] += 1
        if len(refs) >= sample_limit:
            continue
        ref: dict[str, Any] = {
            "conversation_id": str(record.get("conversation_id") or ""),
            "field": str(record.get("field") or "topic_summary"),
            "excerpt": _short_excerpt(str(record.get("text") or "")),
        }
        segment_id = str(record.get("segment_id") or "")
        if segment_id:
            ref["segment_id"] = segment_id
        refs.append(ref)
    return counts, refs


def _topic_summary_text_matcher(normalized_value: str):
    if re.fullmatch(r"[a-z0-9_]+", normalized_value):
        pattern = re.compile(rf"\b{re.escape(normalized_value)}\b")
        return lambda normalized_text: bool(pattern.search(normalized_text))
    return lambda normalized_text: normalized_value in normalized_text


def _topic_summary_contains_uppercase_latin_name(
    normalized_value: str,
    *,
    topic_summary_records: list[dict[str, Any]],
) -> bool:
    if not re.fullmatch(r"[a-z][a-z_-]{2,}", normalized_value):
        return False
    pattern = re.compile(rf"\b{re.escape(normalized_value)}\b", re.IGNORECASE)
    for record in topic_summary_records:
        # Sentence-initial title/summary capitalization is too noisy for persona
        # review candidates. Keyword casing is the safer review signal here.
        if record.get("category") != "keyword":
            continue
        text = str(record.get("text") or "")
        match = pattern.search(text)
        if match and any(char.isupper() for char in match.group(0)):
            return True
    return False


def _persona_reason_codes(
    *,
    token: str,
    normalized_value: str,
    count: int,
    conversation_count: int,
    document_count: int,
    bundle_count: int,
    topic_summary_counts: dict[str, int],
    topic_summary_records: list[dict[str, Any]],
) -> list[str]:
    reasons: list[str] = []
    if re.search(r"(?:さん|ちゃん|くん|君)$", token or normalized_value):
        reasons.append("address_or_honorific_suffix")
    if re.fullmatch(r"[A-Z][A-Za-z_-]{2,}", token):
        reasons.append("latin_name_like_shape")
    if _topic_summary_contains_uppercase_latin_name(
        normalized_value,
        topic_summary_records=topic_summary_records,
    ):
        reasons.append("topic_summary_capitalized_name_usage")
    if (
        _is_all_cjk(normalized_value)
        and 2 <= len(normalized_value) <= 4
        and count >= 100
        and conversation_count >= 20
        and (bundle_count >= 5 or topic_summary_counts["topic_summary_keyword_count"] >= 1)
    ):
        reasons.append("short_cjk_recurring_name_like_token")
    if bundle_count >= 20:
        reasons.append("strong_conversational_bundle_spread")
    if topic_summary_counts["topic_summary_total_count"] >= 10:
        reasons.append("recurring_topic_summary_usage")

    if conversation_count >= PERSONA_MIN_CONVERSATION_COUNT:
        reasons.append("high_conversation_spread")
    if document_count >= PERSONA_MIN_DOCUMENT_COUNT:
        reasons.append("high_document_spread")
    return reasons


def _is_persona_weak_candidate(
    *,
    token: str,
    normalized_value: str,
    count: int,
    conversation_count: int,
    document_count: int,
    bundle_count: int,
    topic_summary_counts: dict[str, int],
    topic_summary_records: list[dict[str, Any]],
) -> tuple[bool, list[str]]:
    reasons = _persona_reason_codes(
        token=token,
        normalized_value=normalized_value,
        count=count,
        conversation_count=conversation_count,
        document_count=document_count,
        bundle_count=bundle_count,
        topic_summary_counts=topic_summary_counts,
        topic_summary_records=topic_summary_records,
    )
    strong_reasons = {
        "address_or_honorific_suffix",
        "latin_name_like_shape",
        "topic_summary_capitalized_name_usage",
        "short_cjk_recurring_name_like_token",
    }
    has_strong_persona_signal = bool(strong_reasons.intersection(reasons))
    has_spread = (
        conversation_count >= PERSONA_MIN_CONVERSATION_COUNT
        and document_count >= PERSONA_MIN_DOCUMENT_COUNT
    )
    return has_strong_persona_signal and has_spread, reasons


def _distinctive_reason_codes(
    *,
    token: str,
    normalized_value: str,
    conversation_count: int,
    document_count: int,
    bundle_count: int,
    topic_summary_counts: dict[str, int],
) -> list[str]:
    reasons: list[str] = []
    parts = {
        part
        for part in re.split(r"[^a-z0-9一-龯ぁ-んァ-ヶー]+", normalized_value)
        if part
    }
    if re.fullmatch(r"[A-Z]{1,6}\d{1,3}", token) or re.fullmatch(r"[A-Z]{2,8}", token):
        reasons.append("distinctive_acronym_or_layer_shape")
    if "-" in token and parts.intersection(_DISTINCTIVE_DOMAIN_TERMS):
        reasons.append("domain_compound_shape")
    if parts.intersection(_DISTINCTIVE_DOMAIN_TERMS):
        reasons.append("project_domain_vocabulary")
    if topic_summary_counts["topic_summary_keyword_count"] >= 1:
        reasons.append("topic_summary_keyword_usage")
    if topic_summary_counts["topic_summary_title_count"] >= 1:
        reasons.append("topic_summary_title_usage")
    if topic_summary_counts["topic_summary_total_count"] >= 3:
        reasons.append("recurring_topic_summary_usage")
    if bundle_count >= 3:
        reasons.append("bundle_cohesion")
    if conversation_count >= DISTINCTIVE_MIN_CONVERSATION_COUNT:
        reasons.append("stable_conversation_spread")
    if document_count >= DISTINCTIVE_MIN_DOCUMENT_COUNT:
        reasons.append("stable_document_spread")
    return reasons


def _is_distinctive_allow_candidate(
    *,
    token: str,
    normalized_value: str,
    conversation_count: int,
    document_count: int,
    bundle_count: int,
    topic_summary_counts: dict[str, int],
) -> tuple[bool, list[str]]:
    reasons = _distinctive_reason_codes(
        token=token,
        normalized_value=normalized_value,
        conversation_count=conversation_count,
        document_count=document_count,
        bundle_count=bundle_count,
        topic_summary_counts=topic_summary_counts,
    )
    strong_reasons = {
        "distinctive_acronym_or_layer_shape",
        "domain_compound_shape",
        "project_domain_vocabulary",
    }
    has_strong_distinctive_signal = bool(strong_reasons.intersection(reasons))
    has_stable_spread = (
        conversation_count >= DISTINCTIVE_MIN_CONVERSATION_COUNT
        and document_count >= DISTINCTIVE_MIN_DOCUMENT_COUNT
    )
    has_supporting_evidence = (
        bundle_count >= 3
        or topic_summary_counts["topic_summary_total_count"] >= 1
        or "distinctive_acronym_or_layer_shape" in reasons
    )
    return has_strong_distinctive_signal and has_stable_spread and has_supporting_evidence, reasons


def _candidate_for_token(
    *,
    provider_id: str,
    row: dict[str, Any],
    bundle_counts: dict[str, int],
    topic_summary_records: list[dict[str, Any]],
    sample_limit: int,
) -> dict[str, Any]:
    token = str(row.get("token") or row.get("normalized") or "")
    normalized_value = normalize_analysis_text(str(row.get("normalized") or token))
    count = _int_field(row, "count")
    conversation_count = _int_field(row, "conversation_count")
    topic_count = _int_field(row, "topic_count")
    document_count = _document_count(row)
    low_specificity = _low_specificity_shape(normalized_value)
    reason_codes = [
        "high_conversation_spread",
        "high_document_spread",
        "high_frequency",
        "broad_corpus_token",
    ]
    if low_specificity:
        reason_codes.append("low_specificity_shape")
    if bundle_counts.get(normalized_value, 0) >= 5:
        reason_codes.append("broad_bundle_spread")
    topic_summary_counts, topic_summary_refs = _topic_summary_evidence_for_token(
        normalized_value,
        topic_summary_records=topic_summary_records,
        sample_limit=sample_limit,
    )
    score, score_components = _candidate_score(
        count=count,
        conversation_count=conversation_count,
        document_count=document_count,
        topic_summary_total_count=topic_summary_counts["topic_summary_total_count"],
        low_specificity=low_specificity,
    )
    return {
        "record_type": LEXICAL_RULE_CANDIDATE_RECORD_TYPE,
        "schema_version": LEXICAL_RULE_CANDIDATE_SCHEMA_VERSION,
        "provider_id": provider_id,
        "candidate_id": _candidate_id(provider_id, normalized_value),
        "candidate_type": "generic_scoring_token",
        "suggested_scope": "project",
        "suggested_rule_path": "topic_summary.scoring.generic_tokens",
        "value": token,
        "value_kind": "token",
        "normalized_value": normalized_value,
        "status": "inactive",
        "activation_state": "requires_review",
        "source": {
            "method": LEXICAL_RULE_CANDIDATE_METHOD,
            "inputs": ["l3/token-dictionary/observed_tokens.json"],
        },
        "evidence": {
            "token_count": count,
            "document_count": document_count,
            "conversation_count": conversation_count,
            "topic_count": topic_count,
            "bundle_count": bundle_counts.get(normalized_value, 0),
            **topic_summary_counts,
            "score": score,
            "score_components": score_components,
            "reason_codes": reason_codes,
        },
        "sample_refs": topic_summary_refs
        or _sample_refs(row, sample_limit=sample_limit),
        "already_active": False,
        "review": {
            "recommendation": "consider",
            "notes": None,
        },
    }


def _persona_candidate_for_token(
    *,
    provider_id: str,
    row: dict[str, Any],
    bundle_counts: dict[str, int],
    topic_summary_records: list[dict[str, Any]],
    sample_limit: int,
    reason_codes: list[str],
) -> dict[str, Any]:
    token = str(row.get("token") or row.get("normalized") or "")
    normalized_value = normalize_analysis_text(str(row.get("normalized") or token))
    count = _int_field(row, "count")
    conversation_count = _int_field(row, "conversation_count")
    topic_count = _int_field(row, "topic_count")
    document_count = _document_count(row)
    topic_summary_counts, topic_summary_refs = _topic_summary_evidence_for_token(
        normalized_value,
        topic_summary_records=topic_summary_records,
        sample_limit=sample_limit,
    )
    score, score_components = _candidate_score(
        count=count,
        conversation_count=conversation_count,
        document_count=document_count,
        topic_summary_total_count=topic_summary_counts["topic_summary_total_count"],
        low_specificity=True,
    )
    return {
        "record_type": LEXICAL_RULE_CANDIDATE_RECORD_TYPE,
        "schema_version": LEXICAL_RULE_CANDIDATE_SCHEMA_VERSION,
        "provider_id": provider_id,
        "candidate_id": _candidate_id(
            provider_id,
            normalized_value,
            candidate_type="persona_weak_token",
            method=PERSONA_WEAK_TOKEN_CANDIDATE_METHOD,
        ),
        "candidate_type": "persona_weak_token",
        "suggested_scope": "project",
        "suggested_rule_path": "topic_summary.scoring.persona_weak_tokens",
        "value": token,
        "value_kind": "token",
        "normalized_value": normalized_value,
        "status": "inactive",
        "activation_state": "requires_review",
        "source": {
            "method": PERSONA_WEAK_TOKEN_CANDIDATE_METHOD,
            "inputs": ["l3/token-dictionary/observed_tokens.json"],
        },
        "evidence": {
            "token_count": count,
            "document_count": document_count,
            "conversation_count": conversation_count,
            "topic_count": topic_count,
            "bundle_count": bundle_counts.get(normalized_value, 0),
            **topic_summary_counts,
            "score": score,
            "score_components": score_components,
            "reason_codes": reason_codes,
        },
        "sample_refs": topic_summary_refs
        or _sample_refs(row, sample_limit=sample_limit),
        "already_active": False,
        "review": {
            "recommendation": "consider",
            "notes": "Consider weakening standalone persona/name/address overlap.",
        },
    }


def _distinctive_allow_candidate_for_token(
    *,
    provider_id: str,
    row: dict[str, Any],
    bundle_counts: dict[str, int],
    topic_summary_records: list[dict[str, Any]],
    sample_limit: int,
    reason_codes: list[str],
) -> dict[str, Any]:
    token = str(row.get("token") or row.get("normalized") or "")
    normalized_value = normalize_analysis_text(str(row.get("normalized") or token))
    count = _int_field(row, "count")
    conversation_count = _int_field(row, "conversation_count")
    topic_count = _int_field(row, "topic_count")
    document_count = _document_count(row)
    topic_summary_counts, topic_summary_refs = _topic_summary_evidence_for_token(
        normalized_value,
        topic_summary_records=topic_summary_records,
        sample_limit=sample_limit,
    )
    score, score_components = _candidate_score(
        count=count,
        conversation_count=conversation_count,
        document_count=document_count,
        topic_summary_total_count=topic_summary_counts["topic_summary_total_count"],
        low_specificity=False,
    )
    return {
        "record_type": LEXICAL_RULE_CANDIDATE_RECORD_TYPE,
        "schema_version": LEXICAL_RULE_CANDIDATE_SCHEMA_VERSION,
        "provider_id": provider_id,
        "candidate_id": _candidate_id(
            provider_id,
            normalized_value,
            candidate_type="distinctive_allow_token",
            method=DISTINCTIVE_ALLOW_TOKEN_CANDIDATE_METHOD,
        ),
        "candidate_type": "distinctive_allow_token",
        "suggested_scope": "project",
        "suggested_rule_path": "topic_summary.scoring.distinctive_allow_tokens",
        "value": token,
        "value_kind": "token",
        "normalized_value": normalized_value,
        "status": "inactive",
        "activation_state": "requires_review",
        "source": {
            "method": DISTINCTIVE_ALLOW_TOKEN_CANDIDATE_METHOD,
            "inputs": ["l3/token-dictionary/observed_tokens.json"],
        },
        "evidence": {
            "token_count": count,
            "document_count": document_count,
            "conversation_count": conversation_count,
            "topic_count": topic_count,
            "bundle_count": bundle_counts.get(normalized_value, 0),
            **topic_summary_counts,
            "score": score,
            "score_components": score_components,
            "reason_codes": reason_codes,
        },
        "sample_refs": topic_summary_refs
        or _sample_refs(row, sample_limit=sample_limit),
        "already_active": False,
        "review": {
            "recommendation": "consider",
            "notes": "Consider protecting this distinctive domain/project token.",
        },
    }


def _candidate_sort_key(row: dict[str, Any]) -> tuple[int, float, int, int, str]:
    evidence = row["evidence"]
    type_rank = {
        "persona_weak_token": 0,
        "distinctive_allow_token": 1,
        "generic_scoring_token": 2,
    }.get(str(row.get("candidate_type")), 9)
    return (
        type_rank,
        -float(evidence["score"]),
        -int(evidence["conversation_count"]),
        -int(evidence["token_count"]),
        str(row["normalized_value"]),
    )


def _review_sort_key(row: dict[str, Any]) -> tuple[float, int, int, str]:
    evidence = row.get("evidence", {})
    return (
        -float(evidence.get("score") or 0.0),
        -int(evidence.get("topic_summary_total_count") or 0),
        -int(evidence.get("conversation_count") or 0),
        str(row.get("normalized_value") or ""),
    )


def build_lexical_rule_candidate_rows(
    provider_root: Path,
    *,
    project_lexical_rules: Path | str | None = None,
    user_lexical_rules: Path | str | None = None,
    max_candidates_per_type: int = DEFAULT_LEXICAL_RULE_CANDIDATE_MAX_PER_TYPE,
    sample_limit: int = DEFAULT_LEXICAL_RULE_CANDIDATE_SAMPLE_LIMIT,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if max_candidates_per_type < 1:
        raise LexicalRuleCandidateError("max_candidates_per_type must be at least 1")
    if sample_limit < 0:
        raise LexicalRuleCandidateError("sample_limit must be at least 0")
    dictionary_payload = _load_dictionary_payload(provider_root)
    bundle_counts, bundles_present = _load_token_bundle_counts(provider_root)
    topic_summary_records, topic_summary_diagnostics = _load_topic_summary_evidence(
        provider_root
    )
    try:
        cross_thread_rules = load_cross_thread_lexical_rules(
            project_rules_path=project_lexical_rules,
            user_rules_path=user_lexical_rules,
        )
    except CrossThreadLexicalRulesError as exc:
        raise LexicalRuleCandidateError(str(exc)) from exc
    active_keys = _active_token_keys(cross_thread_rules)
    active_patterns = _active_patterns(cross_thread_rules)
    provider_id = str(dictionary_payload.get("provider_id") or "unknown")
    skipped_counts: Counter[str] = Counter()
    candidates_by_type: dict[str, list[dict[str, Any]]] = {
        "persona_weak_token": [],
        "distinctive_allow_token": [],
        "generic_scoring_token": [],
    }
    for row in dictionary_payload.get("tokens", []):
        if not isinstance(row, dict):
            continue
        normalized_value = normalize_analysis_text(
            str(row.get("normalized") or row.get("token") or "")
        )
        if not normalized_value:
            skipped_counts["malformed_token"] += 1
            continue
        count = _int_field(row, "count")
        conversation_count = _int_field(row, "conversation_count")
        document_count = _document_count(row)
        if _is_active_token(
            normalized_value,
            active_keys=active_keys,
            active_patterns=active_patterns,
        ):
            skipped_counts["already_active_policy"] += 1
            continue
        topic_summary_counts, _topic_refs = _topic_summary_evidence_for_token(
            normalized_value,
            topic_summary_records=topic_summary_records,
            sample_limit=0,
        )
        persona_candidate, persona_reasons = _is_persona_weak_candidate(
            token=str(row.get("token") or row.get("normalized") or ""),
            normalized_value=normalized_value,
            count=count,
            conversation_count=conversation_count,
            document_count=document_count,
            bundle_count=bundle_counts.get(normalized_value, 0),
            topic_summary_counts=topic_summary_counts,
            topic_summary_records=topic_summary_records,
        )
        if persona_candidate:
            candidates_by_type["persona_weak_token"].append(
                _persona_candidate_for_token(
                    provider_id=provider_id,
                    row=row,
                    bundle_counts=bundle_counts,
                    topic_summary_records=topic_summary_records,
                    sample_limit=sample_limit,
                    reason_codes=persona_reasons,
                )
            )
            skipped_counts["generic_suppressed_by_persona_candidate"] += 1
            continue
        shape_skip_reason = _shape_skip_reason(
            normalized_value,
            count=count,
            conversation_count=conversation_count,
            document_count=document_count,
        )
        distinctive_candidate, distinctive_reasons = _is_distinctive_allow_candidate(
            token=str(row.get("token") or row.get("normalized") or ""),
            normalized_value=normalized_value,
            conversation_count=conversation_count,
            document_count=document_count,
            bundle_count=bundle_counts.get(normalized_value, 0),
            topic_summary_counts=topic_summary_counts,
        )
        if shape_skip_reason in _HARD_SHAPE_SKIP_REASONS and not (
            shape_skip_reason == "shape_identifier_like" and distinctive_candidate
        ):
            skipped_counts[shape_skip_reason] += 1
            continue
        if distinctive_candidate:
            candidates_by_type["distinctive_allow_token"].append(
                _distinctive_allow_candidate_for_token(
                    provider_id=provider_id,
                    row=row,
                    bundle_counts=bundle_counts,
                    topic_summary_records=topic_summary_records,
                    sample_limit=sample_limit,
                    reason_codes=distinctive_reasons,
                )
            )
            skipped_counts["generic_suppressed_by_distinctive_allow_candidate"] += 1
            continue
        if shape_skip_reason:
            skipped_counts[shape_skip_reason] += 1
            continue
        if (
            conversation_count < GENERIC_MIN_CONVERSATION_COUNT
            or document_count < GENERIC_MIN_DOCUMENT_COUNT
        ):
            skipped_counts["below_threshold"] += 1
            continue
        candidates_by_type["generic_scoring_token"].append(
            _candidate_for_token(
                provider_id=provider_id,
                row=row,
                bundle_counts=bundle_counts,
                topic_summary_records=topic_summary_records,
                sample_limit=sample_limit,
            )
        )
    candidates: list[dict[str, Any]] = []
    for candidate_type in (
        "persona_weak_token",
        "distinctive_allow_token",
        "generic_scoring_token",
    ):
        rows = candidates_by_type[candidate_type]
        rows.sort(key=_candidate_sort_key)
        candidates.extend(rows[:max_candidates_per_type])
    candidates.sort(key=_candidate_sort_key)
    diagnostics = _diagnostics(
        dictionary_payload=dictionary_payload,
        candidate_count=len(candidates),
        candidate_type_counts=_candidate_type_counts(candidates),
        skipped_counts=skipped_counts,
        bundles_present=bundles_present,
        topic_summary_diagnostics=topic_summary_diagnostics,
        cross_thread_rules=cross_thread_rules,
        max_candidates_per_type=max_candidates_per_type,
        sample_limit=sample_limit,
    )
    return candidates, diagnostics


def _diagnostics(
    *,
    dictionary_payload: dict[str, Any],
    candidate_count: int,
    candidate_type_counts: dict[str, int],
    skipped_counts: Counter[str],
    bundles_present: bool,
    topic_summary_diagnostics: dict[str, Any],
    cross_thread_rules: CrossThreadLexicalRules,
    max_candidates_per_type: int,
    sample_limit: int,
) -> dict[str, Any]:
    generated_from = ["l3/token-dictionary/observed_tokens.json"]
    if bundles_present:
        generated_from.append("l3/token-dictionary/bundles.json")
    if topic_summary_diagnostics.get("status") == "loaded":
        generated_from.append("thread-*/l3/intra-thread-topics/topic-summaries.jsonl")
    return {
        "artifact_type": LEXICAL_RULE_CANDIDATE_DIAGNOSTICS_ARTIFACT_TYPE,
        "schema_version": LEXICAL_RULE_CANDIDATE_SCHEMA_VERSION,
        "provider_id": str(dictionary_payload.get("provider_id") or "unknown"),
        "generated_from": generated_from,
        "candidate_count": candidate_count,
        "candidate_type_counts": candidate_type_counts,
        "skipped_counts": dict(sorted(skipped_counts.items())),
        "topic_summaries": topic_summary_diagnostics,
        "active_policy": cross_thread_lexical_rules_diagnostics(cross_thread_rules),
        "thresholds": {
            "generic_min_conversation_count": GENERIC_MIN_CONVERSATION_COUNT,
            "generic_min_document_count": GENERIC_MIN_DOCUMENT_COUNT,
            "distinctive_min_conversation_count": DISTINCTIVE_MIN_CONVERSATION_COUNT,
            "distinctive_min_document_count": DISTINCTIVE_MIN_DOCUMENT_COUNT,
            "document_count_mapping": (
                "dictionary document_count when present, otherwise max(conversation_count, topic_count)"
            ),
            "shape_filtering": (
                "URL/path-like, numeric/date-like, hash/ID-like, symbol-heavy, overly short, "
                "and overly long identifier-like tokens are skipped before candidate emission"
            ),
            "max_candidates_per_type": max_candidates_per_type,
            "sample_limit": sample_limit,
        },
        "notes": [
            "Candidates are inactive and require review before use.",
            "observed_tokens.json is treated as observed token index / corpus token statistics only.",
            "dictionary.json remains readable as a legacy alias.",
            "No reviewed lexical rule files are written or modified.",
        ],
    }


def _load_candidate_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise LexicalRuleCandidateError(
                    f"invalid JSON in {path}:{line_number}: {exc.msg}"
                ) from exc
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _load_diagnostics(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LexicalRuleCandidateError(f"invalid JSON in {path}: {exc.msg}") from exc
    return payload if isinstance(payload, dict) else {}


def _candidate_type_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        candidate_type = str(row.get("candidate_type") or "unknown")
        counts[candidate_type] += 1
    return dict(sorted(counts.items()))


def _markdown_inline(value: Any) -> str:
    text = str(value if value is not None else "")
    return text.replace("\n", " ").strip()


def _render_suggested_rule_yaml(normalized_value: str) -> str:
    rendered_value = json.dumps(normalized_value, ensure_ascii=False)
    return "\n".join(
        [
            "```yaml",
            "topic_summary:",
            "  scoring:",
            "    generic_tokens:",
            f"      - {rendered_value}",
            "```",
        ]
    )


def _render_persona_weak_rule_yaml(normalized_value: str) -> str:
    rendered_value = json.dumps(normalized_value, ensure_ascii=False)
    return "\n".join(
        [
            "```yaml",
            "topic_summary:",
            "  scoring:",
            "    persona_weak_tokens:",
            f"      - {rendered_value}",
            "```",
        ]
    )


def _render_distinctive_allow_rule_yaml(normalized_value: str) -> str:
    rendered_value = json.dumps(normalized_value, ensure_ascii=False)
    return "\n".join(
        [
            "```yaml",
            "topic_summary:",
            "  scoring:",
            "    distinctive_allow_tokens:",
            f"      - {rendered_value}",
            "```",
        ]
    )


def _looks_name_or_persona_like(row: dict[str, Any]) -> bool:
    value = str(row.get("value") or "")
    normalized_value = str(row.get("normalized_value") or "")
    if not value and not normalized_value:
        return False
    if re.fullmatch(r"[A-Z][A-Za-z_-]{2,}", value):
        return True
    return bool(re.search(r"(?:さん|ちゃん|くん|君)$", value or normalized_value))


def _render_review_markdown(
    rows: list[dict[str, Any]],
    diagnostics: dict[str, Any],
) -> str:
    candidate_type_counts = diagnostics.get("candidate_type_counts")
    if not isinstance(candidate_type_counts, dict):
        candidate_type_counts = _candidate_type_counts(rows)

    lines = [
        "# Lexical Rule Candidates Review",
        "",
        "## Summary",
        "",
        f"- total candidates: {len(rows)}",
        "- candidate types:",
    ]
    if candidate_type_counts:
        for candidate_type, count in sorted(candidate_type_counts.items()):
            lines.append(f"  - {candidate_type}: {count}")
    else:
        lines.append("  - none: 0")

    generic_rows = [
        row
        for row in rows
        if row.get("candidate_type") == "generic_scoring_token"
    ]
    generic_rows.sort(key=_review_sort_key)
    persona_rows = [
        row
        for row in rows
        if row.get("candidate_type") == "persona_weak_token"
    ]
    persona_rows.sort(key=_review_sort_key)
    distinctive_rows = [
        row
        for row in rows
        if row.get("candidate_type") == "distinctive_allow_token"
    ]
    distinctive_rows.sort(key=_review_sort_key)

    lines.extend(
        [
            "",
            "## persona_weak_token",
            "",
            "> Note: These candidates are review-only suggestions for terms "
            "whose standalone persona/name/address overlap should remain weak. "
            "They are not activated automatically.",
            "",
        ]
    )
    if persona_rows:
        for row in persona_rows:
            _append_candidate_review_section(
                lines,
                row,
                suggested_rule_renderer=_render_persona_weak_rule_yaml,
                include_name_like_warning=False,
            )
    else:
        lines.append("_No candidates._")
        lines.append("")

    lines.extend(
        [
            "",
            "## distinctive_allow_token",
            "",
            "> Note: These candidates are review-only suggestions for "
            "high-value domain/project/topic tokens that should be protected "
            "from generic weakening. They are not activated automatically.",
            "",
        ]
    )
    if distinctive_rows:
        for row in distinctive_rows:
            _append_candidate_review_section(
                lines,
                row,
                suggested_rule_renderer=_render_distinctive_allow_rule_yaml,
                include_name_like_warning=False,
            )
    else:
        lines.append("_No candidates._")
        lines.append("")

    lines.extend(
        [
            "",
            "## generic_scoring_token",
            "",
            "> Note: Do not promote personal names, character names, "
            "assistant/persona names, or project-specific identity terms as "
            "generic scoring tokens. Prefer reviewed project/user "
            "`topic_summary.scoring.persona_weak_tokens` for terms whose "
            "standalone overlap should remain weak.",
            "",
        ]
    )
    if not generic_rows:
        lines.append("_No candidates._")
        lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    for row in generic_rows:
        _append_candidate_review_section(
            lines,
            row,
            suggested_rule_renderer=_render_suggested_rule_yaml,
            include_name_like_warning=True,
        )

    return "\n".join(lines).rstrip() + "\n"


def _append_candidate_review_section(
    lines: list[str],
    row: dict[str, Any],
    *,
    suggested_rule_renderer,
    include_name_like_warning: bool,
) -> None:
    normalized_value = _markdown_inline(row.get("normalized_value"))
    evidence = row.get("evidence", {})
    if not isinstance(evidence, dict):
        evidence = {}
    reason_codes = evidence.get("reason_codes", [])
    if not isinstance(reason_codes, list):
        reason_codes = []
    sample_refs = row.get("sample_refs", [])
    if not isinstance(sample_refs, list):
        sample_refs = []
    looks_name_like = include_name_like_warning and _looks_name_or_persona_like(row)

    lines.extend(
        [
            f"### {normalized_value}",
            "",
            f"- score: {evidence.get('score', '')}",
            "- score_components:",
            "  - spread_score: "
            f"{(evidence.get('score_components') or {}).get('spread_score', '')}",
            "  - frequency_score: "
            f"{(evidence.get('score_components') or {}).get('frequency_score', '')}",
            "  - topic_summary_score: "
            f"{(evidence.get('score_components') or {}).get('topic_summary_score', '')}",
            "  - shape_score: "
            f"{(evidence.get('score_components') or {}).get('shape_score', '')}",
            "- counts:",
            f"  - conversation_count: {evidence.get('conversation_count', 0)}",
            f"  - document_count: {evidence.get('document_count', 0)}",
            f"  - topic_count: {evidence.get('topic_count', 0)}",
            "- evidence:",
            "  - topic_summary_total_count: "
            f"{evidence.get('topic_summary_total_count', 0)}",
            f"  - bundle_count: {evidence.get('bundle_count', 0)}",
            "- reason_codes:",
        ]
    )
    if looks_name_like:
        lines.extend(
            [
                "- review_note: This value looks like a possible "
                "name/persona/project identity term. Consider "
                "`topic_summary.scoring.persona_weak_tokens` instead of "
                "`generic_tokens`.",
            ]
        )
    if reason_codes:
        for reason_code in reason_codes:
            lines.append(f"  - {_markdown_inline(reason_code)}")
    else:
        lines.append("  - none")

    lines.append("- sample_refs:")
    if sample_refs:
        for ref in sample_refs:
            if not isinstance(ref, dict):
                continue
            field = _markdown_inline(ref.get("field") or "unknown")
            excerpt = _markdown_inline(ref.get("excerpt"))
            conversation_id = _markdown_inline(ref.get("conversation_id"))
            segment_id = _markdown_inline(ref.get("segment_id"))
            location = conversation_id
            if segment_id:
                location = f"{location} / {segment_id}" if location else segment_id
            detail = f"{field}"
            if location:
                detail = f"{detail} ({location})"
            if excerpt:
                detail = f"{detail}: {excerpt}"
            lines.append(f"  - {detail}")
    else:
        lines.append("  - none")

    lines.extend(
        [
            "- suggested_rule:",
            suggested_rule_renderer(normalized_value),
        ]
    )
    if looks_name_like:
        lines.extend(
            [
                "- alternative_rule:",
                _render_persona_weak_rule_yaml(normalized_value),
            ]
        )
    lines.append("")


def _write_lexical_rule_candidate_review(
    *,
    candidates_path: Path,
    diagnostics_path: Path,
    review_path: Path,
) -> Path:
    rows = _load_candidate_jsonl(candidates_path)
    diagnostics = _load_diagnostics(diagnostics_path)
    rendered = _render_review_markdown(rows, diagnostics)
    tmp_path = review_path.with_suffix(".tmp")
    tmp_path.write_text(rendered, encoding="utf-8")
    tmp_path.replace(review_path)
    return review_path


def write_lexical_rule_candidate_artifacts(
    input_root: Path,
    *,
    project_lexical_rules: Path | str | None = None,
    user_lexical_rules: Path | str | None = None,
    max_candidates_per_type: int = DEFAULT_LEXICAL_RULE_CANDIDATE_MAX_PER_TYPE,
    sample_limit: int = DEFAULT_LEXICAL_RULE_CANDIDATE_SAMPLE_LIMIT,
    overwrite: bool = False,
) -> dict[str, Any]:
    provider_root = input_root.expanduser()
    if not provider_root.exists() or not provider_root.is_dir():
        raise LexicalRuleCandidateError(f"provider root not found: {provider_root}")
    output_dir = lexical_rule_candidates_dir(provider_root)
    candidates_path = lexical_rule_candidates_path(provider_root)
    diagnostics_path = lexical_rule_candidate_diagnostics_path(provider_root)
    review_path = lexical_rule_candidate_review_path(provider_root)
    existing = [
        path
        for path in (candidates_path, diagnostics_path, review_path)
        if path.exists()
    ]
    if existing and not overwrite:
        raise LexicalRuleCandidateError(
            "lexical-rule candidate artifacts already exist; use --overwrite to replace them"
        )
    rows, diagnostics = build_lexical_rule_candidate_rows(
        provider_root,
        project_lexical_rules=project_lexical_rules,
        user_lexical_rules=user_lexical_rules,
        max_candidates_per_type=max_candidates_per_type,
        sample_limit=sample_limit,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    tmp_candidates_path = candidates_path.with_suffix(".tmp")
    with tmp_candidates_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp_candidates_path.replace(candidates_path)
    write_json_artifact(diagnostics_path, diagnostics)
    _write_lexical_rule_candidate_review(
        candidates_path=candidates_path,
        diagnostics_path=diagnostics_path,
        review_path=review_path,
    )
    return {
        "candidate_count": len(rows),
        "candidates_path": candidates_path,
        "diagnostics_path": diagnostics_path,
        "review_path": review_path,
    }
