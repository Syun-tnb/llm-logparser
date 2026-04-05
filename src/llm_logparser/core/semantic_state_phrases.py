from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .analyzer_common import normalize_analysis_text

DEFAULT_STATE_LOCALE = "en-US"
_RESOURCE_DIR = (
    Path(__file__).resolve().parent.parent / "resources" / "semantic_state"
)
_PHRASE_KEYS = (
    "closure_user",
    "completion_assistant",
    "decision",
    "question",
    "user_revision",
    "uncertainty",
    "next_step",
)


@dataclass(frozen=True)
class SemanticStatePhrases:
    locale: str
    closure_user: tuple[str, ...]
    completion_assistant: tuple[str, ...]
    decision: tuple[str, ...]
    question: tuple[str, ...]
    user_revision: tuple[str, ...]
    uncertainty: tuple[str, ...]
    next_step: tuple[str, ...]


def _normalize_locale(value: str | None) -> str:
    if not value:
        return DEFAULT_STATE_LOCALE
    return value.replace("_", "-")


def _available_locales() -> tuple[str, ...]:
    if not _RESOURCE_DIR.exists():
        return (DEFAULT_STATE_LOCALE,)
    return tuple(sorted(path.stem for path in _RESOURCE_DIR.glob("*.yaml")))


def _locale_aliases() -> dict[str, str]:
    aliases: dict[str, str] = {}
    by_language: dict[str, set[str]] = {}
    for locale in _available_locales():
        language = locale.split("-")[0]
        by_language.setdefault(language, set()).add(locale)
    for language, candidates in by_language.items():
        if len(candidates) == 1:
            aliases[language] = next(iter(candidates))
    return aliases


def resolve_supported_state_locale(
    requested: str | None,
    available_locales: set[str],
) -> str:
    if requested is None:
        return DEFAULT_STATE_LOCALE

    normalized = _normalize_locale(requested)
    if normalized in available_locales:
        return normalized

    language = normalized.split("-")[0]
    matches = sorted(
        locale
        for locale in available_locales
        if locale.split("-")[0] == language
    )
    if len(matches) == 1:
        return matches[0]
    return DEFAULT_STATE_LOCALE


def _read_phrase_payload(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"semantic state phrase file must contain a mapping: {path}")
    return payload


def _normalize_phrase_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        folded = normalize_analysis_text(item)
        if not folded or folded in seen:
            continue
        seen.add(folded)
        normalized.append(folded)
    return tuple(normalized)


@lru_cache(maxsize=None)
def load_semantic_state_phrases(
    state_locale: str | None = None,
) -> SemanticStatePhrases:
    resolved_locale = resolve_supported_state_locale(
        state_locale,
        set(_available_locales()),
    )
    path = _RESOURCE_DIR / f"{resolved_locale}.yaml"
    if not path.exists():
        if resolved_locale != DEFAULT_STATE_LOCALE:
            return load_semantic_state_phrases(DEFAULT_STATE_LOCALE)
        raise RuntimeError(f"semantic state phrase file not found: {path}")

    payload = _read_phrase_payload(path)
    phrase_map = {
        key: _normalize_phrase_list(payload.get(key, []))
        for key in _PHRASE_KEYS
    }
    return SemanticStatePhrases(
        locale=resolved_locale,
        closure_user=phrase_map["closure_user"],
        completion_assistant=phrase_map["completion_assistant"],
        decision=phrase_map["decision"],
        question=phrase_map["question"],
        user_revision=phrase_map["user_revision"],
        uncertainty=phrase_map["uncertainty"],
        next_step=phrase_map["next_step"],
    )
