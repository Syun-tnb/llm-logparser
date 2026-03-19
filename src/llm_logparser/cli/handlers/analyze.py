from __future__ import annotations

import logging

from llm_logparser.cli.common import validate_path, write_or_print


def run_analyze_tokens(args, logger: logging.Logger) -> None:
    from llm_logparser.core.analyzer_tokens import analyze_tokens

    input_path = validate_path(args.input)
    result = analyze_tokens(
        input_path,
        model_override=args.model,
        encoding_override=args.encoding,
    )
    logger.info(
        "token_stats.json written for %s thread(s)",
        result["threads"],
    )


def run_analyze_stats(args, logger: logging.Logger) -> None:
    from llm_logparser.core.analyzer_stats import (
        analyze_stats,
        build_stats_output,
        render_stats_json,
        render_stats_text,
    )

    del logger
    input_path = validate_path(args.input)
    if args.top is not None and args.top < 0:
        raise SystemExit("--top must be >= 0")

    stats = analyze_stats(input_path)
    effective_sort = args.sort
    if effective_sort is None and (args.per_thread or args.top is not None):
        effective_sort = "messages"

    output_stats = build_stats_output(
        stats,
        sort_field=effective_sort,
        top=args.top,
    )
    rendered = (
        render_stats_json(output_stats)
        if args.json
        else render_stats_text(
            output_stats,
            per_thread=args.per_thread,
            include_role_breakdown=args.include_role_breakdown,
        )
    )
    write_or_print(rendered, args.out)


def run_analyze_timeline(args, logger: logging.Logger) -> None:
    from llm_logparser.core.analyzer_timeline import (
        analyze_timeline,
        render_timeline_json,
        render_timeline_text,
    )

    del logger
    input_path = validate_path(args.input)
    timeline_data = analyze_timeline(input_path, bucket=args.bucket)
    rendered = (
        render_timeline_json(timeline_data)
        if args.json
        else render_timeline_text(timeline_data)
    )
    write_or_print(rendered, args.out)


def run_analyze_sqlite_build(args, logger: logging.Logger) -> None:
    from llm_logparser.l2_sqlite import build_analysis_db

    input_root = validate_path(args.input, expect_dir=True)
    result = build_analysis_db(
        input_root,
        args.provider,
        overwrite=args.overwrite,
    )
    logger.info(
        "analysis.db built: %s (threads=%s messages=%s windows=%s)",
        result["db_path"],
        result["threads"],
        result["messages"],
        result["message_windows"],
    )
