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
    locale: str | None
    path: str
    sha1: str
    owner_scope: str | None = None
    schema_version: str | None = None


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


class CrossThreadLexicalRulesError(RuntimeError):
    pass


def _resource_layer(locale: str, path: Path) -> CrossThreadLexicalResourceLayer:
    return CrossThreadLexicalResourceLayer(
        kind="built_in_resource",
        locale=locale,
        path=str(path.relative_to(_PACKAGE_DIR)),
        sha1=hashlib.sha1(path.read_bytes()).hexdigest(),
    )


def _safe_external_path(path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        return str(resolved.relative_to(Path.cwd().resolve()))
    except ValueError:
        pass
    try:
        return str(resolved.relative_to(Path.home().resolve()))
    except ValueError:
        pass
    return path.name


def _reviewed_resource_layer(
    *,
    kind: str,
    path: Path,
    payload: dict[str, Any],
) -> CrossThreadLexicalResourceLayer:
    return CrossThreadLexicalResourceLayer(
        kind=kind,
        locale=None,
        path=_safe_external_path(path),
        sha1=hashlib.sha1(path.expanduser().read_bytes()).hexdigest(),
        owner_scope=str(payload.get("owner_scope", "")),
        schema_version=str(payload.get("schema_version", "")),
    )


def _read_reviewed_payload(
    path: Path | str,
    *,
    expected_owner_scope: str,
) -> dict[str, Any]:
    resolved = Path(path).expanduser()
    if not resolved.exists():
        raise CrossThreadLexicalRulesError(
            f"reviewed {expected_owner_scope} lexical rules file not found: {path}"
        )
    if not resolved.is_file():
        raise CrossThreadLexicalRulesError(
            f"reviewed {expected_owner_scope} lexical rules path is not a file: {path}"
        )
    payload = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise CrossThreadLexicalRulesError(
            f"reviewed {expected_owner_scope} lexical rules must contain a mapping: {path}"
        )
    allowed_top_level = {"schema_version", "owner_scope", "rules"}
    unknown_top_level = sorted(set(payload) - allowed_top_level)
    if unknown_top_level:
        raise CrossThreadLexicalRulesError(
            "reviewed "
            f"{expected_owner_scope} lexical rules contain unsupported top-level "
            f"field(s): {', '.join(unknown_top_level)}"
        )
    schema_version = payload.get("schema_version")
    if schema_version != "0.1":
        raise CrossThreadLexicalRulesError(
            f"reviewed {expected_owner_scope} lexical rules schema_version must be 0.1"
        )
    owner_scope = payload.get("owner_scope")
    if owner_scope != expected_owner_scope:
        raise CrossThreadLexicalRulesError(
            "reviewed "
            f"{expected_owner_scope} lexical rules owner_scope must be "
            f"{expected_owner_scope!r}"
        )
    rules = payload.get("rules", {})
    if not isinstance(rules, dict):
        raise CrossThreadLexicalRulesError(
            f"reviewed {expected_owner_scope} lexical rules 'rules' must be a mapping"
        )
    return payload


def _reviewed_rules_payload(payload: dict[str, Any]) -> dict[str, Any]:
    rules = payload.get("rules", {})
    if not isinstance(rules, dict):
        return {}
    if "cross_thread" in rules:
        return rules
    return {"cross_thread": rules}


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


def load_cross_thread_lexical_rules(
    locale: str | None = None,
    project_rules_path: Path | str | None = None,
    user_rules_path: Path | str | None = None,
) -> CrossThreadLexicalRules:
    if project_rules_path is None and user_rules_path is None:
        return _load_builtin_cross_thread_lexical_rules(locale)
    return _load_cross_thread_lexical_rules_uncached(
        locale,
        project_rules_path=project_rules_path,
        user_rules_path=user_rules_path,
    )


@lru_cache(maxsize=None)
def _load_builtin_cross_thread_lexical_rules(
    locale: str | None = None,
) -> CrossThreadLexicalRules:
    return _load_cross_thread_lexical_rules_uncached(locale)


def _load_cross_thread_lexical_rules_uncached(
    locale: str | None = None,
    *,
    project_rules_path: Path | str | None = None,
    user_rules_path: Path | str | None = None,
) -> CrossThreadLexicalRules:
    locales = _locale_chain(locale)
    resource_paths = tuple(_RESOURCE_DIR / f"{resolved_locale}.yaml" for resolved_locale in locales)
    payloads = [_read_payload(path) for path in resource_paths]
    layers: list[CrossThreadLexicalResourceLayer] = list(
        _resource_layer(resolved_locale, path)
        for resolved_locale, path in zip(locales, resource_paths)
    )
    if project_rules_path is not None:
        project_payload = _read_reviewed_payload(
            project_rules_path,
            expected_owner_scope="project",
        )
        payloads.append(_reviewed_rules_payload(project_payload))
        layers.append(
            _reviewed_resource_layer(
                kind="reviewed_project_rules",
                path=Path(project_rules_path),
                payload=project_payload,
            )
        )
    if user_rules_path is not None:
        user_payload = _read_reviewed_payload(
            user_rules_path,
            expected_owner_scope="user",
        )
        payloads.append(_reviewed_rules_payload(user_payload))
        layers.append(
            _reviewed_resource_layer(
                kind="reviewed_user_rules",
                path=Path(user_rules_path),
                payload=user_payload,
            )
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
            layers=tuple(layers),
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
                "path": layer.path,
                "sha1": layer.sha1,
                **({"locale": layer.locale} if layer.locale is not None else {}),
                **(
                    {"owner_scope": layer.owner_scope}
                    if layer.owner_scope is not None
                    else {}
                ),
                **(
                    {"schema_version": layer.schema_version}
                    if layer.schema_version is not None
                    else {}
                ),
            }
            for layer in rules.provenance.layers
        ],
        "project_rules": _reviewed_rules_status(
            rules,
            kind="reviewed_project_rules",
        ),
        "user_rules": _reviewed_rules_status(
            rules,
            kind="reviewed_user_rules",
        ),
        "category_counts": category_counts,
    }


def _reviewed_rules_status(
    rules: CrossThreadLexicalRules,
    *,
    kind: str,
) -> dict[str, str]:
    layer = next(
        (item for item in rules.provenance.layers if item.kind == kind),
        None,
    )
    if layer is None:
        return {"status": "not_provided"}
    return {
        "status": "loaded",
        "path": layer.path,
        "sha1": layer.sha1,
        "owner_scope": layer.owner_scope or "",
        "schema_version": layer.schema_version or "",
    }
