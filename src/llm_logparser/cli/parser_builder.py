from __future__ import annotations

import argparse
from pathlib import Path

from llm_logparser.core.i18n import _


def build_parser() -> argparse.ArgumentParser:
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

    export_cmd = subparsers.add_parser(
        "export",
        help="Export a normalized thread JSONL into Markdown",
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
    analyze_stats_cmd.add_argument(
        "--per-thread",
        dest="per_thread",
        action="store_true",
        help="Include per-thread rows in human-readable output",
    )
    analyze_stats_cmd.add_argument(
        "--top",
        required=False,
        type=int,
        help="Limit the number of per-thread rows after sorting",
    )
    analyze_stats_cmd.add_argument(
        "--sort",
        choices=["messages", "chars", "span", "conversation_id"],
        default=None,
        help="Sort field for per-thread rows",
    )
    analyze_stats_cmd.add_argument(
        "--include-role-breakdown",
        dest="include_role_breakdown",
        action="store_true",
        help="Include breakdown of roles other than user and assistant",
    )

    analyze_timeline_cmd = analyze_subparsers.add_parser(
        "timeline",
        help="Aggregate timestamped message activity over time",
    )
    analyze_timeline_cmd.add_argument(
        "--input",
        required=False,
        type=Path,
        help="Path to a parsed.jsonl file or a directory containing parsed.jsonl files",
    )
    analyze_timeline_cmd.add_argument(
        "--bucket",
        choices=["hour", "day", "week", "month"],
        default="day",
        help="Bucket size for timeline aggregation",
    )
    analyze_timeline_cmd.add_argument(
        "--json",
        dest="json",
        action="store_true",
        help="Emit JSON instead of human-readable text",
    )
    analyze_timeline_cmd.add_argument(
        "--out",
        required=False,
        type=Path,
        help="Write the rendered result to a file",
    )

    analyze_sqlite_build_cmd = analyze_subparsers.add_parser(
        "sqlite-build",
        help="Build an optional SQLite accelerator from canonical thread artifacts",
    )
    analyze_sqlite_build_cmd.add_argument(
        "--input",
        required=False,
        type=Path,
        help="Root directory containing per-provider artifact directories",
    )
    analyze_sqlite_build_cmd.add_argument(
        "--provider",
        required=False,
        help="Provider ID to index (for example: openai)",
    )
    analyze_sqlite_build_cmd.add_argument(
        "--overwrite",
        dest="overwrite",
        action="store_true",
        help="Delete an existing analysis.db before rebuilding",
    )

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

    subparsers.add_parser("viewer", help="(placeholder) Viewer command (not implemented yet)")

    config_cmd = subparsers.add_parser(
        "config",
        help="Inspect and validate runtime configuration",
    )
    config_subparsers = config_cmd.add_subparsers(
        dest="config_command",
        required=True,
    )
    config_subparsers.add_parser("path", help="Show the resolved config file path")
    config_subparsers.add_parser("show", help="Print the normalized config or selected profile")
    config_subparsers.add_parser("validate", help="Validate the current config and exit")

    return parser
