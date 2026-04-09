# src/llm_logparser/core/parser.py
from __future__ import annotations
import json
import importlib
import logging
from pathlib import Path
from typing import Any, Dict, Generator, Optional
from datetime import datetime
from inspect import signature

try:
    import ijson  # type: ignore
except Exception:  # pragma: no cover
    ijson = None

from .l1_derivation import ThreadMetrics, build_thread_stats_artifact
from .i18n import _
from .message_windows import render_message_windows_jsonl
from .provider_adapter_protocol import (
    ProviderAdapter,
    ProviderInputRecords,
    ProviderRecordExpander,
)
from .utils import format_display_path

# ============================================================
# 1. Error Classes
# ============================================================

class LLPError(Exception):
    """Base class for parser-related errors."""
    code = "LP2000"
    def __init__(self, msg: str, *, code: str | None = None):
        super().__init__(msg)
        self.code = code or self.code

class LLPInputError(LLPError):
    code = "LP2100"

class LLPAdapterError(LLPError):
    code = "LP2200"

class LLPWriteError(LLPError):
    code = "LP2300"


# ============================================================
# 2. Provider Adapter Loader
# ============================================================

def load_adapter(provider: str):
    """Dynamically load the provider adapter."""
    mod = importlib.import_module(f"llm_logparser.core.providers.{provider}.adapter")
    get_adapter = getattr(mod, "get_adapter", None)
    if not get_adapter:
        raise LLPAdapterError(f"adapter missing for provider={provider}")
    adapter: ProviderAdapter = get_adapter()
    get_record_expander = getattr(mod, "get_record_expander", None)
    record_expander: ProviderRecordExpander | None = None
    if callable(get_record_expander):
        try:
            record_expander = get_record_expander()
        except Exception:
            record_expander = None
    setattr(adapter, "__llp_record_expander__", record_expander)
    get_input_records = getattr(mod, "get_input_records", None)
    input_records: ProviderInputRecords | None = None
    if callable(get_input_records):
        try:
            input_records = get_input_records()
        except Exception:
            input_records = None
    setattr(adapter, "__llp_input_records__", input_records)
    manifest = getattr(mod, "get_manifest", lambda: {})()
    policy = getattr(mod, "get_policy", lambda: {})()
    return adapter, manifest, policy


def load_extractor(provider: str):
    """Dynamically load the provider extractor."""
    mod = importlib.import_module(f"llm_logparser.core.providers.{provider}.extractor")
    get_extractor = getattr(mod, "get_extractor", None)
    if not get_extractor:
        raise LLPAdapterError(f"extractor missing for provider={provider}")
    return get_extractor()


# ============================================================
# 3. JSON Stream Reader (Hybrid)
# ============================================================

def iter_json_records(path: Path, logger: logging.Logger) -> Generator[Dict[str, Any], None, None]:
    """
    Stream and read large JSON/JSONL files.
    - JSON arrays: sequential read with ijson (if available)
    - JSON objects: read as single item with json.load
    - JSONL/NDJSON: process line by line
    """
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            first = ""
            while True:
                ch = f.read(1)
                if ch == "":
                    break
                if not ch.isspace():
                    first = ch
                    break
            f.seek(0)

            # JSONL / NDJSON
            if first not in ("[", "{"):
                for i, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError as e:
                        logger.warning(
                            _("runtime.parser.skip_invalid_json_line", index=i, detail=e)
                        )
                        continue
                return

            # JSON array
            if first == "[":
                if ijson is not None:
                    for i, item in enumerate(ijson.items(f, "item"), start=1):
                        if not isinstance(item, dict):
                            logger.warning(_("runtime.parser.skip_invalid_element", index=i))
                            continue
                        yield item
                    return
                # fallback: load entire array (smaller files)
                data = json.load(f)
                if not isinstance(data, list):
                    raise LLPInputError("expected JSON array")
                for i, item in enumerate(data, start=1):
                    if not isinstance(item, dict):
                        logger.warning(_("runtime.parser.skip_invalid_element", index=i))
                        continue
                    yield item
                return

            # JSON object
            obj = json.load(f)
            if isinstance(obj, dict):
                yield obj
                return
            raise LLPInputError("expected JSON object at top-level")

    except FileNotFoundError:
        raise LLPInputError(f"input not found: {path}")
    except PermissionError:
        raise LLPInputError(f"permission denied: {path}")
    except Exception as e:
        raise LLPInputError(f"reader error: {e}")


def iter_input_records(
    path: Path,
    logger: logging.Logger,
) -> Generator[tuple[Any, str], None, None]:
    """Yield provider input records with their concrete source path."""
    target = path.expanduser()
    if target.is_dir():
        for child in sorted(
            candidate for candidate in target.rglob("*.json") if candidate.is_file()
        ):
            for record in iter_json_records(child, logger):
                yield record, str(child)
        return

    for record in iter_json_records(target, logger):
        yield record, str(target)


# ============================================================
# 4. Validation / Cache Utilities
# ============================================================

def _is_json_compatible(value: Any) -> bool:
    try:
        json.dumps(value, ensure_ascii=True)
    except (TypeError, ValueError):
        return False
    return True

def validate_message(msg: dict, *, fail_fast=False):
    """Basic schema validation."""
    required_str = ["conversation_id", "message_id", "role"]
    for k in required_str:
        if not isinstance(msg.get(k), str) or not msg.get(k):
            if fail_fast:
                raise LLPAdapterError(f"missing required field: {k}")
            return False

    parent_id = msg.get("parent_id")
    if parent_id is not None and not isinstance(parent_id, str):
        if fail_fast:
            raise LLPAdapterError("invalid parent_id type")
        return False

    ts = msg.get("ts")
    if not isinstance(ts, int):
        if fail_fast:
            raise LLPAdapterError("missing/invalid ts (expected epoch ms int)")
        return False

    if "content" not in msg:
        if fail_fast:
            raise LLPAdapterError("missing content")
        return False
    content = msg.get("content")
    if not _is_json_compatible(content):
        if fail_fast:
            raise LLPAdapterError("invalid content (expected JSON-compatible value)")
        return False

    if not isinstance(msg.get("text"), str):
        if fail_fast:
            raise LLPAdapterError("invalid text type")
        return False

    return True


def load_manifest_if_exists(provider_dir: Path) -> dict:
    """Load existing manifest to use as cache."""
    man_path = provider_dir / "manifest.json"
    if not man_path.exists():
        return {}
    try:
        return json.loads(man_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def should_skip_thread(conv_id: str, msgs: list, manifest_old: dict) -> bool:
    """Determine differential skipping by update_time, etc."""
    try:
        index = manifest_old.get("index", {}).get("threads", [])
        old = next((t for t in index if t["conversation_id"] == conv_id), None)
        if not old:
            return False
        old_count = old.get("count")
        new_count = len(msgs)
        if old_count == new_count:
            return True
    except Exception:
        pass
    return False


def write_thread_stats_artifact(
    outdir_thread: Path,
    *,
    provider: str,
    metrics: ThreadMetrics,
) -> None:
    """Persist cheap thread-local stats derived during the parse write loop."""
    artifact_path = outdir_thread / "thread_stats.json"
    tmp = artifact_path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(
            build_thread_stats_artifact(metrics, provider_id=provider),
            ensure_ascii=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    tmp.replace(artifact_path)


def write_message_windows_artifact(
    outdir_thread: Path,
    *,
    canonical_rows: list[dict[str, Any]],
    window_size: int,
    window_stride: int | None,
) -> None:
    """Persist deterministic message windows from canonical message rows."""
    artifact_path = outdir_thread / "message_windows.jsonl"
    tmp = artifact_path.with_suffix(".tmp")
    tmp.write_text(
        render_message_windows_jsonl(
            canonical_rows,
            window_size=window_size,
            window_stride=window_stride,
        ),
        encoding="utf-8",
    )
    tmp.replace(artifact_path)


def _invoke_adapter(
    adapter_func: ProviderAdapter,
    raw: dict,
    *,
    source: str,
    logger: logging.Logger | None = None,
) -> list[dict]:
    """Call provider adapter through the explicit provider boundary."""
    return list(adapter_func(raw, source=source, logger=logger))


def _iter_provider_input_records(
    adapter_func: ProviderAdapter,
    input_path: Path,
    logger: logging.Logger,
):
    """Call the provider input iterator and normalize tuple output."""
    input_records_func = getattr(adapter_func, "__llp_input_records__", None) or iter_input_records
    records_iter = input_records_func(input_path, logger)

    for item in records_iter:
        if (
            isinstance(item, tuple)
            and len(item) == 2
            and isinstance(item[1], str)
        ):
            yield item[0], item[1]
        else:
            yield item, str(input_path)


# ============================================================
# 5. Main Parser
# ============================================================

def parse_to_jsonl(
    provider: str,
    input_path: Path,
    outdir: Path,
    *,
    dry_run: bool = False,
    fail_fast: bool = False,
    logger: Optional[logging.Logger] = None,
    progress_interval: int = 100,
    validate_schema: bool = False,
    schema_validator: "MessageSchemaValidator" | None = None,
    message_window_size: int = 4,
    message_window_stride: int | None = None,
) -> Dict[str, Any]:
    """
    Parse the exported JSON for each provider and generate thread-level JSONL files.
    Stop on a certain number of errors if fail_fast=True.
    """
    log = logger or logging.getLogger("llm_logparser.parser")
    log.info(
        _("runtime.parser.start_parse", provider=provider, dry_run=dry_run, fail_fast=fail_fast)
    )

    adapter_func, _manifest, policy = load_adapter(provider)
    provider_dir = outdir / provider
    provider_dir.mkdir(parents=True, exist_ok=True)
    manifest_old = load_manifest_if_exists(provider_dir)

    if validate_schema and schema_validator is None:
        from .schema_validation import MessageSchemaValidator

        schema_validator = MessageSchemaValidator()
    message_validation_error_cls = None
    if schema_validator:
        from .schema_validation import MessageValidationError

        message_validation_error_cls = MessageValidationError

    errors, skipped, count = 0, 0, 0
    sample_errors: list[str] = []
    stats = {"threads": 0, "messages": 0}
    manifest_index = []
    record_expander = getattr(adapter_func, "__llp_record_expander__", None)
    if record_expander is None:
        record_expander = lambda raw: [raw]

    for raw, raw_source in _iter_provider_input_records(adapter_func, input_path, log):
        try:
            expanded_records = list(record_expander(raw))
        except Exception as e:
            msg = _("runtime.parser.adapter_error", detail=e)
            log.warning(msg)
            errors += 1
            if len(sample_errors) < 5:
                sample_errors.append(msg)
            if fail_fast and errors > 3:
                raise LLPAdapterError(f"too many adapter errors ({errors})")
            continue

        for expanded_raw in expanded_records:
            try:
                recs = _invoke_adapter(
                    adapter_func,
                    expanded_raw,
                    source=raw_source,
                    logger=log,
                )
                if not recs:
                    continue

                cid = recs[0].get("conversation_id")
                if not cid:
                    skipped += len(recs)
                    continue

                count += len(recs)
                if count % progress_interval == 0:
                    log.info(_("runtime.parser.processed_messages", count=count))

                recs.sort(key=lambda r: (r.get("ts") is None, r.get("ts")))

                if should_skip_thread(cid, recs, manifest_old):
                    skipped += 1
                    log.info(_("runtime.parser.skip_thread_unchanged", conversation_id=cid))
                    continue

                ts_values = [m.get("ts") for m in recs if isinstance(m.get("ts"), (int, float))]
                ts_min = min(ts_values) if ts_values else None
                ts_max = max(ts_values) if ts_values else None

                outdir_thread = provider_dir / f"thread-{cid}"
                outdir_thread.mkdir(parents=True, exist_ok=True)
                outpath = outdir_thread / "parsed.jsonl"

                if not dry_run:
                    tmp = outpath.with_suffix(".tmp")
                    thread_metrics = ThreadMetrics(conversation_id=cid)
                    canonical_rows: list[dict[str, Any]] = []
                    try:
                        with tmp.open("w", encoding="utf-8") as f:
                            thread_meta = {
                                "record_type": "thread",
                                "provider_id": provider,
                                "conversation_id": cid,
                                "message_count": len(recs),
                            }
                            f.write(json.dumps(thread_meta, ensure_ascii=True) + "\n")
                            for m in recs:
                                if not validate_message(m, fail_fast=fail_fast):
                                    skipped += 1
                                    continue
                                canonical_row = {
                                    "record_type": "message",
                                    "provider_id": provider,
                                    **m,
                                }
                                if schema_validator:
                                    try:
                                        schema_validator.validate_message(canonical_row)
                                    except message_validation_error_cls as verr:
                                        idx = canonical_row.get("message_id") or "<unknown>"
                                        log.warning(
                                            _(
                                                "runtime.parser.schema_validation_failed",
                                                conversation_id=cid,
                                                message_id=idx,
                                                detail=verr,
                                            )
                                        )
                                        skipped += 1
                                        if fail_fast:
                                            raise LLPAdapterError(
                                                "message schema validation failed"
                                            ) from verr
                                        continue
                                # Keep derived artifacts tied to the exact canonical rows we write.
                                canonical_rows.append(canonical_row)
                                thread_metrics.add_message(canonical_row)
                                f.write(
                                    json.dumps(
                                        canonical_row,
                                        ensure_ascii=True,
                                    )
                                    + "\n"
                                )
                        write_thread_stats_artifact(
                            outdir_thread,
                            provider=provider,
                            metrics=thread_metrics,
                        )
                        write_message_windows_artifact(
                            outdir_thread,
                            canonical_rows=canonical_rows,
                            window_size=message_window_size,
                            window_stride=message_window_stride,
                        )
                    except Exception as e:
                        raise LLPWriteError(f"write error: {e}")
                    tmp.replace(outpath)

                stats["threads"] += 1
                stats["messages"] += len(recs)

                manifest_index.append(
                    {
                        "conversation_id": cid,
                        "path": f"thread-{cid}/parsed.jsonl",
                        "count": len(recs),
                        "ts_min": ts_min,
                        "ts_max": ts_max,
                    }
                )
            except Exception as e:
                msg = _("runtime.parser.adapter_error", detail=e)
                log.warning(msg)
                errors += 1
                if len(sample_errors) < 5:
                    sample_errors.append(msg)
                if fail_fast and errors > 3:
                    raise LLPAdapterError(f"too many adapter errors ({errors})")

    # Output manifest
    if not dry_run:
        manifest_path = provider_dir / "manifest.json"
        manifest_obj = {
            "schema_version": "1.3",
            "provider": provider,
            "policy": policy,
            "exported_at": datetime.utcnow().isoformat(),
            "index": {"threads": manifest_index},
        }
        manifest_path.write_text(json.dumps(manifest_obj, ensure_ascii=True, indent=2), encoding="utf-8")
        log.info(_("runtime.parser.manifest_saved", path=format_display_path(manifest_path)))

    log.info(
        _(
            "runtime.parser.summary",
            threads=stats["threads"],
            messages=stats["messages"],
            errors=errors,
            skipped=skipped,
        )
    )
    return {**stats, "errors": errors, "skipped": skipped, "samples": sample_errors}


def extract_to_json(
    provider: str,
    input_path: Path,
    outdir: Path,
    conversation_id: str,
    *,
    dry_run: bool = False,
    sanitize_policy: Any | None = None,
    logger: Optional[logging.Logger] = None,
) -> Dict[str, Any]:
    """Extract raw conversation by conversation_id and output Gemini-compatible JSON."""
    log = logger or logging.getLogger("llm_logparser.parser")
    log.info(
        _(
            "runtime.parser.start_extract",
            provider=provider,
            conversation_id=conversation_id,
            dry_run=dry_run,
        )
    )
    extractor = load_extractor(provider)
    extractor_kwargs: dict[str, Any] = {
        "input_path": input_path,
        "outdir": outdir,
        "provider": provider,
        "conversation_id": conversation_id,
        "dry_run": dry_run,
        "logger": log,
    }
    if "sanitize_policy" in signature(extractor).parameters:
        extractor_kwargs["sanitize_policy"] = sanitize_policy
    elif sanitize_policy is not None:
        log.debug(
            "Extractor for provider=%s does not accept sanitize_policy; using extractor defaults",
            provider,
        )
    result = extractor(**extractor_kwargs)
    if not isinstance(result, dict):
        raise LLPAdapterError("extractor returned invalid result")
    return result
