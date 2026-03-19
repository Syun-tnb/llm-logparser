# src/llm_logparser/i18n.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml

DEFAULT_LOCALE = "en-US"
FALLBACK_LOCALE = "en-US"
LOCALE_ALIASES = {
    "en": "en-US",
    "ja": "ja-JP",
}

_EN_MESSAGES = {
    # --- CLI / general ---
    "cli.description": "CLI interface for LLM Log Parser (MVP)",
    "cli.option.lang.help": "UI language/locale for CLI output (use --locale, alias --lang; e.g. en, ja)",
    "cli.option.log_level.help": "Log level override (DEBUG|INFO|WARNING|ERROR|CRITICAL); overrides environment variable",
    "cli.parse.help": "Parse provider export JSON into normalized JSONL threads",
    "cli.export.help": "Export parsed logs to Markdown",
    "cli.viewer.help": "(placeholder) Run lightweight HTML viewer",
    "cli.config.help": "Inspect and validate runtime configuration",

    "cli.parse.opt.provider.help": "Provider ID (e.g., openai)",
    "cli.parse.opt.input.help": "Input JSON/JSONL path",
    "cli.parse.opt.outdir.help": "Output root directory (provider subdir will be auto-created)",
    "cli.parse.opt.dry_run.help": "Run parse without writing any files (stats/log only).",
    "cli.parse.opt.fail_fast.help": "Stop parsing on first error instead of continuing.",

    # --- log / info ---
    "cli.parse.provider": "Provider: {provider}",
    "cli.parse.input": "Input file: {path}",
    "cli.parse.outdir": "Output directory: {path}",
    "cli.parse.done": "Parsed {threads} threads ({messages} messages)",

    # --- errors ---
    "cli.error.path": "Path error: {detail}",
    "cli.error.permission": "Permission error: {detail}",
    "cli.error.unexpected": "Unexpected error: {detail}",
}

_JA_MESSAGES = {
    "cli.description": "LLM Log Parser 用のCLIインターフェース（MVP）",
    "cli.option.lang.help": "CLI 表示の言語/ロケール (--locale、旧 --lang。例: en, ja)",
    "cli.option.log_level.help": "ログレベルを指定 (DEBUG|INFO|WARNING|ERROR|CRITICAL)。環境変数 LLM_LOGPARSER_LOGLEVEL を上書き",
    "cli.parse.help": "プロバイダのエクスポートJSONを正規化JSONLスレッドに変換する",
    "cli.export.help": "parsedログをMarkdownに出力する",
    "cli.viewer.help": "（プレースホルダ）簡易HTMLビューアを起動する",
    "cli.config.help": "ランタイム設定を確認・検証する",

    "cli.parse.provider": "プロバイダ: {provider}",
    "cli.parse.input": "入力ファイル: {path}",
    "cli.parse.outdir": "出力ディレクトリ: {path}",
    "cli.parse.done": "✅ {threads} スレッド（{messages} メッセージ）をパースしました",

    "cli.error.path": "パスエラー: {detail}",
    "cli.error.permission": "アクセス権限エラー: {detail}",
    "cli.error.unexpected": "予期しないエラー: {detail}",
}

_MESSAGES: Dict[str, Dict[str, str]] = {
    "en": _EN_MESSAGES,
    "en-US": _EN_MESSAGES,
    "ja": _JA_MESSAGES,
    "ja-JP": _JA_MESSAGES,
}

_TRANSLATIONS = _MESSAGES

_CURRENT_LOCALE = DEFAULT_LOCALE
_RESOURCE_DIR = Path(__file__).resolve().parent.parent / "i18n"


def _load_locale_resources() -> Dict[str, dict[str, Any]]:
    resources: Dict[str, dict[str, Any]] = {}
    if not _RESOURCE_DIR.exists():
        return resources

    for path in sorted(_RESOURCE_DIR.glob("*.yaml")):
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict):
            resources[path.stem] = payload
    return resources


_RESOURCE_MESSAGES = _load_locale_resources()


def resolve_locale(cli_locale: str | None = None) -> str:
    """
    ロケール決定ロジック（MVP版）

    優先度:
      1. CLI引数 --locale
      2. 環境変数 LLP_LOCALE
      3. DEFAULT_LOCALE ("en")
    """
    if cli_locale:
        base = _normalize_locale(cli_locale)
    else:
        env = os.getenv("LLP_LOCALE")
        base = _normalize_locale(env) if env else DEFAULT_LOCALE

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


def _normalize_locale(value: str | None) -> str:
    if not value:
        return DEFAULT_LOCALE
    return value.replace("_", "-")


def t(key: str, locale: str, **params: Any) -> str:
    """
    翻訳関数。
    - locale -> key で文字列を引き、
    - 見つからなければ fallback locale / key を返す。
    """
    # まず指定ロケール
    catalog = _MESSAGES.get(locale) or _MESSAGES.get(FALLBACK_LOCALE, {})
    template = catalog.get(key)
    if template is None and locale != FALLBACK_LOCALE:
        # fallback locale に再チャレンジ
        catalog = _MESSAGES.get(FALLBACK_LOCALE, {})
        template = catalog.get(key)

    if template is None:
        # 最後の手段: key をそのまま返す（デバッグしやすい）
        template = key

    if params:
        try:
            return template.format(**params)
        except Exception:
            # formatエラー時も落ちないようにしておく
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


def set_locale(cli_locale: str | None = None) -> str:
    global _CURRENT_LOCALE
    _CURRENT_LOCALE = resolve_locale(cli_locale)
    return _CURRENT_LOCALE


def _(key: str, **params: Any) -> str:
    return t(key, _CURRENT_LOCALE, **params)
