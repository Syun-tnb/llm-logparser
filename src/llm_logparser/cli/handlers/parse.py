from __future__ import annotations

import logging

from llm_logparser.cli.common import validate_path
from llm_logparser.core.i18n import _
from llm_logparser.core.utils import format_display_path


def run_parse(args, logger: logging.Logger) -> None:
    from llm_logparser.core.parser import parse_to_jsonl

    input_path = validate_path(args.input)
    args.outdir.mkdir(parents=True, exist_ok=True)
    provider_outdir = args.outdir / args.provider

    logger.info(_("runtime.parse.provider", provider=args.provider))
    logger.info(_("runtime.parse.input", path=input_path))
    logger.info(_("runtime.parse.output_dir", path=format_display_path(provider_outdir)))
    logger.info(_("runtime.parse.dry_run", dry_run=args.dry_run))
    logger.info(_("runtime.parse.fail_fast", fail_fast=args.fail_fast))
    schema_validator = None
    if args.validate_schema:
        from llm_logparser.core.schema_validation import MessageSchemaValidator

        schema_validator = MessageSchemaValidator()
        logger.info(
            _("runtime.parse.schema_validation_enabled", schema_path=schema_validator.schema_path.name)
        )

    stats = parse_to_jsonl(
        args.provider,
        input_path,
        args.outdir,
        dry_run=args.dry_run,
        fail_fast=args.fail_fast,
        validate_schema=args.validate_schema,
        schema_validator=schema_validator,
        message_window_size=args.message_window_size,
        message_window_stride=args.message_window_stride,
    )

    threads = stats.get("threads", 0)
    messages = stats.get("messages", 0)
    logger.info(_("runtime.parse.done", threads=threads, messages=messages))
