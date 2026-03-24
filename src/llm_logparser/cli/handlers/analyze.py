from __future__ import annotations

import logging

from llm_logparser.cli.common import validate_path, write_or_print
from llm_logparser.core.i18n import _


def _log_sidecar_dry_run_summary(
    logger: logging.Logger,
    *,
    artifact_name: str,
    result: dict,
) -> None:
    logger.info(_("runtime.analyze.dry_run.preview", artifact_name=artifact_name))
    logger.info(
        _("runtime.analyze.dry_run.detected_threads", count=result["detected_threads"])
    )
    logger.info(
        _("runtime.analyze.dry_run.existing_sidecars", count=result["existing_threads"])
    )
    logger.info(_("runtime.analyze.dry_run.new_sidecars", count=result["new_threads"]))
    logger.info(
        _("runtime.analyze.dry_run.rebuild_sidecars", count=result["rebuild_threads"])
    )
    logger.info(
        _("runtime.analyze.dry_run.skipped_sidecars", count=result["skipped_threads"])
    )
    logger.info(_("runtime.analyze.dry_run.no_writes"))


def run_analyze_metrics(args, logger: logging.Logger) -> None:
    from llm_logparser.core.analyzer_metrics import (
        MetricsDependencyError,
        analyze_metrics,
    )

    input_path = validate_path(args.input)
    try:
        result = analyze_metrics(
            input_path,
            skip_existing=args.skip_existing,
            dry_run=args.dry_run,
        )
    except MetricsDependencyError as exc:
        logger.error(_("runtime.error.with_code", code=exc.code, detail=str(exc)))
        raise SystemExit(2) from None
    if args.dry_run:
        _log_sidecar_dry_run_summary(
            logger,
            artifact_name="metrics.json",
            result=result,
        )
        return
    logger.info(_("runtime.analyze.metrics_written", threads=result["threads"]))


def run_analyze_tokens(args, logger: logging.Logger) -> None:
    from llm_logparser.core.analyzer_tokens import analyze_tokens

    input_path = validate_path(args.input)
    result = analyze_tokens(
        input_path,
        model_override=args.model,
        encoding_override=args.encoding,
        skip_existing=args.skip_existing,
        dry_run=args.dry_run,
    )
    if args.dry_run:
        _log_sidecar_dry_run_summary(
            logger,
            artifact_name="token_stats.json",
            result=result,
        )
        return
    logger.info(_("runtime.analyze.tokens_written", threads=result["threads"]))


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
        raise SystemExit(_("runtime.analyze.top_non_negative"))

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


def run_analyze_datasheet(args, logger: logging.Logger) -> None:
    from llm_logparser.core.analyzer_datasheet import (
        build_datasheet_summary,
        render_datasheet_json,
        render_datasheet_markdown,
    )

    del logger
    input_path = validate_path(args.input)
    summary = build_datasheet_summary(input_path)
    rendered = (
        render_datasheet_json(summary)
        if args.json
        else render_datasheet_markdown(summary)
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
        _(
            "runtime.analyze.sqlite_built",
            db_path=result["db_path"],
            threads=result["threads"],
            messages=result["messages"],
            windows=result["message_windows"],
        )
    )


def run_analyze_semantic_prototype(args, logger: logging.Logger) -> None:
    from llm_logparser.core.analyzer_semantic_prototype import (
        SemanticPrototypeError,
        analyze_semantic_prototype,
    )

    input_path = validate_path(args.input)
    try:
        result = analyze_semantic_prototype(
            input_path,
            top_k=args.top_k,
            overwrite=args.overwrite,
        )
    except SemanticPrototypeError as exc:
        logger.error(str(exc))
        raise SystemExit(2) from None

    logger.info(
        _(
            "runtime.analyze.semantic_prototype_written",
            threads=result["threads"],
            windows=result["windows"],
            model=result["embedding_model"],
        )
    )
