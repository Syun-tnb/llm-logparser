from __future__ import annotations

import logging

from llm_logparser.cli.common import validate_path


def run_parse(args, logger: logging.Logger) -> None:
    from llm_logparser.core.parser import parse_to_jsonl

    input_path = validate_path(args.input, expect_file=True)
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

    stats = parse_to_jsonl(
        args.provider,
        input_path,
        args.outdir,
        dry_run=args.dry_run,
        fail_fast=args.fail_fast,
        validate_schema=args.validate_schema,
        schema_validator=schema_validator,
    )

    threads = stats.get("threads", 0)
    messages = stats.get("messages", 0)
    logger.info(f"✅ Parsed {threads} threads ({messages} messages)")
