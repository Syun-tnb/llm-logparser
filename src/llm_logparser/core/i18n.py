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
    "cli.option.config.help": "Path to config.yaml",
    "cli.option.profile.help": "Profile name to use",
    "cli.option.non_interactive.help": "Disable interactive prompts and fail when required values are missing",
    "cli.option.timezone.help": "IANA timezone (e.g., Asia/Tokyo)",
    "cli.option.formatting.help": "Apply minimal Markdown formatting (none|light).",
    "cli.option.split.help": "size=<4M|512KiB|...> or count=<N> or auto (auto = size=4M & count=1500)",
    "cli.option.tiny_tail_threshold.help": "Threshold for tail merge (message count)",
    "cli.parse.help": "Parse provider export JSON into normalized JSONL threads",
    "cli.export.help": "Export parsed logs to Markdown",
    "cli.extract.help": "Extract one conversation as Gemini-compatible JSON",
    "cli.analyze.help": "Analyze canonical parsed JSONL threads",
    "cli.chain.help": "Parse raw export and export all threads to Markdown in one shot",
    "cli.viewer.help": "(placeholder) Run lightweight HTML viewer",
    "cli.config.help": "Inspect and validate runtime configuration",
    "cli.config.path.help": "Show the resolved config file path",
    "cli.config.show.help": "Print the normalized config or selected profile",
    "cli.config.validate.help": "Validate the current config and exit",

    "cli.parse.opt.provider.help": "Provider ID (e.g., openai)",
    "cli.parse.opt.input.help": "Input JSON/JSONL path",
    "cli.parse.opt.outdir.help": "Output root directory (provider subdir will be auto-created)",
    "cli.parse.opt.dry_run.help": "Run parse without writing any files (stats/log only).",
    "cli.parse.opt.fail_fast.help": "Stop parsing on first error instead of continuing.",
    "cli.parse.opt.validate_schema.help": "Validate normalized messages against message.schema.json",
    "cli.export.opt.input.help": "Path to thread parsed.jsonl",
    "cli.export.opt.out.help": "Output Markdown path",
    "cli.extract.opt.conversation_id.help": "Conversation ID to extract",
    "cli.extract.opt.outdir.help": "Output root directory",
    "cli.analyze.opt.input.help": "Path to a parsed.jsonl file or a directory containing parsed.jsonl files",
    "cli.analyze.opt.json.help": "Emit JSON instead of human-readable text",
    "cli.analyze.opt.out.help": "Write the rendered result to a file",
    "cli.analyze.stats.help": "Compute deterministic conversation statistics from parsed JSONL",
    "cli.analyze.stats.opt.per_thread.help": "Include per-thread rows in human-readable output",
    "cli.analyze.stats.opt.top.help": "Limit the number of per-thread rows after sorting",
    "cli.analyze.stats.opt.sort.help": "Sort field for per-thread rows",
    "cli.analyze.stats.opt.include_role_breakdown.help": "Include breakdown of roles other than user and assistant",
    "cli.analyze.timeline.help": "Aggregate timestamped message activity over time",
    "cli.analyze.timeline.opt.bucket.help": "Bucket size for timeline aggregation",
    "cli.analyze.tokens.help": "Build deterministic token_stats.json sidecars from canonical parsed JSONL",
    "cli.analyze.tokens.opt.model.help": "Optional model name for tokenizer resolution (primarily for OpenAI)",
    "cli.analyze.tokens.opt.encoding.help": "Explicit tiktoken encoding name that overrides provider/model resolution",
    "cli.analyze.metrics.help": "Build deterministic metrics.json sidecars from parsed.jsonl plus token_stats.json",
    "cli.analyze.sqlite_build.help": "Build an optional SQLite accelerator from canonical thread artifacts",
    "cli.analyze.sqlite_build.opt.input.help": "Root directory containing per-provider artifact directories",
    "cli.analyze.sqlite_build.opt.provider.help": "Provider ID to index (for example: openai)",
    "cli.analyze.sqlite_build.opt.overwrite.help": "Delete an existing analysis.db before rebuilding",
    "cli.chain.opt.outdir.help": "Root directory for artifacts (parse+export). Parsed JSONL will be under outdir/output/<provider>/...",
    "cli.chain.opt.export_outdir.help": "Optional root directory to place all exported Markdown files. If omitted, Markdown is written next to each thread directory.",
    "cli.chain.opt.parsed_root.help": "Optional root directory that already contains parsed threads (…/thread-*/parsed.jsonl). If specified, parse phase is skipped.",
    "cli.chain.opt.fail_fast.help": "Stop chain processing on first export error. Default is to continue.",
    "cli.chain.opt.validate_schema.help": "Validate normalized messages during the parse phase",

    # --- log / info ---
    "cli.parse.provider": "Provider: {provider}",
    "cli.parse.input": "Input file: {path}",
    "cli.parse.outdir": "Output directory: {path}",
    "cli.parse.done": "Parsed {threads} threads ({messages} messages)",

    # --- errors ---
    "cli.error.path": "Path error: {detail}",
    "cli.error.permission": "Permission error: {detail}",
    "cli.error.unexpected": "Unexpected error: {detail}",
    "error.path": "Path error: {detail}",
    "error.permission": "Permission error: {detail}",
    "error.unexpected": "Unexpected error: {detail}",
    "error.path_not_found": "Path does not exist: {path}",
    "error.path_expected_file": "File path expected: {path}",
    "error.path_expected_dir": "Directory path expected: {path}",
    "error.missing_required": "Missing required options for '{command}':",
    "error.missing_required_hint.provider": "--provider / config: provider",
    "error.missing_required_hint.input": "--input / config: input.path, input.paths, input.parsed(export)",
    "error.missing_required_hint.conversation_id": "--conversation-id / config: extract.conversation_id",
    "error.missing_required_hint.generic": "CLI option or config value",
    "runtime.split_invalid": "invalid --split: {raw}",
    "runtime.timezone_unknown": "Unknown timezone '{timezone_name}', fallback to UTC",
    "runtime.prompt.enter_value": "Please enter a value.",
    "runtime.prompt.file_not_found": "File not found: {path}",
    "runtime.prompt.skip_hint": " or press Enter to skip",
    "runtime.prompt.select_option": "Select option (1-{count}){skip_hint}: ",
    "runtime.prompt.invalid_selection": "Invalid selection.",
    "runtime.config.hint_passed_via_config": "passed via --config",
    "runtime.config.no_file": "No config file found.",
    "runtime.config.valid": "Config structure is valid: {path} (profile: {selected})",
    "runtime.config.unknown_command": "Unknown config command: {command}",
    "runtime.config.yaml_required": "PyYAML is required for config support. Install dependency 'PyYAML'.",
    "runtime.config.load_failed": "Failed to load config YAML '{path}': {detail}",
    "runtime.config.mapping_required": "Config YAML must be a mapping at top level: {path}",
    "runtime.config.file_not_found": "Config file not found: {path}{hint}",
    "runtime.config.path_must_be_file": "Config path must be a file: {path}",
    "runtime.config.env_missing": "{env_var} points to a missing file: {path}. Fix the path or unset the environment variable.",
    "runtime.config.env_must_be_file": "{env_var} must point to a file: {path}",
    "runtime.config.profile_not_found": "Profile not found in config: {name}",
    "runtime.config.invalid": "Invalid config: {message}",
    "runtime.config.unsupported_schema_version": "Unsupported config schema_version '{normalized}'. This build supports major version {major}.",
    "runtime.config.deprecated_profile_key": "Deprecated config key profiles.{profile_name}.{legacy_key} is still supported for schema_version 1; use {replacement} instead. Removal is planned for a future schema_version 2 cleanup.",
    "runtime.config.unknown_sanitize_keys": "Unknown sanitize config key(s) under {context}: {keys}",
    "runtime.config.must_be_mapping": "{context} must be a mapping",
    "runtime.config.must_be_string": "{context} must be a string",
    "runtime.config.must_be_boolean": "{context} must be a boolean",
    "runtime.config.must_be_integer": "{context} must be an integer",
    "runtime.config.must_be_number": "{context} must be a number",
    "runtime.config.must_be_list_of_strings": "{context} must be a list of strings",
    "runtime.config.invalid_regex": "{context}[{index}] is not a valid regex: {detail}",
    "runtime.config.schema_version_type": "schema_version must be a string or number",
    "runtime.config.schema_version_whole_major": "schema_version must use a whole-number major version",
    "runtime.config.schema_version_empty": "schema_version must not be empty",
    "runtime.config.schema_version_format": "schema_version must look like '1' or '1.0'",
    "runtime.config.sanitize_scope_invalid": "{context}.scope must be one of: content_parts, all_strings",
    "runtime.config.profile_names_non_empty": "profile names must be non-empty strings",
    "runtime.parse.provider": "Provider: {provider}",
    "runtime.parse.input": "Input file: {path}",
    "runtime.parse.output_dir": "Output directory: {path}",
    "runtime.parse.dry_run": "Dry run   : {dry_run}",
    "runtime.parse.fail_fast": "Fail fast : {fail_fast}",
    "runtime.parse.schema_validation_enabled": "Schema validation: enabled ({schema_path})",
    "runtime.parse.done": "Parsed {threads} threads ({messages} messages)",
    "runtime.export.input_jsonl": "Input JSONL: {path}",
    "runtime.export.output_md": "Output MD  : {path}",
    "runtime.export.output_md_split": "Output MD  : {path}/thread-<cid>*.md",
    "runtime.export.timezone": "Timezone   : {timezone}",
    "runtime.export.formatting": "Formatting : {formatting}",
    "runtime.export.preview_only": "Preview only (no files written)",
    "runtime.export.exported_one": "Exported 1 Markdown",
    "runtime.export.exported_many": "Exported {count} Markdown",
    "runtime.extract.provider": "Provider: {provider}",
    "runtime.extract.input": "Input file: {path}",
    "runtime.extract.conversation_id": "Conversation ID: {conversation_id}",
    "runtime.extract.output_root": "Output root: {path}",
    "runtime.extract.dry_run": "Dry run: {dry_run}",
    "runtime.extract.done": "Extracted to {path}",
    "runtime.extract.dry_run_done": "Dry-run complete (planned output: {path})",
    "runtime.analyze.metrics_written": "metrics.json written for {threads} thread(s)",
    "runtime.analyze.tokens_written": "token_stats.json written for {threads} thread(s)",
    "runtime.analyze.top_non_negative": "--top must be >= 0",
    "runtime.analyze.sqlite_built": "analysis.db built: {db_path} (threads={threads} messages={messages} windows={windows})",
    "runtime.chain.provider": "[chain] Provider : {provider}",
    "runtime.chain.input": "[chain] Input    : {path}",
    "runtime.chain.root": "[chain] Root     : {path}",
    "runtime.chain.timezone": "[chain] TZ       : {timezone}",
    "runtime.chain.formatting": "[chain] Formatting: {formatting}",
    "runtime.chain.dry_run": "[chain] Dry run  : {dry_run}",
    "runtime.chain.fail_fast": "[chain] Fail fast: {fail_fast}",
    "runtime.chain.using_parsed_root": "[chain] Using existing parsed root: {path}",
    "runtime.chain.parsing_into": "[chain] Parsing into: {path}",
    "runtime.chain.schema_validation_enabled": "[chain] Schema validation: enabled ({schema_path})",
    "runtime.chain.parsed": "[chain] Parsed {threads} threads ({messages} messages)",
    "runtime.chain.parsed_root_missing": "[chain] Parsed root directory not found: {path}\n  - You may need to check your directory layout.\n  - Or specify --parsed-root explicitly.",
    "runtime.chain.no_parsed_jsonl": "[chain] No parsed.jsonl found under {path}",
    "runtime.chain.found_threads": "[chain] Found {count} thread(s)",
    "runtime.chain.export_outdir": "[chain] Export outdir: {path}",
    "runtime.chain.exporting": "[chain] Exporting: {parsed} -> {out}",
    "runtime.chain.export_failed": "[chain] Failed exporting {parsed}: {detail}",
    "runtime.chain.preview_only": "[chain] Preview only (no files written)",
    "runtime.chain.done": "[chain] Exported {markdown_files} Markdown file(s) from {threads} thread(s) (failed: {failed})",
    "runtime.profile.select": "Select profile:",
    "runtime.profile.select_input": "Select input file:",
    "runtime.profile.multiple_input_paths": "Multiple input paths found in config. Resolve ambiguity with --input or keep a single input.path/input.paths entry.",
    "runtime.prompt.provider": "Provider (e.g., openai):",
    "runtime.prompt.input_file_path": "Input file path:",
    "runtime.prompt.conversation_id": "Conversation ID:",
    "runtime.prompt.analyze_provider_root": "Input provider-root directory path:",
    "runtime.prompt.analyze_input": "Input parsed.jsonl or directory path:",
    "runtime.prompt.provider_id": "Provider ID (for example: openai):",
    "runtime.analyze.missing_input": "Missing required options for 'analyze {command}':\n  - input: --input",
    "runtime.analyze.missing_provider": "Missing required options for 'analyze sqlite-build':\n  - provider: --provider",
    "runtime.viewer.todo": "[TODO] Viewer not implemented yet.",
    "runtime.parser.skip_invalid_json_line": "skip invalid JSON line ({index}): {detail}",
    "runtime.parser.skip_invalid_element": "skip invalid element ({index})",
    "runtime.parser.start_parse": "Starting parse for provider={provider} (dry-run={dry_run}, fail-fast={fail_fast})",
    "runtime.parser.adapter_error": "adapter error: {detail}",
    "runtime.parser.processed_messages": "processed {count} messages...",
    "runtime.parser.skip_thread_unchanged": "SKIP thread {conversation_id} (unchanged)",
    "runtime.parser.schema_validation_failed": "schema validation failed for {conversation_id}/{message_id}: {detail}",
    "runtime.parser.manifest_saved": "manifest saved: {path}",
    "runtime.parser.summary": "SUMMARY: threads={threads} messages={messages} errors={errors} skipped={skipped}",
    "runtime.parser.start_extract": "Starting extract for provider={provider}, conversation_id={conversation_id} (dry-run={dry_run})",
    "runtime.openai_extract.custom_mask_patterns": "[extract] custom sanitize mask_patterns configured; built-in email/phone masking is not active",
    "runtime.openai_extract.dry_run_skip": "[extract] dry-run: matched conversation={conversation_id}; skip writing {path}",
    "runtime.openai_extract.wrote": "[extract] wrote {path}",
    "runtime.analyze_tokens.unsupported_encoding": "unsupported --encoding for analyze tokens: {encoding}",
    "runtime.analyze_tokens.unsupported_model": "unsupported --model for analyze tokens: {model}",
    "runtime.analyze_tokens.unsupported_provider": "unsupported provider for analyze tokens: {provider}. Use --encoding to override tokenizer resolution.",
    "runtime.exporter.preview_bytes": "[preview] ~{total_bytes} / {messages} messages",
    "runtime.exporter.preview_estimated_parts": "[preview] estimated parts: {parts}",
    "runtime.exporter.wrote_file": "  - {name} (messages={messages}, ~{bytes})",
    "runtime.sanitize.invalid_scope": "Invalid config: sanitize.scope must be one of: content_parts, all_strings",
}

_JA_MESSAGES = {
    "cli.description": "LLM Log Parser 用のCLIインターフェース（MVP）",
    "cli.option.lang.help": "CLI 表示の言語/ロケール (--locale、旧 --lang。例: en, ja)",
    "cli.option.log_level.help": "ログレベルを指定 (DEBUG|INFO|WARNING|ERROR|CRITICAL)。環境変数 LLM_LOGPARSER_LOGLEVEL を上書き",
    "cli.option.config.help": "config.yaml へのパス",
    "cli.option.profile.help": "使用するプロファイル名",
    "cli.option.non_interactive.help": "対話プロンプトを無効にし、必要な値が不足している場合は失敗する",
    "cli.option.timezone.help": "IANA タイムゾーン (例: Asia/Tokyo)",
    "cli.option.formatting.help": "最小限の Markdown 整形を適用する (none|light)。",
    "cli.option.split.help": "size=<4M|512KiB|...> または count=<N> または auto (auto = size=4M & count=1500)",
    "cli.option.tiny_tail_threshold.help": "末尾結合のしきい値 (メッセージ数)",
    "cli.parse.help": "プロバイダのエクスポートJSONを正規化JSONLスレッドに変換する",
    "cli.export.help": "parsedログをMarkdownに出力する",
    "cli.extract.help": "1つの会話を Gemini 互換 JSON として抽出する",
    "cli.analyze.help": "正規化済み parsed JSONL スレッドを解析する",
    "cli.chain.help": "生エクスポートを解析し、全スレッドをMarkdownに出力する",
    "cli.viewer.help": "（プレースホルダ）簡易HTMLビューアを起動する",
    "cli.config.help": "ランタイム設定を確認・検証する",
    "cli.config.path.help": "解決された設定ファイルのパスを表示する",
    "cli.config.show.help": "正規化済み設定または選択したプロファイルを表示する",
    "cli.config.validate.help": "現在の設定を検証して終了する",

    "cli.parse.provider": "プロバイダ: {provider}",
    "cli.parse.input": "入力ファイル: {path}",
    "cli.parse.outdir": "出力ディレクトリ: {path}",
    "cli.parse.done": "✅ {threads} スレッド（{messages} メッセージ）をパースしました",
    "cli.parse.opt.provider.help": "プロバイダ ID (例: openai)",
    "cli.parse.opt.input.help": "入力 JSON/JSONL パス",
    "cli.parse.opt.outdir.help": "出力ルートディレクトリ (provider 配下は自動作成)",
    "cli.parse.opt.dry_run.help": "ファイルを書き込まずに parse を実行する (統計/ログのみ)。",
    "cli.parse.opt.fail_fast.help": "継続せず、最初のエラーで parse を停止する。",
    "cli.parse.opt.validate_schema.help": "正規化済みメッセージを message.schema.json で検証する",
    "cli.export.opt.input.help": "スレッド parsed.jsonl へのパス",
    "cli.export.opt.out.help": "出力 Markdown パス",
    "cli.extract.opt.conversation_id.help": "抽出する会話 ID",
    "cli.extract.opt.outdir.help": "出力ルートディレクトリ",
    "cli.analyze.opt.input.help": "parsed.jsonl ファイル、または parsed.jsonl を含むディレクトリへのパス",
    "cli.analyze.opt.json.help": "人間向けテキストではなく JSON を出力する",
    "cli.analyze.opt.out.help": "描画結果をファイルに書き込む",
    "cli.analyze.stats.help": "parsed JSONL から決定的な会話統計を計算する",
    "cli.analyze.stats.opt.per_thread.help": "人間向け出力にスレッドごとの行を含める",
    "cli.analyze.stats.opt.top.help": "ソート後にスレッドごとの行数を制限する",
    "cli.analyze.stats.opt.sort.help": "スレッドごとの行のソート項目",
    "cli.analyze.stats.opt.include_role_breakdown.help": "user / assistant 以外のロール内訳を含める",
    "cli.analyze.timeline.help": "時刻付きメッセージの活動を時間軸で集計する",
    "cli.analyze.timeline.opt.bucket.help": "タイムライン集計のバケット幅",
    "cli.analyze.tokens.help": "正規化済み parsed JSONL から決定的な token_stats.json を生成する",
    "cli.analyze.tokens.opt.model.help": "トークナイザ解決用の任意モデル名 (主に OpenAI 向け)",
    "cli.analyze.tokens.opt.encoding.help": "provider/model 解決より優先される明示的な tiktoken エンコーディング名",
    "cli.analyze.metrics.help": "parsed.jsonl と token_stats.json から決定的な metrics.json を生成する",
    "cli.analyze.sqlite_build.help": "正規化済みスレッド成果物から任意の SQLite アクセラレータを構築する",
    "cli.analyze.sqlite_build.opt.input.help": "provider ごとの成果物ディレクトリを含むルートディレクトリ",
    "cli.analyze.sqlite_build.opt.provider.help": "インデックス対象のプロバイダ ID (例: openai)",
    "cli.analyze.sqlite_build.opt.overwrite.help": "既存の analysis.db を削除して再構築する",
    "cli.chain.opt.outdir.help": "成果物のルートディレクトリ (parse+export)。Parsed JSONL は outdir/output/<provider>/... 配下になる",
    "cli.chain.opt.export_outdir.help": "すべての Markdown を置く任意のルートディレクトリ。未指定の場合は各スレッドディレクトリの隣に書き込む",
    "cli.chain.opt.parsed_root.help": "既に parsed スレッド (…/thread-*/parsed.jsonl) を含む任意のルートディレクトリ。指定した場合は parse フェーズをスキップする",
    "cli.chain.opt.fail_fast.help": "最初の export エラーで chain 処理を停止する。既定では継続する。",
    "cli.chain.opt.validate_schema.help": "parse フェーズ中に正規化済みメッセージを検証する",

    "cli.error.path": "パスエラー: {detail}",
    "cli.error.permission": "アクセス権限エラー: {detail}",
    "cli.error.unexpected": "予期しないエラー: {detail}",
    "error.path": "パスエラー: {detail}",
    "error.permission": "アクセス権限エラー: {detail}",
    "error.unexpected": "予期しないエラー: {detail}",
    "error.path_not_found": "指定されたパスが存在しません: {path}",
    "error.path_expected_file": "ファイルパスを指定してください: {path}",
    "error.path_expected_dir": "ディレクトリパスを指定してください: {path}",
    "error.missing_required": "'{command}' に必要なオプションが不足しています:",
    "error.missing_required_hint.provider": "--provider / config: provider",
    "error.missing_required_hint.input": "--input / config: input.path, input.paths, input.parsed(export)",
    "error.missing_required_hint.conversation_id": "--conversation-id / config: extract.conversation_id",
    "error.missing_required_hint.generic": "CLI オプションまたは設定値",
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
