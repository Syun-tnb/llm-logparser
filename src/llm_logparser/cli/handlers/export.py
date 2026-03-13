from __future__ import annotations

import logging

from llm_logparser.cli.common import (
    resolve_timezone,
    validate_path,
    validate_split_option,
)


def run_export(args, logger: logging.Logger) -> None:
    from llm_logparser.core.exporter import export_thread_md

    in_path = validate_path(args.input, expect_file=True)

    if args.out:
        out_md = args.out
    else:
        parent = in_path.parent
        out_md = parent / f"{parent.name}.md"

    tz = resolve_timezone(args.timezone, logger)
    split_option = validate_split_option(args.split)

    logger.info(f"Input JSONL: {in_path}")
    logger.info(
        f"Output MD  : {out_md.parent}/thread-<cid>*.md"
        if args.split
        else f"Output MD  : {out_md}"
    )
    logger.info(f"Timezone   : {args.timezone}")
    logger.info(f"Formatting : {args.formatting}")

    opts = {
        "split": split_option,
        "split_soft_overflow": args.split_soft_overflow,
        "split_hard": args.split_hard,
        "split_preview": args.split_preview,
        "tiny_tail_threshold": args.tiny_tail_threshold,
        "formatting": args.formatting,
    }
    paths = export_thread_md(in_path, out_md, tz=tz, **opts)

    if args.split_preview:
        logger.info("✅ Preview only (no files written)")
    else:
        if len(paths) == 1:
            logger.info("✅ Exported 1 Markdown")
        else:
            logger.info(f"✅ Exported {len(paths)} Markdown")
