# src/llm_logparser/i18n.py
from __future__ import annotations

import os
from typing import Any, Dict

DEFAULT_LOCALE = "en"
FALLBACK_LOCALE = "en"

# 将来はここを YAML / JSON ロードに差し替える
_MESSAGES: Dict[str, Dict[str, str]] = {
    "en": {
        # --- CLI / general ---
        "cli.description": "CLI interface for LLM Log Parser (MVP)",
        "cli.option.lang.help": "UI language/locale for CLI output (use --locale, alias --lang; e.g. en, ja)",
        "cli.option.log_level.help": "Log level override (DEBUG|INFO|WARNING|ERROR|CRITICAL); overrides environment variable",
        "cli.parse.help": "Parse provider export JSON into normalized JSONL threads",
        "cli.export.help": "(placeholder) Export parsed logs to Markdown/HTML",
        "cli.viewer.help": "(placeholder) Run lightweight HTML viewer",
        "cli.config.help": "(placeholder) Manage runtime configuration",

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
    },
    "ja": {
        "cli.description": "LLM Log Parser 用のCLIインターフェース（MVP）",
        "cli.option.lang.help": "CLI 表示の言語/ロケール (--locale、旧 --lang。例: en, ja)",
        "cli.option.log_level.help": "ログレベルを指定 (DEBUG|INFO|WARNING|ERROR|CRITICAL)。環境変数 LLM_LOGPARSER_LOGLEVEL を上書き",
        "cli.parse.help": "プロバイダのエクスポートJSONを正規化JSONLスレッドに変換する",
        "cli.export.help": "（プレースホルダ）parsedログをMarkdown/HTMLに出力する",
        "cli.viewer.help": "（プレースホルダ）簡易HTMLビューアを起動する",
        "cli.config.help": "（プレースホルダ）ランタイム設定を管理する",

        "cli.parse.provider": "プロバイダ: {provider}",
        "cli.parse.input": "入力ファイル: {path}",
        "cli.parse.outdir": "出力ディレクトリ: {path}",
        "cli.parse.done": "✅ {threads} スレッド（{messages} メッセージ）をパースしました",

        "cli.error.path": "パスエラー: {detail}",
        "cli.error.permission": "アクセス権限エラー: {detail}",
        "cli.error.unexpected": "予期しないエラー: {detail}",
    },
}

_TRANSLATIONS = _MESSAGES

_CURRENT_LOCALE = DEFAULT_LOCALE


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

    if base in _MESSAGES:
        return base
    # "en-US" みたいな場合は "en" にフォールバック
    lang = base.split("-")[0]
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


def set_locale(cli_locale: str | None = None) -> str:
    global _CURRENT_LOCALE
    _CURRENT_LOCALE = resolve_locale(cli_locale)
    return _CURRENT_LOCALE


def _(key: str, **params: Any) -> str:
    return t(key, _CURRENT_LOCALE, **params)
