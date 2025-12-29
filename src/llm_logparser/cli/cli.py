# src/llm_logparser/cli.py
from __future__ import annotations

import argparse
import sys
from typing import Any, Dict
import logging
from pathlib import Path
from datetime import timezone as _dt_timezone

from zoneinfo import ZoneInfo

from llm_logparser.core.i18n import _, set_locale

def setup_logger() -> logging.Logger:
    """プロジェクト全体で共有するルートロガー設定
    重複ハンドラを避けつつ一度だけ設定する。
    """
    logger = logging.getLogger("llm_logparser")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


def validate_path(path: Path, must_exist: bool = True) -> Path:
    """入力ファイル・出力ディレクトリのバリデーション"""
    if must_exist and not path.exists():
        raise FileNotFoundError(f"指定されたパスが存在しません: {path}")
    if must_exist and path.is_dir():
        raise IsADirectoryError(f"ファイルパスを指定してください: {path}")
    return path


def main():
    logger = setup_logger()

    set_locale()
    
    parser = argparse.ArgumentParser(
        prog="llm-logparser",
        description=_("cli.description"),
    )
    
    parser.add_argument(
        "--lang",
        default=None,
        help=_("cli.option.lang.help"),
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
        required=True,
        help=_("cli.parse.opt.provider.help"),
    )
    parse_cmd.add_argument(
        "--input",
        required=True,
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

    # ------------------------------------------------------------
    # export サブコマンド
    # ------------------------------------------------------------
    export_cmd = subparsers.add_parser(
        "export",
        help="Export a normalized thread JSONL into a single Markdown file",
    )
    export_cmd.add_argument("--input", required=True, type=Path, help="Path to thread parsed.jsonl")
    export_cmd.add_argument("--out", required=False, type=Path, help="Output Markdown path")
    export_cmd.add_argument("--tz", required=False, default="UTC", help="IANA timezone (e.g., Asia/Tokyo)")
    export_cmd.add_argument("--formatting", choices=["none", "light"], default="light", help="Apply minimal Markdown formatting (none|light).")
    export_cmd.add_argument("--split", dest="split", help="size=<4M|512KiB|...> or count=<N> or auto (auto = size=4M & count=1500)")
    export_cmd.add_argument("--split-soft-overflow", dest="split_soft_overflow", type=float, default=0.20)
    export_cmd.add_argument("--split-hard", dest="split_hard", action="store_true")
    export_cmd.add_argument("--split-preview", dest="split_preview", action="store_true")
    export_cmd.add_argument("--tiny-tail-threshold", dest="tiny_tail_threshold", type=int, default=20, help="Threshold for tail merge (message count)")

    # ------------------------------------------------------------
    # chain サブコマンド（parse → export を一気通し）
    # ------------------------------------------------------------
    chain_cmd = subparsers.add_parser(
        "chain",
        help="Parse raw export and export all threads to Markdown in one shot",
    )
    chain_cmd.add_argument("--provider", required=True, help="Provider ID (e.g., openai)")
    chain_cmd.add_argument("--input", required=True, type=Path, help="Input JSON/JSONL path")
    chain_cmd.add_argument("--outdir", required=False, type=Path, default=Path("artifacts"), help="Root directory for artifacts (parse+export). Parsed JSONL will be under outdir/output/<provider>/...")
    chain_cmd.add_argument("--tz", required=False, default="UTC", help="IANA timezone (e.g., Asia/Tokyo)")
    chain_cmd.add_argument("--formatting", choices=["none", "light"], default="light", help="Apply minimal Markdown formatting (none|light).")
    chain_cmd.add_argument("--split", dest="split", help="size=<4M|512KiB|...> or count=<N> or auto (auto = size=4M & count=1500)")
    chain_cmd.add_argument("--split-soft-overflow", dest="split_soft_overflow", type=float, default=0.20)
    chain_cmd.add_argument("--split-hard", dest="split_hard", action="store_true")
    chain_cmd.add_argument("--split-preview", dest="split_preview", action="store_true")
    chain_cmd.add_argument("--tiny-tail-threshold", dest="tiny_tail_threshold", type=int, default=20, help="Threshold for tail merge (message count)")
    chain_cmd.add_argument("--export-outdir", dest="export_outdir", type=Path,help="Optional root directory to place all exported Markdown files. If omitted, Markdown is written next to each thread directory.")
    chain_cmd.add_argument("--parsed-root", dest="parsed_root", type=Path, help="Optional root directory that already contains parsed threads (…/thread-*/parsed.jsonl). If specified, parse phase is skipped.")
    chain_cmd.add_argument("--jobs", dest="jobs", type=int, default=1, help="(Future) Maximum parallel exports. Currently ignored (always serial).")
    chain_cmd.add_argument("--fail-fast", dest="fail_fast", action="store_true", help="Stop chain processing on first export error. Default is to continue.")

    # ------------------------------------------------------------
    # プレースホルダコマンド
    # ------------------------------------------------------------
    subparsers.add_parser("viewer", help="(placeholder) Run lightweight HTML viewer")
    subparsers.add_parser("config", help="(placeholder) Manage runtime configuration")

    args = parser.parse_args()

    if args.lang:
        set_locale(args.lang)

    try:
        # --------------------------------------------------------
        # parse
        # --------------------------------------------------------
        if args.command == "parse":
            from llm_logparser.core.parser import parse_to_jsonl

            input_path = validate_path(args.input)
            # parse_to_jsonl() 側で <outdir>/<provider>/... を作る
            args.outdir.mkdir(parents=True, exist_ok=True)
            provider_outdir = args.outdir / args.provider

            logger.info(f"Provider: {args.provider}")
            logger.info(f"Input file: {input_path}")
            logger.info(f"Output directory: {provider_outdir}")
            logger.info(f"Dry run   : {args.dry_run}")
            logger.info(f"Fail fast : {args.fail_fast}")

            stats: Dict[str, Any] = parse_to_jsonl(
                args.provider,
                input_path,
                args.outdir,
                dry_run=args.dry_run,
                fail_fast=args.fail_fast,
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

            in_path = args.input
            if not in_path.exists():
                raise FileNotFoundError(f"指定されたパスが存在しません: {in_path}")
            if in_path.is_dir():
                raise IsADirectoryError(f"ファイルパスを指定してください: {in_path}")

            if args.out:
                out_md = args.out
            else:
                parent = in_path.parent
                out_md = parent / f"{parent.name}.md"

            try:
                tz = ZoneInfo(args.tz)
            except Exception:
                logger.warning(f"Unknown timezone '{args.tz}', fallback to UTC")
                tz = _dt_timezone.utc

            # --split の軽いバリデーション
            if args.split:
                s = args.split.strip().lower()
                if not (s == "auto" or s.startswith("size=") or s.startswith("count=")):
                    raise SystemExit(f"invalid --split: {args.split}")

            logger.info(f"Input JSONL: {in_path}")
            logger.info(
                f"Output MD  : {out_md.parent}/thread-<cid>*.md"
                if args.split
                else f"Output MD  : {out_md}"
            )
            logger.info(f"Timezone   : {args.tz}")
            logger.info(f"Formatting : {args.formatting}")

            opts = {
                "split": args.split,
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
        # chain: parse → export (全thread対象)
        # --------------------------------------------------------
        elif args.command == "chain":
            from llm_logparser.core.exporter import export_thread_md
            from llm_logparser.core.parser import parse_to_jsonl

            input_path = validate_path(args.input)
            args.outdir.mkdir(parents=True, exist_ok=True)

            logger.info(f"[chain] Provider : {args.provider}")
            logger.info(f"[chain] Input    : {input_path}")
            logger.info(f"[chain] Root     : {args.outdir}")
            logger.info(f"[chain] TZ       : {args.tz}")
            logger.info(f"[chain] Formatting: {args.formatting}")

            if args.jobs and args.jobs > 1:
                logger.info(f"[chain] --jobs={args.jobs} specified "
                            f"but parallelism is not implemented yet (running serially).")

            # timezone
            try:
                tz = ZoneInfo(args.tz)
            except Exception:
                logger.warning(f"Unknown timezone '{args.tz}', fallback to UTC")
                tz = _dt_timezone.utc

            # --split の軽いバリデーション（export と同様）
            if args.split:
                s = args.split.strip().lower()
                if not (s == "auto" or s.startswith("size=") or s.startswith("count=")):
                    raise SystemExit(f"invalid --split: {args.split}")

            # parsed_root 決定
            if args.parsed_root:
                parsed_root = args.parsed_root
                logger.info(f"[chain] Using existing parsed root: {parsed_root}")
            else:
                # chain 専用: outdir/output/<provider> 配下に parse する
                parse_outdir = args.outdir / "output"
                parse_outdir.mkdir(parents=True, exist_ok=True)

                logger.info(f"[chain] Parsing into: {parse_outdir}")
                stats = parse_to_jsonl(args.provider, input_path, parse_outdir)
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
                "split": args.split,
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
