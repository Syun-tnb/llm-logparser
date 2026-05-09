from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_RESOURCE_DIR = Path(__file__).resolve().parent / "lexical_rule_candidates"
_PACKAGE_DIR = Path(__file__).resolve().parents[1]
_DEFAULT_RESOURCE = _RESOURCE_DIR / "default.yaml"


class LexicalRuleCandidateConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class LexicalRuleCandidateResourceLayer:
    kind: str
    path: str
    sha1: str


@dataclass(frozen=True)
class LexicalRuleCandidateConfigProvenance:
    rule_family: str
    schema_version: str
    layers: tuple[LexicalRuleCandidateResourceLayer, ...]


@dataclass(frozen=True)
class LexicalRuleCandidateTypeConfig:
    enabled: bool
    method: str
    suggested_rule_path: str
    min_conversation_count: int
    min_document_count: int
    precedence: int
    strong_reason_codes: frozenset[str]
    thresholds: dict[str, int]
    supporting_evidence: dict[str, Any]
    domain_terms: frozenset[str]


@dataclass(frozen=True)
class LexicalRuleCandidateOutputConfig:
    max_candidates_per_type: int
    sample_limit: int
    topic_summary_excerpt_max_chars: int


@dataclass(frozen=True)
class LexicalRuleCandidateShapeConfig:
    hard_skip_reasons: frozenset[str]
    distinctive_bypass: dict[str, bool]
    token_symbol_pattern: str
    url_like_pattern: str
    path_like_pattern: str
    date_like_pattern: str
    hash_or_id_like_pattern: str
    latin: dict[str, Any]
    cjk: dict[str, Any]
    file_extension_like: frozenset[str]


@dataclass(frozen=True)
class LexicalRuleCandidateScoringConfig:
    normalizers: dict[str, float]
    weights: dict[str, float]
    spread_weights: dict[str, float]
    shape_score_values: dict[str, float]
    rounding_digits: int


@dataclass(frozen=True)
class LexicalRuleCandidateConfig:
    provenance: LexicalRuleCandidateConfigProvenance
    output: LexicalRuleCandidateOutputConfig
    candidate_types: dict[str, LexicalRuleCandidateTypeConfig]
    shape_filtering: LexicalRuleCandidateShapeConfig
    scoring: LexicalRuleCandidateScoringConfig
    locale_signals: dict[str, dict[str, tuple[str, ...]]]
    review_rendering: dict[str, Any]

    def candidate_type(self, name: str) -> LexicalRuleCandidateTypeConfig:
        try:
            return self.candidate_types[name]
        except KeyError as exc:
            raise LexicalRuleCandidateConfigError(
                f"lexical-rule candidate config missing candidate type: {name}"
            ) from exc


def _read_payload(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise LexicalRuleCandidateConfigError(
            f"invalid lexical-rule candidate config YAML: {path}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise LexicalRuleCandidateConfigError(
            f"lexical-rule candidate config must contain a mapping: {path}"
        )
    return payload


def _resource_layer(path: Path) -> LexicalRuleCandidateResourceLayer:
    try:
        display_path = str(path.relative_to(_PACKAGE_DIR))
    except ValueError:
        display_path = path.name
    return LexicalRuleCandidateResourceLayer(
        kind="built_in_resource",
        path=display_path,
        sha1=hashlib.sha1(path.read_bytes()).hexdigest(),
    )


def _mapping(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise LexicalRuleCandidateConfigError(
            f"lexical-rule candidate config field must be a mapping: {key}"
        )
    return value


def _string_list(value: Any, *, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise LexicalRuleCandidateConfigError(
            f"lexical-rule candidate config field must be a list of strings: {field}"
        )
    return tuple(value)


def _int_value(mapping: dict[str, Any], key: str) -> int:
    value = mapping.get(key)
    if not isinstance(value, int):
        raise LexicalRuleCandidateConfigError(
            f"lexical-rule candidate config field must be an integer: {key}"
        )
    return value


def _float_mapping(payload: dict[str, Any], key: str) -> dict[str, float]:
    raw = _mapping(payload, key)
    values: dict[str, float] = {}
    for item_key, value in raw.items():
        if not isinstance(value, int | float):
            raise LexicalRuleCandidateConfigError(
                f"lexical-rule candidate config field must be numeric: {key}.{item_key}"
            )
        values[str(item_key)] = float(value)
    return values


def _compile_pattern(pattern: str, *, field: str) -> None:
    try:
        re.compile(pattern)
    except re.error as exc:
        raise LexicalRuleCandidateConfigError(
            f"invalid lexical-rule candidate config regex {field}: {exc}"
        ) from exc


def _candidate_type_config(
    name: str,
    payload: dict[str, Any],
) -> LexicalRuleCandidateTypeConfig:
    if not isinstance(payload.get("enabled"), bool):
        raise LexicalRuleCandidateConfigError(
            f"lexical-rule candidate type enabled must be boolean: {name}"
        )
    method = payload.get("method")
    suggested_rule_path = payload.get("suggested_rule_path")
    if not isinstance(method, str) or not method:
        raise LexicalRuleCandidateConfigError(
            f"lexical-rule candidate type method must be a string: {name}"
        )
    if not isinstance(suggested_rule_path, str) or not suggested_rule_path:
        raise LexicalRuleCandidateConfigError(
            f"lexical-rule candidate type suggested_rule_path must be a string: {name}"
        )
    thresholds = payload.get("thresholds", {})
    if thresholds is None:
        thresholds = {}
    if not isinstance(thresholds, dict):
        raise LexicalRuleCandidateConfigError(
            f"lexical-rule candidate type thresholds must be a mapping: {name}"
        )
    normalized_thresholds: dict[str, int] = {}
    for key, value in thresholds.items():
        if not isinstance(value, int):
            raise LexicalRuleCandidateConfigError(
                f"lexical-rule candidate type threshold must be an integer: {name}.{key}"
            )
        normalized_thresholds[str(key)] = value
    supporting_evidence = payload.get("supporting_evidence", {})
    if supporting_evidence is None:
        supporting_evidence = {}
    if not isinstance(supporting_evidence, dict):
        raise LexicalRuleCandidateConfigError(
            f"lexical-rule candidate type supporting_evidence must be a mapping: {name}"
        )
    return LexicalRuleCandidateTypeConfig(
        enabled=payload["enabled"],
        method=method,
        suggested_rule_path=suggested_rule_path,
        min_conversation_count=_int_value(payload, "min_conversation_count"),
        min_document_count=_int_value(payload, "min_document_count"),
        precedence=_int_value(payload, "precedence"),
        strong_reason_codes=frozenset(
            _string_list(payload.get("strong_reason_codes", []), field=f"{name}.strong_reason_codes")
        ),
        thresholds=normalized_thresholds,
        supporting_evidence=dict(supporting_evidence),
        domain_terms=frozenset(
            _string_list(payload.get("domain_terms", []), field=f"{name}.domain_terms")
        ),
    )


def _shape_config(payload: dict[str, Any]) -> LexicalRuleCandidateShapeConfig:
    patterns = _mapping(payload, "patterns")
    required_patterns = ("url_like", "path_like", "date_like", "hash_or_id_like")
    for key in required_patterns:
        pattern = patterns.get(key)
        if not isinstance(pattern, str):
            raise LexicalRuleCandidateConfigError(
                f"lexical-rule candidate config pattern must be a string: {key}"
            )
        _compile_pattern(pattern, field=f"shape_filtering.patterns.{key}")
    token_symbol_pattern = payload.get("token_symbol_pattern")
    if not isinstance(token_symbol_pattern, str):
        raise LexicalRuleCandidateConfigError(
            "lexical-rule candidate config token_symbol_pattern must be a string"
        )
    _compile_pattern(token_symbol_pattern, field="shape_filtering.token_symbol_pattern")
    latin = _mapping(payload, "latin")
    cjk = _mapping(payload, "cjk")
    _mapping(cjk, "one_char")
    _mapping(cjk, "two_char")
    distinctive_bypass = payload.get("distinctive_bypass", {})
    if not isinstance(distinctive_bypass, dict):
        raise LexicalRuleCandidateConfigError(
            "lexical-rule candidate config distinctive_bypass must be a mapping"
        )
    return LexicalRuleCandidateShapeConfig(
        hard_skip_reasons=frozenset(
            _string_list(payload.get("hard_skip_reasons", []), field="hard_skip_reasons")
        ),
        distinctive_bypass={
            str(key): bool(value) for key, value in distinctive_bypass.items()
        },
        token_symbol_pattern=token_symbol_pattern,
        url_like_pattern=str(patterns["url_like"]),
        path_like_pattern=str(patterns["path_like"]),
        date_like_pattern=str(patterns["date_like"]),
        hash_or_id_like_pattern=str(patterns["hash_or_id_like"]),
        latin=dict(latin),
        cjk=dict(cjk),
        file_extension_like=frozenset(
            _string_list(payload.get("file_extension_like", []), field="file_extension_like")
        ),
    )


def _locale_signals(payload: dict[str, Any]) -> dict[str, dict[str, tuple[str, ...]]]:
    signals: dict[str, dict[str, tuple[str, ...]]] = {}
    for locale, locale_payload in payload.items():
        if not isinstance(locale_payload, dict):
            raise LexicalRuleCandidateConfigError(
                f"lexical-rule candidate locale signal must be a mapping: {locale}"
            )
        signals[str(locale)] = {
            str(key): _string_list(value, field=f"locale_signals.{locale}.{key}")
            for key, value in locale_payload.items()
        }
    return signals


def _build_config(path: Path, payload: dict[str, Any]) -> LexicalRuleCandidateConfig:
    schema_version = payload.get("schema_version")
    rule_family = payload.get("rule_family")
    if schema_version != "0.1":
        raise LexicalRuleCandidateConfigError(
            f"unsupported lexical-rule candidate config schema_version: {schema_version}"
        )
    if rule_family != "lexical_rule_candidates":
        raise LexicalRuleCandidateConfigError(
            f"unsupported lexical-rule candidate config rule_family: {rule_family}"
        )
    output_payload = _mapping(payload, "output")
    candidate_payload = _mapping(payload, "candidate_types")
    candidate_types = {
        str(name): _candidate_type_config(str(name), value)
        for name, value in candidate_payload.items()
        if isinstance(value, dict)
    }
    expected_types = {
        "generic_scoring_token",
        "persona_weak_token",
        "distinctive_allow_token",
    }
    missing = sorted(expected_types - set(candidate_types))
    if missing:
        raise LexicalRuleCandidateConfigError(
            "lexical-rule candidate config missing candidate type(s): "
            + ", ".join(missing)
        )
    scoring_payload = _mapping(payload, "scoring")
    provenance = LexicalRuleCandidateConfigProvenance(
        rule_family=rule_family,
        schema_version=schema_version,
        layers=(_resource_layer(path),),
    )
    return LexicalRuleCandidateConfig(
        provenance=provenance,
        output=LexicalRuleCandidateOutputConfig(
            max_candidates_per_type=_int_value(output_payload, "max_candidates_per_type"),
            sample_limit=_int_value(output_payload, "sample_limit"),
            topic_summary_excerpt_max_chars=_int_value(
                output_payload,
                "topic_summary_excerpt_max_chars",
            ),
        ),
        candidate_types=candidate_types,
        shape_filtering=_shape_config(_mapping(payload, "shape_filtering")),
        scoring=LexicalRuleCandidateScoringConfig(
            normalizers=_float_mapping(scoring_payload, "normalizers"),
            weights=_float_mapping(scoring_payload, "weights"),
            spread_weights=_float_mapping(scoring_payload, "spread_weights"),
            shape_score_values=_float_mapping(scoring_payload, "shape_score_values"),
            rounding_digits=_int_value(scoring_payload, "rounding_digits"),
        ),
        locale_signals=_locale_signals(_mapping(payload, "locale_signals")),
        review_rendering=dict(_mapping(payload, "review_rendering")),
    )


@lru_cache(maxsize=1)
def load_lexical_rule_candidate_config() -> LexicalRuleCandidateConfig:
    return _build_config(_DEFAULT_RESOURCE, _read_payload(_DEFAULT_RESOURCE))


def load_lexical_rule_candidate_config_from_path(
    path: Path | str,
) -> LexicalRuleCandidateConfig:
    resource_path = Path(path)
    return _build_config(resource_path, _read_payload(resource_path))


def lexical_rule_candidate_config_diagnostics(
    config: LexicalRuleCandidateConfig,
) -> dict[str, Any]:
    candidate_type_names = sorted(config.candidate_types)
    enabled_candidate_type_names = sorted(
        name for name, item in config.candidate_types.items() if item.enabled
    )
    thresholds = {
        name: {
            "min_conversation_count": item.min_conversation_count,
            "min_document_count": item.min_document_count,
            "precedence": item.precedence,
            "thresholds": dict(sorted(item.thresholds.items())),
        }
        for name, item in sorted(config.candidate_types.items())
    }
    locale_signal_counts = {
        locale: {key: len(values) for key, values in sorted(signals.items())}
        for locale, signals in sorted(config.locale_signals.items())
    }
    return {
        "rule_family": config.provenance.rule_family,
        "schema_version": config.provenance.schema_version,
        "layers": [
            {
                "kind": layer.kind,
                "path": layer.path,
                "sha1": layer.sha1,
            }
            for layer in config.provenance.layers
        ],
        "candidate_type_names": candidate_type_names,
        "enabled_candidate_type_names": enabled_candidate_type_names,
        "thresholds": thresholds,
        "scoring": {
            "normalizers": dict(sorted(config.scoring.normalizers.items())),
            "weights": dict(sorted(config.scoring.weights.items())),
            "spread_weights": dict(sorted(config.scoring.spread_weights.items())),
            "shape_score_values": dict(
                sorted(config.scoring.shape_score_values.items())
            ),
            "rounding_digits": config.scoring.rounding_digits,
        },
        "shape_filtering": {
            "hard_skip_reason_count": len(config.shape_filtering.hard_skip_reasons),
            "distinctive_bypass": dict(
                sorted(config.shape_filtering.distinctive_bypass.items())
            ),
            "file_extension_like_count": len(
                config.shape_filtering.file_extension_like
            ),
        },
        "domain_term_counts": {
            name: len(item.domain_terms)
            for name, item in sorted(config.candidate_types.items())
        },
        "locale_signal_counts": locale_signal_counts,
        "output": {
            "max_candidates_per_type": config.output.max_candidates_per_type,
            "sample_limit": config.output.sample_limit,
            "topic_summary_excerpt_max_chars": (
                config.output.topic_summary_excerpt_max_chars
            ),
        },
    }
