# src/llm_logparser/cli.py
from __future__ import annotations
import argparse
import sys
import logging
from pathlib import Path
from llm_logparser.parser import parse_to_jsonl


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

    # プレースホルダコマンド
    subparsers.add_parser("export", help="(placeholder) Export parsed logs to Markdown/HTML")
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
            logger.warning("[TODO] Export command not implemented yet.")

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
