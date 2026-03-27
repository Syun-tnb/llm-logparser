from __future__ import annotations

import logging

from llm_logparser.cli.common import validate_path
from llm_logparser.core.i18n import _


def run_extract(args, logger: logging.Logger) -> None:
    from llm_logparser.core.parser import extract_to_json

    input_path = validate_path(args.input, expect_file=True)
    args.outdir.mkdir(parents=True, exist_ok=True)

    logger.info(_("runtime.extract.provider", provider=args.provider))
    logger.info(_("runtime.extract.input", path=input_path))
    logger.info(_("runtime.extract.conversation_id", conversation_id=args.conversation_id))
    logger.info(_("runtime.extract.output_root", path=args.outdir))
    logger.info(_("runtime.extract.dry_run", dry_run=args.dry_run))

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
        logger.info(_("runtime.extract.done", path=result.get("path")))
    else:
        logger.info(_("runtime.extract.dry_run_done", path=result.get("path")))
