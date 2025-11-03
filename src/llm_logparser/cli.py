# src/llm_logparser/cli.py
from __future__ import annotations
import argparse
import sys
from pathlib import Path
from llm_logparser.parser import parse_to_jsonl


def main():
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
    # export / viewer / config はプレースホルダ
    # ------------------------------------------------------------
    subparsers.add_parser("export", help="(placeholder) Export parsed logs to Markdown/HTML")
    subparsers.add_parser("viewer", help="(placeholder) Run lightweight HTML viewer")
    subparsers.add_parser("config", help="(placeholder) Manage runtime configuration")

    args = parser.parse_args()

    # ------------------------------------------------------------
    # コマンドディスパッチ
    # ------------------------------------------------------------
    if args.command == "parse":
        try:
            stats = parse_to_jsonl(args.provider, args.input, args.outdir)
            print(f"✅ Parsed {stats['threads']} threads ({stats['messages']} messages)")
        except Exception as e:
            print(f"[ERROR] Parsing failed: {e}")
            sys.exit(1)

    elif args.command == "export":
        print("[INFO] Export command not implemented yet (MVP scope: parse only).")

    elif args.command == "viewer":
        print("[INFO] Viewer not implemented yet (reserved for GUI mode).")

    elif args.command == "config":
        print("[INFO] Config command not implemented yet (reserved for runtime config).")


if __name__ == "__main__":
    main()
