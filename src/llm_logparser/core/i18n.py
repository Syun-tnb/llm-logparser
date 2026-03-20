from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Iterable

import yaml

DEFAULT_LOCALE = "en-US"
FALLBACK_LOCALE = "en-US"
LOCALE_ENV_VAR = "LLP_LOCALE"
LOCALE_ALIASES: dict[str, str] = {}

_CURRENT_LOCALE = DEFAULT_LOCALE
_RESOURCE_DIR = Path(__file__).resolve().parent.parent / "i18n"


def _load_locale_payload(path: Path) -> dict[str, Any]:
    # Locale files are best-effort extensions. They are expected to be YAML
    # mappings and may contain `messages:` and/or `analysis:`, but partial
    # files must remain safe because fallback behavior is non-blocking.
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _load_locale_payloads(resource_dir: Path | None = None) -> Dict[str, dict[str, Any]]:
    base_dir = resource_dir or _RESOURCE_DIR
    if not base_dir.exists():
        return {}

    payloads: Dict[str, dict[str, Any]] = {}
    for path in sorted(base_dir.glob("*.yaml")):
        payloads[path.stem] = _load_locale_payload(path)
    return payloads


def _normalize_locale(value: str | None) -> str:
    if not value:
        return DEFAULT_LOCALE
    return value.replace("_", "-")


def _build_locale_aliases(locales: Iterable[str]) -> dict[str, str]:
    """Derive short aliases from discovered locale filenames.

    A short language alias is only created when one locale file unambiguously
    owns that language prefix. If multiple locale files share the same language,
    callers must use the full locale tag.
    """
    by_language: dict[str, set[str]] = {}
    for locale in locales:
        normalized = _normalize_locale(locale)
        language = normalized.split("-")[0]
        by_language.setdefault(language, set()).add(normalized)

    aliases: dict[str, str] = {}
    for language, candidates in by_language.items():
        if language in candidates:
            aliases[language] = language
            continue
        if len(candidates) == 1:
            aliases[language] = next(iter(candidates))
    return aliases


def _extract_scalar_messages(payload: dict[str, Any]) -> dict[str, str]:
    messages = payload.get("messages")
    if not isinstance(messages, dict):
        return {}
    return {
        key: value
        for key, value in messages.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def _with_aliases(catalogs: Dict[str, Any], aliases: dict[str, str]) -> Dict[str, Any]:
    aliased = dict(catalogs)
    for alias, canonical in aliases.items():
        value = aliased.get(canonical)
        if value is not None:
            aliased[alias] = value
    return aliased


_LOCALE_PAYLOADS = _load_locale_payloads()
LOCALE_ALIASES = _build_locale_aliases(_LOCALE_PAYLOADS.keys())
_RESOURCE_MESSAGES = _with_aliases(_LOCALE_PAYLOADS, LOCALE_ALIASES)
_MESSAGES: Dict[str, Dict[str, str]] = _with_aliases(
    {
        locale: _extract_scalar_messages(payload)
        for locale, payload in _LOCALE_PAYLOADS.items()
    },
    LOCALE_ALIASES,
)
_MESSAGES.setdefault(FALLBACK_LOCALE, {})

_TRANSLATIONS = _MESSAGES


def _resolve_supported_locale(value: str | None) -> str:
    base = _normalize_locale(value)

    alias = LOCALE_ALIASES.get(base)
    if alias and alias in _MESSAGES:
        return alias
    if base in _MESSAGES:
        return base

    lang = base.split("-")[0]
    alias = LOCALE_ALIASES.get(lang)
    if alias and alias in _MESSAGES:
        return alias
    if lang in _MESSAGES:
        return lang

    return FALLBACK_LOCALE


def resolve_locale(
    cli_locale: str | None = None,
    *,
    config_locale: str | None = None,
    env_locale: str | None = None,
) -> str:
    """
    ロケール決定ロジック（MVP版）

    優先度:
      1. CLI引数 --locale
      2. 環境変数 LLP_LOCALE
      3. config profile locale
      4. DEFAULT_LOCALE ("en-US")
    """
    if cli_locale:
        return _resolve_supported_locale(cli_locale)

    env = env_locale if env_locale is not None else os.getenv(LOCALE_ENV_VAR)
    if env:
        return _resolve_supported_locale(env)

    if config_locale:
        return _resolve_supported_locale(config_locale)

    return FALLBACK_LOCALE
def t(key: str, locale: str, **params: Any) -> str:
    """
    翻訳関数。
    - locale -> key で文字列を引き、
    - 見つからなければ fallback locale / key を返す。
    """
    catalog = _MESSAGES.get(locale) or _MESSAGES.get(FALLBACK_LOCALE, {})
    template = catalog.get(key)
    if template is None and locale != FALLBACK_LOCALE:
        catalog = _MESSAGES.get(FALLBACK_LOCALE, {})
        template = catalog.get(key)

    if template is None:
        template = key

    if params:
        try:
            return template.format(**params)
        except Exception:
            return template

    return template


def _resource_locale_candidates(locale: str | None) -> list[str]:
    normalized = _normalize_locale(locale)
    candidates: list[str] = []
    for candidate in (
        normalized,
        LOCALE_ALIASES.get(normalized),
        normalized.split("-")[0] if normalized else None,
        LOCALE_ALIASES.get(normalized.split("-")[0]) if normalized else None,
        FALLBACK_LOCALE,
        FALLBACK_LOCALE.split("-")[0],
    ):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _nested_lookup(payload: dict[str, Any], key: str) -> Any:
    current: Any = payload
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def get_resource(key: str, locale: str | None = None, default: Any = None) -> Any:
    active_locale = locale or _CURRENT_LOCALE
    for candidate in _resource_locale_candidates(active_locale):
        payload = _RESOURCE_MESSAGES.get(candidate)
        if not isinstance(payload, dict):
            continue
        value = _nested_lookup(payload, key)
        if value is not None:
            return value
    return default


def get_resource_list(key: str, locale: str | None = None) -> list[str]:
    value = get_resource(key, locale=locale, default=[])
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def set_locale(
    cli_locale: str | None = None,
    *,
    config_locale: str | None = None,
    env_locale: str | None = None,
) -> str:
    global _CURRENT_LOCALE
    _CURRENT_LOCALE = resolve_locale(
        cli_locale,
        config_locale=config_locale,
        env_locale=env_locale,
    )
    return _CURRENT_LOCALE


def _(key: str, **params: Any) -> str:
    return t(key, _CURRENT_LOCALE, **params)
