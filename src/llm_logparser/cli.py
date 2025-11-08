# src/llm_logparser/cli.py
from __future__ import annotations
import argparse
import sys
import logging
from pathlib import Path
from llm_logparser.parser import parse_to_jsonl
from llm_logparser.exporter import export_thread_md
from zoneinfo import ZoneInfo
from datetime import timezone as _dt_timezone


def setup_logger():
    """標準出力用の簡易ロガー設定"""
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    return logging.getLogger("llm_logparser")


def validate_path(path: Path, must_exist: bool = True) -> Path:
    """入力ファイル・出力ディレクトリのバリデーション"""
    if must_exist and not path.exists():
        raise FileNotFoundError(f"指定されたパスが存在しません: {path}")
    if must_exist and path.is_dir():
        raise IsADirectoryError(f"ファイルパスを指定してください: {path}")
    return path


def main():
    logger = setup_logger()

    parser = argparse.ArgumentParser(
        prog="llm-logparser",
        description="CLI interface for LLM Log Parser (MVP)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ------------------------------------------------------------
    # parse サブコマンド
    # ------------------------------------------------------------
    parse_cmd = subparsers.add_parser(
        "parse",
        help="Parse provider export JSON into normalized JSONL threads",
    )
    parse_cmd.add_argument("--provider", required=True, help="Provider ID (e.g., openai)")
    parse_cmd.add_argument("--input", required=True, type=Path, help="Input JSON/JSONL path")
    parse_cmd.add_argument(
        "--outdir",
        required=False,
        type=Path,
        default=Path("artifacts/output"),
        help="Output root directory",
    )

    # ------------------------------------------------------------
    # export サブコマンド（最小実装：1 JSONL → 1 Markdown）
    # ------------------------------------------------------------
    export_cmd = subparsers.add_parser(
        "export",
        help="Export a normalized thread JSONL into a single Markdown file",
    )
    export_cmd.add_argument("--input", required=True, type=Path, help="Path to thread parsed.jsonl")
    export_cmd.add_argument("--out", required=False, type=Path, help="Output Markdown path")
    export_cmd.add_argument("--tz", required=False, default="UTC", help="IANA timezone (e.g., Asia/Tokyo)")
    export_cmd.add_argument("--split", dest="split", help="size=<4M|512KiB|...> or count=<N> or auto (auto = size=4M & count=1500)")
    export_cmd.add_argument("--split-soft-overflow", dest="split_soft_overflow", type=float, default=0.20)
    export_cmd.add_argument("--split-hard", dest="split_hard", action="store_true")
    export_cmd.add_argument("--split-preview", dest="split_preview", action="store_true")
    export_cmd.add_argument("--tiny-tail-threshold", dest="tiny_tail_threshold", type=int, default=20, help="Threshold for tail merge (message count)")

    # プレースホルダコマンド
    subparsers.add_parser("viewer", help="(placeholder) Run lightweight HTML viewer")
    subparsers.add_parser("config", help="(placeholder) Manage runtime configuration")

    args = parser.parse_args()

    try:
        if args.command == "parse":
            input_path = validate_path(args.input)
            args.outdir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Provider: {args.provider}")
            logger.info(f"Input file: {input_path}")
            logger.info(f"Output directory: {args.outdir}")

            stats = parse_to_jsonl(args.provider, input_path, args.outdir)
            logger.info(f"✅ Parsed {stats['threads']} threads ({stats['messages']} messages)")

        elif args.command == "export":
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
            logger.info(f"Output MD  : {out_md.parent}/thread-<cid>*.md" if args.split else f"Output MD  : {out_md}")
            logger.info(f"Timezone   : {args.tz}")

            opts = {
                "split": args.split,
                "split_soft_overflow": args.split_soft_overflow,
                "split_hard": args.split_hard,
                "split_preview": args.split_preview,
                "tiny_tail_threshold": args.tiny_tail_threshold,
            }
            paths = export_thread_md(in_path, out_md, tz=tz, **opts)

            if args.split_preview:
                logger.info("✅ Preview only (no files written)")
            else:
                if len(paths) == 1:
                    logger.info("✅ Exported 1 Markdown")
                else:
                    logger.info(f"✅ Exported {len(paths)} Markdown")

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
