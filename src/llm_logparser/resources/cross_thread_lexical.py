from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from llm_logparser.core.analyzer_common import normalize_analysis_text

DEFAULT_CROSS_THREAD_LEXICAL_LOCALE = "en-US"
_RESOURCE_DIR = Path(__file__).resolve().parent / "cross_thread"
_PACKAGE_DIR = Path(__file__).resolve().parents[1]
_LANGUAGE_DEFAULT_LOCALES = {
    "en": "en-US",
    "ja": "ja-JP",
}


@dataclass(frozen=True)
class CrossThreadResidueCompositeRules:
    infrastructure_terms: frozenset[str]
    control_terms: frozenset[str]


@dataclass(frozen=True)
class CrossThreadResidueRules:
    prompt_exact_markers: tuple[str, ...]
    system_tool_exact_markers: tuple[str, ...]
    exact_markers: tuple[str, ...]
    composite: CrossThreadResidueCompositeRules


@dataclass(frozen=True)
class CrossThreadTaskRules:
    verbs: frozenset[str]
    nouns: frozenset[str]
    phrases: tuple[str, ...]


@dataclass(frozen=True)
class CrossThreadReflectiveRules:
    markers: frozenset[str]


@dataclass(frozen=True)
class CrossThreadBroadOverlapRules:
    markers: tuple[str, ...]


@dataclass(frozen=True)
class CrossThreadTopicSummaryAdmissionRules:
    generic_anchor_tokens: tuple[str, ...]
    generic_anchor_patterns: tuple[str, ...]


@dataclass(frozen=True)
class CrossThreadTopicSummaryScoringRules:
    generic_tokens: tuple[str, ...]
    generic_patterns: tuple[str, ...]
    short_specific_tokens: tuple[str, ...]
    distinctive_allow_tokens: tuple[str, ...]
    distinctive_block_tokens: tuple[str, ...]
    weak_distinctive_tokens: tuple[str, ...]
    persona_weak_tokens: tuple[str, ...]
    tool_residue_patterns: tuple[str, ...]
    citation_residue_patterns: tuple[str, ...]
    ritual_title_phrases: tuple[str, ...]


@dataclass(frozen=True)
class CrossThreadLexicalResourceLayer:
    kind: str
    locale: str
    path: str
    sha1: str


@dataclass(frozen=True)
class CrossThreadLexicalProvenance:
    rule_family: str
    schema_version: str
    requested_locale: str
    resolved_locale: str
    locale_chain: tuple[str, ...]
    layers: tuple[CrossThreadLexicalResourceLayer, ...]


@dataclass(frozen=True)
class CrossThreadLexicalRules:
    locale: str
    provenance: CrossThreadLexicalProvenance
    residue: CrossThreadResidueRules
    task: CrossThreadTaskRules
    reflective: CrossThreadReflectiveRules
    broad_overlap: CrossThreadBroadOverlapRules
    topic_summary_admission: CrossThreadTopicSummaryAdmissionRules
    topic_summary_scoring: CrossThreadTopicSummaryScoringRules

    @property
    def topic_summary_admission_generic_anchor_tokens(self) -> tuple[str, ...]:
        return self.topic_summary_admission.generic_anchor_tokens

    @property
    def topic_summary_admission_generic_anchor_patterns(self) -> tuple[str, ...]:
        return self.topic_summary_admission.generic_anchor_patterns

    @property
    def topic_summary_scoring_generic_tokens(self) -> tuple[str, ...]:
        return self.topic_summary_scoring.generic_tokens

    @property
    def topic_summary_scoring_generic_patterns(self) -> tuple[str, ...]:
        return self.topic_summary_scoring.generic_patterns

    @property
    def topic_summary_scoring_short_specific_tokens(self) -> tuple[str, ...]:
        return self.topic_summary_scoring.short_specific_tokens

    @property
    def topic_summary_scoring_distinctive_allow_tokens(self) -> tuple[str, ...]:
        return self.topic_summary_scoring.distinctive_allow_tokens

    @property
    def topic_summary_scoring_distinctive_block_tokens(self) -> tuple[str, ...]:
        return self.topic_summary_scoring.distinctive_block_tokens

    @property
    def topic_summary_scoring_weak_distinctive_tokens(self) -> tuple[str, ...]:
        return self.topic_summary_scoring.weak_distinctive_tokens

    @property
    def topic_summary_scoring_persona_weak_tokens(self) -> tuple[str, ...]:
        return self.topic_summary_scoring.persona_weak_tokens

    @property
    def topic_summary_scoring_tool_residue_patterns(self) -> tuple[str, ...]:
        return self.topic_summary_scoring.tool_residue_patterns

    @property
    def topic_summary_scoring_citation_residue_patterns(self) -> tuple[str, ...]:
        return self.topic_summary_scoring.citation_residue_patterns

    @property
    def topic_summary_scoring_ritual_title_phrases(self) -> tuple[str, ...]:
        return self.topic_summary_scoring.ritual_title_phrases


def _normalize_locale(value: str | None) -> str:
    if not value:
        return DEFAULT_CROSS_THREAD_LEXICAL_LOCALE
    return value.replace("_", "-")


def _available_locales() -> tuple[str, ...]:
    if not _RESOURCE_DIR.exists():
        return (DEFAULT_CROSS_THREAD_LEXICAL_LOCALE,)
    return tuple(sorted(path.stem for path in _RESOURCE_DIR.glob("*.yaml")))


def _locale_chain(requested: str | None) -> tuple[str, ...]:
    normalized = _normalize_locale(requested)
    available = set(_available_locales())
    chain: list[str] = []

    def _push(locale: str | None) -> None:
        if locale and locale in available and locale not in chain:
            chain.append(locale)

    _push(normalized)
    language = normalized.split("-")[0]
    _push(_LANGUAGE_DEFAULT_LOCALES.get(language))
    _push(DEFAULT_CROSS_THREAD_LEXICAL_LOCALE)
    if not chain:
        chain.append(DEFAULT_CROSS_THREAD_LEXICAL_LOCALE)
    return tuple(chain)


def _read_payload(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"cross-thread lexical resource must contain a mapping: {path}")
    return payload


def _resource_layer(locale: str, path: Path) -> CrossThreadLexicalResourceLayer:
    return CrossThreadLexicalResourceLayer(
        kind="built_in_resource",
        locale=locale,
        path=str(path.relative_to(_PACKAGE_DIR)),
        sha1=hashlib.sha1(path.read_bytes()).hexdigest(),
    )


def _normalized_unique_sequence(values: list[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        folded = normalize_analysis_text(value)
        if not folded or folded in seen:
            continue
        seen.add(folded)
        normalized.append(folded)
    return tuple(normalized)


def _merged_list(payloads: list[dict[str, Any]], *keys: str) -> tuple[str, ...]:
    merged: list[str] = []
    for payload in payloads:
        current: Any = payload
        for key in keys:
            if not isinstance(current, dict):
                current = []
                break
            current = current.get(key, [])
        if not isinstance(current, list):
            continue
        merged.extend(str(item) for item in current if isinstance(item, str) and item.strip())
    return _normalized_unique_sequence(merged)


@lru_cache(maxsize=None)
def load_cross_thread_lexical_rules(locale: str | None = None) -> CrossThreadLexicalRules:
    locales = _locale_chain(locale)
    resource_paths = tuple(_RESOURCE_DIR / f"{resolved_locale}.yaml" for resolved_locale in locales)
    payloads = [_read_payload(path) for path in resource_paths]
    layers = tuple(
        _resource_layer(resolved_locale, path)
        for resolved_locale, path in zip(locales, resource_paths)
    )
    prompt_exact_markers = _merged_list(payloads, "residue", "prompt_exact_markers")
    system_tool_exact_markers = _merged_list(payloads, "residue", "system_tool_exact_markers")
    exact_markers = _normalized_unique_sequence(
        list(prompt_exact_markers) + list(system_tool_exact_markers)
    )
    return CrossThreadLexicalRules(
        locale=locales[0],
        provenance=CrossThreadLexicalProvenance(
            rule_family="cross_thread",
            schema_version="0.1",
            requested_locale=_normalize_locale(locale),
            resolved_locale=locales[0],
            locale_chain=locales,
            layers=layers,
        ),
        residue=CrossThreadResidueRules(
            prompt_exact_markers=prompt_exact_markers,
            system_tool_exact_markers=system_tool_exact_markers,
            exact_markers=exact_markers,
            composite=CrossThreadResidueCompositeRules(
                infrastructure_terms=frozenset(
                    _merged_list(payloads, "residue", "composite", "infrastructure_terms")
                ),
                control_terms=frozenset(
                    _merged_list(payloads, "residue", "composite", "control_terms")
                ),
            ),
        ),
        task=CrossThreadTaskRules(
            verbs=frozenset(_merged_list(payloads, "task", "verbs")),
            nouns=frozenset(_merged_list(payloads, "task", "nouns")),
            phrases=_merged_list(payloads, "task", "phrases"),
        ),
        reflective=CrossThreadReflectiveRules(
            markers=frozenset(_merged_list(payloads, "reflective", "markers"))
        ),
        broad_overlap=CrossThreadBroadOverlapRules(
            markers=_merged_list(payloads, "broad_overlap", "markers")
        ),
        topic_summary_admission=CrossThreadTopicSummaryAdmissionRules(
            generic_anchor_tokens=_merged_list(
                payloads,
                "cross_thread",
                "topic_summary_admission",
                "generic_anchor_tokens",
            ),
            generic_anchor_patterns=_merged_list(
                payloads,
                "cross_thread",
                "topic_summary_admission",
                "generic_anchor_patterns",
            ),
        ),
        topic_summary_scoring=CrossThreadTopicSummaryScoringRules(
            generic_tokens=_merged_list(
                payloads,
                "cross_thread",
                "topic_summary",
                "scoring",
                "generic_tokens",
            ),
            generic_patterns=_merged_list(
                payloads,
                "cross_thread",
                "topic_summary",
                "scoring",
                "generic_patterns",
            ),
            short_specific_tokens=_merged_list(
                payloads,
                "cross_thread",
                "topic_summary",
                "scoring",
                "short_specific_tokens",
            ),
            distinctive_allow_tokens=_merged_list(
                payloads,
                "cross_thread",
                "topic_summary",
                "scoring",
                "distinctive_allow_tokens",
            ),
            distinctive_block_tokens=_merged_list(
                payloads,
                "cross_thread",
                "topic_summary",
                "scoring",
                "distinctive_block_tokens",
            ),
            weak_distinctive_tokens=_merged_list(
                payloads,
                "cross_thread",
                "topic_summary",
                "scoring",
                "weak_distinctive_tokens",
            ),
            persona_weak_tokens=_merged_list(
                payloads,
                "cross_thread",
                "topic_summary",
                "scoring",
                "persona_weak_tokens",
            ),
            tool_residue_patterns=_merged_list(
                payloads,
                "cross_thread",
                "topic_summary",
                "scoring",
                "tool_residue_patterns",
            ),
            citation_residue_patterns=_merged_list(
                payloads,
                "cross_thread",
                "topic_summary",
                "scoring",
                "citation_residue_patterns",
            ),
            ritual_title_phrases=_merged_list(
                payloads,
                "cross_thread",
                "topic_summary",
                "scoring",
                "ritual_title_phrases",
            ),
        ),
    )


def cross_thread_lexical_rules_diagnostics(
    rules: CrossThreadLexicalRules,
) -> dict[str, Any]:
    """Return summary-safe diagnostics for resolved cross-thread lexical rules.

    This intentionally reports provenance and category counts only. Full lexical
    policy export is deferred until reviewed project/user rule layers exist.
    """

    category_counts = {
        "topic_summary_admission.generic_anchor_tokens": len(
            rules.topic_summary_admission.generic_anchor_tokens
        ),
        "topic_summary_admission.generic_anchor_patterns": len(
            rules.topic_summary_admission.generic_anchor_patterns
        ),
        "topic_summary_scoring.generic_tokens": len(
            rules.topic_summary_scoring.generic_tokens
        ),
        "topic_summary_scoring.generic_patterns": len(
            rules.topic_summary_scoring.generic_patterns
        ),
        "topic_summary_scoring.short_specific_tokens": len(
            rules.topic_summary_scoring.short_specific_tokens
        ),
        "topic_summary_scoring.distinctive_allow_tokens": len(
            rules.topic_summary_scoring.distinctive_allow_tokens
        ),
        "topic_summary_scoring.distinctive_block_tokens": len(
            rules.topic_summary_scoring.distinctive_block_tokens
        ),
        "topic_summary_scoring.weak_distinctive_tokens": len(
            rules.topic_summary_scoring.weak_distinctive_tokens
        ),
        "topic_summary_scoring.persona_weak_tokens": len(
            rules.topic_summary_scoring.persona_weak_tokens
        ),
        "topic_summary_scoring.tool_residue_patterns": len(
            rules.topic_summary_scoring.tool_residue_patterns
        ),
        "topic_summary_scoring.citation_residue_patterns": len(
            rules.topic_summary_scoring.citation_residue_patterns
        ),
        "topic_summary_scoring.ritual_title_phrases": len(
            rules.topic_summary_scoring.ritual_title_phrases
        ),
    }
    return {
        "rule_family": rules.provenance.rule_family,
        "schema_version": rules.provenance.schema_version,
        "requested_locale": rules.provenance.requested_locale,
        "resolved_locale": rules.provenance.resolved_locale,
        "locale_chain": list(rules.provenance.locale_chain),
        "layers": [
            {
                "kind": layer.kind,
                "locale": layer.locale,
                "path": layer.path,
                "sha1": layer.sha1,
            }
            for layer in rules.provenance.layers
        ],
        "project_rules": {"status": "not_implemented"},
        "user_rules": {"status": "not_implemented"},
        "category_counts": category_counts,
    }
