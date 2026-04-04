from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from llm_logparser.core.i18n import _
from llm_logparser.core.parser import LLPInputError, iter_json_records
from llm_logparser.core.providers.openai.chatgpt.utils import json_safe
from llm_logparser.core.sanitize import SanitizePolicy, sanitize_value
from llm_logparser.core.utils import format_display_path


def _conversation_id_of(record: dict[str, Any]) -> str | None:
    for key in ("conversation_id", "id", "uuid"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def get_extractor():
    def extract(
        *,
        input_path: Path,
        outdir: Path,
        provider: str,
        conversation_id: str,
        dry_run: bool = False,
        sanitize_policy: SanitizePolicy | None = None,
        logger: logging.Logger | None = None,
    ) -> dict[str, Any]:
        log = logger or logging.getLogger("llm_logparser.extractor")
        policy = sanitize_policy or SanitizePolicy.defaults()
        if policy.enabled and policy.custom_mask_patterns:
            log.info(_("runtime.openai_extract.custom_mask_patterns"))

        matched_record: dict[str, Any] | None = None
        scanned = 0
        for raw in iter_json_records(input_path, log):
            scanned += 1
            if not isinstance(raw, dict):
                continue
            if _conversation_id_of(raw) == conversation_id:
                matched_record = raw
                break

        if matched_record is None:
            raise LLPInputError(f"conversation not found: {conversation_id}")

        sanitized = json_safe(sanitize_value(matched_record, policy))
        thread_dir = outdir / provider / f"thread-{conversation_id}"
        out_path = thread_dir / "extract.json"
        meta_path = thread_dir / "extract.meta.json"
        sanitize_meta = policy.summary()
        metadata = {
            "provider": provider,
            "conversation_id": conversation_id,
            "records_scanned": scanned,
            "sanitize": sanitize_meta,
        }

        if dry_run:
            log.info(
                _(
                    "runtime.openai_extract.dry_run_skip",
                    conversation_id=conversation_id,
                    path=format_display_path(out_path),
                )
            )
            return {
                "conversation_id": conversation_id,
                "records_scanned": scanned,
                "path": str(out_path),
                "meta_path": str(meta_path),
                "sanitize": sanitize_meta,
                "written": False,
            }

        thread_dir.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as f:
            json.dump([sanitized], f, ensure_ascii=False, indent=2)
            f.write("\n")
        meta_path.write_text(
            json.dumps(metadata, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        log.info(_("runtime.openai_extract.wrote", path=format_display_path(out_path)))
        return {
            "conversation_id": conversation_id,
            "records_scanned": scanned,
            "path": str(out_path),
            "meta_path": str(meta_path),
            "sanitize": sanitize_meta,
            "written": True,
        }

    return extract
