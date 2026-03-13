from __future__ import annotations

import logging

from llm_logparser.cli.common import (
    resolve_timezone,
    validate_path,
    validate_split_option,
)


def run_chain(args, logger: logging.Logger) -> None:
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

    tz = resolve_timezone(args.timezone, logger)
    split_option = validate_split_option(args.split)

    if args.parsed_root:
        parsed_root = validate_path(args.parsed_root, expect_dir=True)
        logger.info(f"[chain] Using existing parsed root: {parsed_root}")
    else:
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
        raise SystemExit(4)

    parsed_files = sorted(parsed_root.rglob("parsed.jsonl"))
    if not parsed_files:
        logger.warning(f"[chain] No parsed.jsonl found under {parsed_root}")
        return

    logger.info(f"[chain] Found {len(parsed_files)} thread(s)")

    export_opts = {
        "split": split_option,
        "split_soft_overflow": args.split_soft_overflow,
        "split_hard": args.split_hard,
        "split_preview": args.split_preview,
        "tiny_tail_threshold": args.tiny_tail_threshold,
        "formatting": args.formatting,
    }

    export_root = None
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
            continue

        if not args.split_preview:
            total_md += len(paths)

    if args.split_preview:
        logger.info("[chain] ✅ Preview only (no files written)")
    else:
        succeeded_threads = len(parsed_files) - failed
        logger.info(
            f"[chain] ✅ Exported {total_md} Markdown file(s) "
            f"from {succeeded_threads} thread(s) "
            f"(failed: {failed})"
        )
