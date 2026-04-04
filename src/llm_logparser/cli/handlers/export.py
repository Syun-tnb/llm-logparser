from __future__ import annotations

import logging

from llm_logparser.cli.common import (
    resolve_timezone,
    validate_path,
    validate_split_option,
)
from llm_logparser.core.i18n import _
from llm_logparser.core.utils import format_display_path


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

    logger.info(_("runtime.export.input_jsonl", path=in_path))
    logger.info(
        _("runtime.export.output_md_split", path=format_display_path(out_md.parent))
        if args.split
        else _("runtime.export.output_md", path=format_display_path(out_md))
    )
    logger.info(_("runtime.export.timezone", timezone=args.timezone))
    logger.info(_("runtime.export.formatting", formatting=args.formatting))

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
        logger.info(_("runtime.export.preview_only"))
    else:
        if len(paths) == 1:
            logger.info(_("runtime.export.exported_one"))
        else:
            logger.info(_("runtime.export.exported_many", count=len(paths)))
