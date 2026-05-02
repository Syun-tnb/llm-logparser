from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from llm_logparser.core.analyzer_common import normalize_analysis_text

DEFAULT_CROSS_THREAD_LEXICAL_LOCALE = "en-US"
_RESOURCE_DIR = Path(__file__).resolve().parent / "cross_thread"
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


@dataclass(frozen=True)
class CrossThreadLexicalRules:
    locale: str
    residue: CrossThreadResidueRules
    task: CrossThreadTaskRules
    reflective: CrossThreadReflectiveRules
    broad_overlap: CrossThreadBroadOverlapRules
    topic_summary_admission: CrossThreadTopicSummaryAdmissionRules

    @property
    def topic_summary_admission_generic_anchor_tokens(self) -> tuple[str, ...]:
        return self.topic_summary_admission.generic_anchor_tokens


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
    payloads = [
        _read_payload(_RESOURCE_DIR / f"{resolved_locale}.yaml")
        for resolved_locale in locales
    ]
    prompt_exact_markers = _merged_list(payloads, "residue", "prompt_exact_markers")
    system_tool_exact_markers = _merged_list(payloads, "residue", "system_tool_exact_markers")
    exact_markers = _normalized_unique_sequence(
        list(prompt_exact_markers) + list(system_tool_exact_markers)
    )
    return CrossThreadLexicalRules(
        locale=locales[0],
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
            )
        ),
    )
