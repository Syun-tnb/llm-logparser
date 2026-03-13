# src/llm_logparser/cli.py
from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict
import logging
from pathlib import Path
from datetime import timezone as _dt_timezone

from zoneinfo import ZoneInfo

from llm_logparser.cli.config_apply import (
    apply_profile_defaults,
    missing_required_fields,
    parse_explicit_flags,
    resolve_profile,
)
from llm_logparser.cli.config_loader import load_config_with_discovery
from llm_logparser.cli.prompts import (
    interactive_enabled,
    prompt_choice,
    prompt_existing_file,
    prompt_text,
)
from llm_logparser.core.i18n import _, set_locale

def setup_logger(level: str | None = None) -> logging.Logger:
    """プロジェクト全体で共有するルートロガー設定
    重複ハンドラを避けつつ一度だけ設定する。
    """
    logger = logging.getLogger("llm_logparser")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        logger.addHandler(handler)
    env_level = os.getenv("LLM_LOGPARSER_LOGLEVEL")
    raw_level = level or env_level or "INFO"
    logger.setLevel(getattr(logging, raw_level.upper(), logging.INFO))
    return logger


def validate_path(
    path: Path,
    *,
    must_exist: bool = True,
    expect_file: bool = False,
    expect_dir: bool = False,
) -> Path:
    """入力ファイル・出力ディレクトリのバリデーション"""
    target = path.expanduser()
    if must_exist and not target.exists():
        raise FileNotFoundError(f"指定されたパスが存在しません: {target}")
    if expect_file and target.is_dir():
        raise IsADirectoryError(f"ファイルパスを指定してください: {target}")
    if expect_dir and not target.is_dir():
        raise NotADirectoryError(f"ディレクトリパスを指定してください: {target}")
    return target


def validate_split_option(raw: str | None) -> str | None:
    if raw is None:
        return None
    normalized = raw.strip()
    if not normalized:
        return None
    lowered = normalized.lower()
    if lowered == "auto" or lowered.startswith("size=") or lowered.startswith("count="):
        return normalized
    raise SystemExit(f"invalid --split: {raw}")


def _missing_arg_message(command: str, missing: list[str]) -> str:
    key_hint = {
        "provider": "--provider / config: provider",
        "input": "--input / config: input.path, input.paths, input.parsed(export)",
        "conversation_id": "--conversation-id / config: conversation_id",
    }
    lines = [f"Missing required options for '{command}':"]
    for name in missing:
        lines.append(f"  - {name}: {key_hint.get(name, 'CLI option or config value')}")
    return "\n".join(lines)


def main():
    set_locale()

    parser = argparse.ArgumentParser(
        prog="llm-logparser",
        description=_("cli.description"),
    )
    parser.add_argument(
        "--locale",
        "--lang",
        dest="locale",
        default=None,
        help=_("cli.option.lang.help"),
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default=None,
        help=_("cli.option.log_level.help"),
    )
    parser.add_argument("--config", type=Path, help="Path to config.yaml")
    parser.add_argument("--profile", help="Profile name to use")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Disable interactive prompts and fail when required values are missing",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # ------------------------------------------------------------
    # parse サブコマンド
    # ------------------------------------------------------------
    parse_cmd = subparsers.add_parser(
        "parse", 
        help=_("cli.parse.help"),
    )
    parse_cmd.add_argument(
        "--provider",
        required=False,
        help=_("cli.parse.opt.provider.help"),
    )
    parse_cmd.add_argument(
        "--input",
        required=False,
        type=Path,
        help=_("cli.parse.opt.input.help"),
    )
    parse_cmd.add_argument(
        "--outdir",
        required=False,
        type=Path,
        default=Path("artifacts"),
        help=_("cli.parse.opt.outdir.help"),
    )
    parse_cmd.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help=_("cli.parse.opt.dry_run.help"),
    )
    parse_cmd.add_argument(
        "--fail-fast",
        dest="fail_fast",
        action="store_true",
        help=_("cli.parse.opt.fail_fast.help"),
    )
    parse_cmd.add_argument(
        "--validate-schema",
        dest="validate_schema",
        action="store_true",
        help="Validate normalized messages against message.schema.json",
    )

    # ------------------------------------------------------------
    # export サブコマンド
    # ------------------------------------------------------------
    export_cmd = subparsers.add_parser(
        "export",
        help="Export a normalized thread JSONL into a single Markdown file",
    )
    export_cmd.add_argument("--input", required=False, type=Path, help="Path to thread parsed.jsonl")
    export_cmd.add_argument("--out", required=False, type=Path, help="Output Markdown path")
    export_cmd.add_argument(
        "--timezone",
        "--tz",
        dest="timezone",
        required=False,
        default="UTC",
        help="IANA timezone (e.g., Asia/Tokyo)",
    )
    export_cmd.add_argument("--formatting", choices=["none", "light"], default="light", help="Apply minimal Markdown formatting (none|light).")
    export_cmd.add_argument("--split", dest="split", help="size=<4M|512KiB|...> or count=<N> or auto (auto = size=4M & count=1500)")
    export_cmd.add_argument("--split-soft-overflow", dest="split_soft_overflow", type=float, default=0.20)
    export_cmd.add_argument("--split-hard", dest="split_hard", action="store_true")
    export_cmd.add_argument("--split-preview", dest="split_preview", action="store_true")
    export_cmd.add_argument("--tiny-tail-threshold", dest="tiny_tail_threshold", type=int, default=20, help="Threshold for tail merge (message count)")

    # ------------------------------------------------------------
    # extract サブコマンド
    # ------------------------------------------------------------
    extract_cmd = subparsers.add_parser(
        "extract",
        help="Extract one conversation as Gemini-compatible JSON",
    )
    extract_cmd.add_argument("--provider", required=False, help="Provider ID (e.g., openai)")
    extract_cmd.add_argument("--input", required=False, type=Path, help="Input JSON/JSONL path")
    extract_cmd.add_argument("--conversation-id", required=False, help="Conversation ID to extract")
    extract_cmd.add_argument(
        "--outdir",
        required=False,
        type=Path,
        default=Path("artifacts"),
        help="Output root directory",
    )
    extract_cmd.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help=_("cli.parse.opt.dry_run.help"),
    )

    # ------------------------------------------------------------
    # analyze サブコマンド
    # ------------------------------------------------------------
    analyze_cmd = subparsers.add_parser(
        "analyze",
        help="Analyze canonical parsed JSONL threads",
    )
    analyze_subparsers = analyze_cmd.add_subparsers(
        dest="analyze_command",
        required=True,
    )
    analyze_stats_cmd = analyze_subparsers.add_parser(
        "stats",
        help="Compute deterministic conversation statistics from parsed JSONL",
    )
    analyze_stats_cmd.add_argument(
        "--input",
        required=False,
        type=Path,
        help="Path to a parsed.jsonl file or a directory containing parsed.jsonl files",
    )
    analyze_stats_cmd.add_argument(
        "--json",
        dest="json",
        action="store_true",
        help="Emit JSON instead of human-readable text",
    )
    analyze_stats_cmd.add_argument(
        "--out",
        required=False,
        type=Path,
        help="Write the rendered result to a file",
    )

    # ------------------------------------------------------------
    # chain サブコマンド（parse → export を一気通し）
    # ------------------------------------------------------------
    chain_cmd = subparsers.add_parser(
        "chain",
        help="Parse raw export and export all threads to Markdown in one shot",
    )
    chain_cmd.add_argument("--provider", required=False, help="Provider ID (e.g., openai)")
    chain_cmd.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help=_("cli.parse.opt.dry_run.help"),
    )
    chain_cmd.add_argument("--input", required=False, type=Path, help="Input JSON/JSONL path")
    chain_cmd.add_argument("--outdir", required=False, type=Path, default=Path("artifacts"), help="Root directory for artifacts (parse+export). Parsed JSONL will be under outdir/output/<provider>/...")
    chain_cmd.add_argument(
        "--timezone",
        "--tz",
        dest="timezone",
        required=False,
        default="UTC",
        help="IANA timezone (e.g., Asia/Tokyo)",
    )
    chain_cmd.add_argument("--formatting", choices=["none", "light"], default="light", help="Apply minimal Markdown formatting (none|light).")
    chain_cmd.add_argument("--split", dest="split", help="size=<4M|512KiB|...> or count=<N> or auto (auto = size=4M & count=1500)")
    chain_cmd.add_argument("--split-soft-overflow", dest="split_soft_overflow", type=float, default=0.20)
    chain_cmd.add_argument("--split-hard", dest="split_hard", action="store_true")
    chain_cmd.add_argument("--split-preview", dest="split_preview", action="store_true")
    chain_cmd.add_argument("--tiny-tail-threshold", dest="tiny_tail_threshold", type=int, default=20, help="Threshold for tail merge (message count)")
    chain_cmd.add_argument("--export-outdir", dest="export_outdir", type=Path,help="Optional root directory to place all exported Markdown files. If omitted, Markdown is written next to each thread directory.")
    chain_cmd.add_argument("--parsed-root", dest="parsed_root", type=Path, help="Optional root directory that already contains parsed threads (…/thread-*/parsed.jsonl). If specified, parse phase is skipped.")
    chain_cmd.add_argument("--fail-fast", dest="fail_fast", action="store_true", help="Stop chain processing on first export error. Default is to continue.")
    chain_cmd.add_argument(
        "--validate-schema",
        dest="validate_schema",
        action="store_true",
        help="Validate normalized messages during the parse phase",
    )

    # ------------------------------------------------------------
    # プレースホルダコマンド
    # ------------------------------------------------------------
    subparsers.add_parser("viewer", help="(placeholder) Viewer command (not implemented yet)")
    subparsers.add_parser("config", help="(placeholder) Config command (not implemented yet)")

    args = parser.parse_args()
    explicit_flags = parse_explicit_flags(sys.argv[1:])
    non_interactive = args.non_interactive or os.getenv("LLM_LOGPARSER_NON_INTERACTIVE") == "1"
    can_prompt = interactive_enabled(non_interactive=non_interactive)

    config, config_path = load_config_with_discovery(args.config)
    profile: dict[str, Any] | None = None
    profiles: dict[str, Any] = {}
    if config is not None:
        profile, profiles = resolve_profile(config, args.profile)
        if profile is None and can_prompt and len(profiles) > 1:
            selected = prompt_choice("Select profile:", list(profiles.keys()), allow_skip=True)
            if selected is not None:
                candidate = profiles.get(selected)
                if isinstance(candidate, dict):
                    profile = candidate

    extra_info: dict[str, Any] = {}
    if profile is not None:
        extra_info = apply_profile_defaults(
            args,
            profile,
            explicit_flags,
            base_dir=config_path.parent if config_path is not None else None,
        )

    input_candidates = extra_info.get("input_candidates")
    if (
        args.command in ("parse", "export", "chain", "extract")
        and args.input is None
        and isinstance(input_candidates, list)
        and input_candidates
    ):
        if can_prompt:
            selected_input = prompt_choice("Select input file:", [str(p) for p in input_candidates])
            if selected_input:
                args.input = Path(selected_input)
        else:
            print(
                "Multiple input paths found in config. Resolve ambiguity with --input "
                "or keep a single input.path/input.paths entry.",
                file=sys.stderr,
            )
            sys.exit(2)

    set_locale(args.locale)
    logger = setup_logger(args.log_level)

    missing = missing_required_fields(args)
    if missing:
        if can_prompt:
            if "provider" in missing:
                provider_default = profile.get("provider") if isinstance(profile, dict) else None
                args.provider = prompt_text("Provider (e.g., openai):", default=provider_default)
            if "input" in missing:
                default_input = None
                if isinstance(profile, dict):
                    input_cfg = profile.get("input")
                    if isinstance(input_cfg, dict):
                        v = input_cfg.get("path") or input_cfg.get("parsed")
                        if isinstance(v, str) and v.strip():
                            default_input = v
                args.input = prompt_existing_file("Input file path:", default=default_input)
            if "conversation_id" in missing:
                conv_default = profile.get("conversation_id") if isinstance(profile, dict) else None
                args.conversation_id = prompt_text(
                    "Conversation ID:", default=conv_default if isinstance(conv_default, str) else None
                )
        else:
            logger.error(_missing_arg_message(args.command, missing))
            sys.exit(2)

    if (
        args.command == "analyze"
        and args.analyze_command == "stats"
        and args.input is None
    ):
        if can_prompt:
            raw_input = prompt_text("Input parsed.jsonl or directory path:")
            args.input = Path(raw_input) if raw_input else None
        else:
            logger.error(
                "Missing required options for 'analyze stats':\n"
                "  - input: --input"
            )
            sys.exit(2)

    try:
        # --------------------------------------------------------
        # parse
        # --------------------------------------------------------
        if args.command == "parse":
            from llm_logparser.core.parser import parse_to_jsonl

            input_path = validate_path(args.input, expect_file=True)
            # parse_to_jsonl() 側で <outdir>/<provider>/... を作る
            args.outdir.mkdir(parents=True, exist_ok=True)
            provider_outdir = args.outdir / args.provider

            logger.info(f"Provider: {args.provider}")
            logger.info(f"Input file: {input_path}")
            logger.info(f"Output directory: {provider_outdir}")
            logger.info(f"Dry run   : {args.dry_run}")
            logger.info(f"Fail fast : {args.fail_fast}")
            schema_validator = None
            if args.validate_schema:
                from llm_logparser.core.schema_validation import MessageSchemaValidator

                schema_validator = MessageSchemaValidator()
                logger.info(
                    f"Schema validation: enabled ({schema_validator.schema_path.name})"
                )

            stats: Dict[str, Any] = parse_to_jsonl(
                args.provider,
                input_path,
                args.outdir,
                dry_run=args.dry_run,
                fail_fast=args.fail_fast,
                validate_schema=args.validate_schema,
                schema_validator=schema_validator,
            )

            # stats の安全なアクセス
            threads = stats.get("threads", 0)
            messages = stats.get("messages", 0)
            logger.info(f"✅ Parsed {threads} threads ({messages} messages)")


        # --------------------------------------------------------
        # export
        # --------------------------------------------------------
        elif args.command == "export":
            from llm_logparser.core.exporter import export_thread_md

            in_path = validate_path(args.input, expect_file=True)

            if args.out:
                out_md = args.out
            else:
                parent = in_path.parent
                out_md = parent / f"{parent.name}.md"

            try:
                tz = ZoneInfo(args.timezone)
            except Exception:
                logger.warning(f"Unknown timezone '{args.timezone}', fallback to UTC")
                tz = _dt_timezone.utc

            split_option = validate_split_option(args.split)

            logger.info(f"Input JSONL: {in_path}")
            logger.info(
                f"Output MD  : {out_md.parent}/thread-<cid>*.md"
                if args.split
                else f"Output MD  : {out_md}"
            )
            logger.info(f"Timezone   : {args.timezone}")
            logger.info(f"Formatting : {args.formatting}")

            opts = {
                "split": split_option,
                "split_soft_overflow": args.split_soft_overflow,
                "split_hard": args.split_hard,
                "split_preview": args.split_preview,
                "tiny_tail_threshold": args.tiny_tail_threshold,
                "formatting": args.formatting,
            }
            paths = export_thread_md(in_path, out_md, tz=tz, **opts)

            if args.split_preview:
                logger.info("✅ Preview only (no files written)")
            else:
                if len(paths) == 1:
                    logger.info("✅ Exported 1 Markdown")
                else:
                    logger.info(f"✅ Exported {len(paths)} Markdown")

        # --------------------------------------------------------
        # extract
        # --------------------------------------------------------
        elif args.command == "extract":
            from llm_logparser.core.parser import extract_to_json

            input_path = validate_path(args.input, expect_file=True)
            args.outdir.mkdir(parents=True, exist_ok=True)

            logger.info(f"Provider: {args.provider}")
            logger.info(f"Input file: {input_path}")
            logger.info(f"Conversation ID: {args.conversation_id}")
            logger.info(f"Output root: {args.outdir}")
            logger.info(f"Dry run: {args.dry_run}")

            result = extract_to_json(
                args.provider,
                input_path,
                args.outdir,
                args.conversation_id,
                dry_run=args.dry_run,
                logger=logger,
            )
            if result.get("written"):
                logger.info(f"✅ Extracted to {result.get('path')}")
            else:
                logger.info(f"✅ Dry-run complete (planned output: {result.get('path')})")

        # --------------------------------------------------------
        # analyze
        # --------------------------------------------------------
        elif args.command == "analyze":
            if args.analyze_command == "stats":
                from llm_logparser.core.analyzer_stats import (
                    analyze_stats,
                    render_stats_json,
                    render_stats_text,
                )

                input_path = validate_path(args.input)
                stats = analyze_stats(input_path)
                rendered = (
                    render_stats_json(stats)
                    if args.json
                    else render_stats_text(stats)
                )

                if args.out:
                    if args.out.exists() and args.out.is_dir():
                        raise IsADirectoryError(
                            f"ファイルパスを指定してください: {args.out}"
                        )
                    args.out.parent.mkdir(parents=True, exist_ok=True)
                    args.out.write_text(f"{rendered}\n", encoding="utf-8")
                else:
                    print(rendered)

        # --------------------------------------------------------
        # chain: parse → export (全thread対象)
        # --------------------------------------------------------
        elif args.command == "chain":
            from llm_logparser.core.exporter import export_thread_md
            from llm_logparser.core.parser import parse_to_jsonl

            input_path = validate_path(args.input, expect_file=True)
            args.outdir.mkdir(parents=True, exist_ok=True)

            logger.info(f"[chain] Provider : {args.provider}")
            logger.info(f"[chain] Input    : {input_path}")
            logger.info(f"[chain] Root     : {args.outdir}")
            logger.info(f"[chain] TZ       : {args.timezone}")
            logger.info(f"[chain] Formatting: {args.formatting}")
            logger.info(f"[chain] Dry run  : {args.dry_run}")
            logger.info(f"[chain] Fail fast: {args.fail_fast}")

            # timezone
            try:
                tz = ZoneInfo(args.timezone)
            except Exception:
                logger.warning(f"Unknown timezone '{args.timezone}', fallback to UTC")
                tz = _dt_timezone.utc

            split_option = validate_split_option(args.split)

            # parsed_root 決定
            if args.parsed_root:
                parsed_root = validate_path(args.parsed_root, expect_dir=True)
                logger.info(f"[chain] Using existing parsed root: {parsed_root}")
            else:
                # chain 専用: outdir/output/<provider> 配下に parse する
                parse_outdir = args.outdir / "output"
                parse_outdir.mkdir(parents=True, exist_ok=True)

                logger.info(f"[chain] Parsing into: {parse_outdir}")
                schema_validator = None
                if args.validate_schema:
                    from llm_logparser.core.schema_validation import MessageSchemaValidator

                    schema_validator = MessageSchemaValidator()
                    logger.info(
                        f"[chain] Schema validation: enabled ({schema_validator.schema_path.name})"
                    )

                stats = parse_to_jsonl(
                    args.provider,
                    input_path,
                    parse_outdir,
                    dry_run=args.dry_run,
                    fail_fast=args.fail_fast,
                    validate_schema=args.validate_schema,
                    schema_validator=schema_validator,
                )
                threads = stats.get("threads", 0)
                messages = stats.get("messages", 0)
                logger.info(f"[chain] Parsed {threads} threads ({messages} messages)")

                parsed_root = parse_outdir / args.provider

            if not parsed_root.exists():
                logger.error(
                    f"[chain] Parsed root directory not found: {parsed_root}\n"
                    f"  - You may need to check your directory layout.\n"
                    f"  - Or specify --parsed-root explicitly."
                )
                sys.exit(4)

            parsed_files = sorted(parsed_root.rglob("parsed.jsonl"))
            if not parsed_files:
                logger.warning(f"[chain] No parsed.jsonl found under {parsed_root}")
                return

            logger.info(f"[chain] Found {len(parsed_files)} thread(s)")

            # export オプション
            export_opts = {
                "split": split_option,
                "split_soft_overflow": args.split_soft_overflow,
                "split_hard": args.split_hard,
                "split_preview": args.split_preview,
                "tiny_tail_threshold": args.tiny_tail_threshold,
                "formatting": args.formatting,
            }

            # export 出力ルート（未指定なら各threadディレクトリ直下）
            export_root: Path | None = None
            if args.export_outdir:
                export_root = args.export_outdir
                export_root.mkdir(parents=True, exist_ok=True)
                logger.info(f"[chain] Export outdir: {export_root}")

            total_md = 0
            failed = 0

            for parsed in parsed_files:
                parent = parsed.parent
                if export_root is not None:
                    out_md = export_root / f"{parent.name}.md"
                else:
                    out_md = parent / f"{parent.name}.md"

                logger.info(f"[chain] Exporting: {parsed} -> {out_md}")

                try:
                    paths = export_thread_md(parsed, out_md, tz=tz, **export_opts)
                except Exception as e:
                    failed += 1
                    logger.error(f"[chain] Failed exporting {parsed}: {e}")
                    if args.fail_fast:
                        raise
                    else:
                        continue

                if not args.split_preview:
                    total_md += len(paths)

            if args.split_preview:
                logger.info(f"[chain] ✅ Preview only (no files written)")
            else:
                succeeded_threads = len(parsed_files) - failed
                logger.info(
                    f"[chain] ✅ Exported {total_md} Markdown file(s) "
                    f"from {succeeded_threads} thread(s) "
                    f"(failed: {failed})"
                )

        # --------------------------------------------------------
        # viewer / config プレースホルダ
        # --------------------------------------------------------
        elif args.command == "viewer":
            logger.warning("[TODO] Viewer not implemented yet.")
        elif args.command == "config":
            logger.warning("[TODO] Config command not implemented yet.")

    except (FileNotFoundError, IsADirectoryError) as e:
        logger.error(f"パスエラー: {e}")
        sys.exit(2)
    except PermissionError as e:
        logger.error(f"アクセス権限エラー: {e}")
        sys.exit(3)
    except Exception as e:
        logger.exception(f"予期しないエラー: {e}")
        sys.exit(99)


if __name__ == "__main__":
    main()
