from __future__ import annotations

import logging

from llm_logparser.cli.common import (
    resolve_timezone,
    validate_path,
    validate_split_option,
)
from llm_logparser.core.i18n import _


def run_chain(args, logger: logging.Logger) -> None:
    from llm_logparser.core.exporter import export_thread_md
    from llm_logparser.core.parser import parse_to_jsonl

    input_path = validate_path(args.input)
    args.outdir.mkdir(parents=True, exist_ok=True)

    logger.info(_("runtime.chain.provider", provider=args.provider))
    logger.info(_("runtime.chain.input", path=input_path))
    logger.info(_("runtime.chain.root", path=args.outdir))
    logger.info(_("runtime.chain.timezone", timezone=args.timezone))
    logger.info(_("runtime.chain.formatting", formatting=args.formatting))
    logger.info(_("runtime.chain.dry_run", dry_run=args.dry_run))
    logger.info(_("runtime.chain.fail_fast", fail_fast=args.fail_fast))

    tz = resolve_timezone(args.timezone, logger)
    split_option = validate_split_option(args.split)

    if args.parsed_root:
        parsed_root = validate_path(args.parsed_root, expect_dir=True)
        logger.info(_("runtime.chain.using_parsed_root", path=parsed_root))
    else:
        parse_outdir = args.outdir / "output"
        parse_outdir.mkdir(parents=True, exist_ok=True)

        logger.info(_("runtime.chain.parsing_into", path=parse_outdir))
        schema_validator = None
        if args.validate_schema:
            from llm_logparser.core.schema_validation import MessageSchemaValidator

            schema_validator = MessageSchemaValidator()
            logger.info(
                _("runtime.chain.schema_validation_enabled", schema_path=schema_validator.schema_path.name)
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
        logger.info(_("runtime.chain.parsed", threads=threads, messages=messages))

        parsed_root = parse_outdir / args.provider

    if not parsed_root.exists():
        logger.error(_("runtime.chain.parsed_root_missing", path=parsed_root))
        raise SystemExit(4)

    parsed_files = sorted(parsed_root.rglob("parsed.jsonl"))
    if not parsed_files:
        logger.warning(_("runtime.chain.no_parsed_jsonl", path=parsed_root))
        return

    logger.info(_("runtime.chain.found_threads", count=len(parsed_files)))

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
        logger.info(_("runtime.chain.export_outdir", path=export_root))

    total_md = 0
    failed = 0

    for parsed in parsed_files:
        parent = parsed.parent
        if export_root is not None:
            out_md = export_root / f"{parent.name}.md"
        else:
            out_md = parent / f"{parent.name}.md"

        logger.info(_("runtime.chain.exporting", parsed=parsed, out=out_md))

        try:
            paths = export_thread_md(parsed, out_md, tz=tz, **export_opts)
        except Exception as e:
            failed += 1
            logger.error(_("runtime.chain.export_failed", parsed=parsed, detail=e))
            if args.fail_fast:
                raise
            continue

        if not args.split_preview:
            total_md += len(paths)

    if args.split_preview:
        logger.info(_("runtime.chain.preview_only"))
    else:
        succeeded_threads = len(parsed_files) - failed
        logger.info(
            _(
                "runtime.chain.done",
                markdown_files=total_md,
                threads=succeeded_threads,
                failed=failed,
            )
        )
