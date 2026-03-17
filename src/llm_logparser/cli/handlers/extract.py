from __future__ import annotations

import logging

from llm_logparser.cli.common import validate_path


def run_extract(args, logger: logging.Logger) -> None:
    from llm_logparser.core.parser import extract_to_json

    input_path = validate_path(args.input, expect_file=True)
    args.outdir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Provider: {args.provider}")
    logger.info(f"Input file: {input_path}")
    logger.info(f"Conversation ID: {args.conversation_id}")
    logger.info(f"Output root: {args.outdir}")
    logger.info(f"Dry run: {args.dry_run}")

    result = extract_to_json(
        args.provider,
        input_path,
        args.outdir,
        args.conversation_id,
        dry_run=args.dry_run,
        sanitize_policy=getattr(args, "sanitize_policy", None),
        logger=logger,
    )
    if result.get("written"):
        logger.info(f"✅ Extracted to {result.get('path')}")
    else:
        logger.info(f"✅ Dry-run complete (planned output: {result.get('path')})")
