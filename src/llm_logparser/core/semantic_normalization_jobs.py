from __future__ import annotations

import json
import os
import shutil
import statistics
import time
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha1
from pathlib import Path
from typing import Any, Callable

from .analyzer_common import render_artifact_json, write_json_artifact
from .analyzer_semantic_prototype import derive_semantic_span_id
from .analyzer_common import detect_header_metadata
from .llm_client_protocol import LLMClient
from .message_window_reconstruction import (
    ReconstructedMessageWindow,
    load_reconstructed_message_windows,
    load_reconstructed_message_windows_from_parsed,
)
from .message_windows import (
    DEFAULT_MESSAGE_WINDOW_SIZE,
    DEFAULT_MESSAGE_WINDOW_STRIDE,
    resolve_message_window_stride,
)
from .ollama_client import OllamaClient
from .semantic_normalization import (
    RAW_LABEL_CONFIDENCE_THRESHOLD,
    SEED_TAXONOMY_VERSION,
    SemanticNormalizationResult,
    SemanticNormalizationRuntimeOptions,
    normalize_representative_span,
    semantic_normalization_prompt_hashes,
    semantic_normalization_runtime_options_to_dict,
    semantic_normalization_to_dict,
)
from .schema_validation import (
    load_semantic_normalization_job_config_validator,
    load_semantic_normalization_job_failure_validator,
    load_semantic_normalization_job_result_validator,
    load_semantic_normalization_job_span_validator,
)

JOB_CONFIG_SCHEMA_VERSION = "0.1"
JOB_RESULT_SCHEMA_VERSION = "0.1"
JOB_FAILURE_SCHEMA_VERSION = "0.1"
JOB_SPAN_SCHEMA_VERSION = "0.1"
JOB_ID_PREFIX = "snorm_"
JOB_ARTIFACT_TYPE = "semantic_normalization_job"
SPAN_RECORD_TYPE = "semantic_normalization_span"
RESULT_RECORD_TYPE = "semantic_normalization_result"
FAILURE_RECORD_TYPE = "semantic_normalization_failure"
JOB_PROGRESS_STATUSES = frozenset(
    {
        "queued",
        "running",
        "interrupted",
        "completed",
        "completed_with_failures",
        "failed_preflight",
    }
)
RETRYABLE_FAILURE_KINDS = frozenset(
    {"transport_error", "structured_output_error", "runtime_error", "write_error"}
)
WindowKey = tuple[str, str]
SpanKey = tuple[str, str, tuple[str, ...]]


class SemanticNormalizationJobError(RuntimeError):
    pass


@dataclass(frozen=True)
class ThreadInput:
    source_mode: str
    parsed_path: Path
    windows_path: Path | None
    provider_id: str | None
    conversation_id: str | None


@dataclass(frozen=True)
class SemanticNormalizationJobResults:
    job_dir: Path
    config: dict[str, Any]
    result_rows: list[dict[str, Any]]


def _utc_now_isoformat() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _safe_name(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in "._-" else "_"
        for character in value.casefold()
    ).strip("_") or "job"


def _default_job_id(model: str) -> str:
    return f"{JOB_ID_PREFIX}{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}_{_safe_name(model)}"


def _job_root(input_root: Path) -> Path:
    return input_root / "l3" / "semantic-normalization" / "jobs"


def _job_dir(input_root: Path, job_id: str) -> Path:
    return _job_root(input_root) / _safe_name(job_id)


def _config_path(job_dir: Path) -> Path:
    return job_dir / "config.json"


def _spans_path(job_dir: Path) -> Path:
    return job_dir / "spans.jsonl"


def _results_path(job_dir: Path) -> Path:
    return job_dir / "results.jsonl"


def _failures_path(job_dir: Path) -> Path:
    return job_dir / "failures.jsonl"


def _progress_path(job_dir: Path) -> Path:
    return job_dir / "progress.json"


def _summary_path(job_dir: Path) -> Path:
    return job_dir / "summary.json"


def _lock_path(job_dir: Path) -> Path:
    return job_dir / "job.lock"


def _text_sha1(text: str) -> str:
    return sha1(text.encode("utf-8")).hexdigest()


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    lower = int(index)
    upper = lower if lower == index else lower + 1
    if lower == upper:
        return ordered[lower]
    fraction = index - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> Path:
    return write_json_artifact(path, payload)


@lru_cache(maxsize=1)
def _job_config_validator():
    return load_semantic_normalization_job_config_validator()


@lru_cache(maxsize=1)
def _job_span_validator():
    return load_semantic_normalization_job_span_validator()


@lru_cache(maxsize=1)
def _job_result_validator():
    return load_semantic_normalization_job_result_validator()


@lru_cache(maxsize=1)
def _job_failure_validator():
    return load_semantic_normalization_job_failure_validator()


def _require_valid_payload(
    *,
    path: Path,
    payload: dict[str, Any],
    validator: Any,
    artifact_name: str,
    location: str | None = None,
) -> dict[str, Any]:
    errors = list(validator.iter_errors(payload))
    if not errors:
        return payload
    suffix = f":{location}" if location is not None else ""
    raise SemanticNormalizationJobError(
        f"{artifact_name} schema validation failed for {path}{suffix}: {errors[0].message}"
    )


def _load_job_config(job_dir: Path) -> dict[str, Any]:
    path = _config_path(job_dir)
    payload = _load_json(path)
    return _require_valid_payload(
        path=path,
        payload=payload,
        validator=_job_config_validator(),
        artifact_name="semantic normalization config",
    )


def _load_validated_jsonl_rows(
    path: Path,
    *,
    validator: Any,
    artifact_name: str,
) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SemanticNormalizationJobError(
                    f"invalid JSON in {path}:{line_no}: {exc.msg}"
                ) from exc
            if not isinstance(row, dict):
                raise SemanticNormalizationJobError(
                    f"invalid JSON object in {path}:{line_no}"
                )
            rows.append(
                _require_valid_payload(
                    path=path,
                    payload=row,
                    validator=validator,
                    artifact_name=artifact_name,
                    location=f"line {line_no}",
                )
            )
    return rows


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SemanticNormalizationJobError(f"missing required job file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SemanticNormalizationJobError(f"invalid JSON in {path}: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise SemanticNormalizationJobError(f"invalid JSON object in {path}")
    return payload


def _load_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SemanticNormalizationJobError(
                    f"invalid JSON in {path}:{line_no}: {exc.msg}"
                ) from exc
            if not isinstance(row, dict):
                raise SemanticNormalizationJobError(
                    f"invalid JSON object in {path}:{line_no}"
                )
            rows.append(row)
    return rows


def _append_jsonl_row(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _span_key(
    *,
    conversation_id: str,
    span_id: str,
    message_ids: list[str] | tuple[str, ...],
) -> SpanKey:
    return (
        conversation_id,
        span_id,
        tuple(str(message_id) for message_id in message_ids),
    )


def _classify_failure(exc: Exception) -> tuple[str, bool]:
    message = str(exc)
    lowered = message.casefold()
    if "structured llm response" in lowered or "invalid json after 2 attempts" in lowered:
        return "structured_output_error", True
    if "ollama request failed" in lowered or "missing 'response'" in lowered:
        return "transport_error", True
    if "schema validation failed" in lowered or "unknown message_id" in lowered:
        return "input_resolution_error", False
    return "runtime_error", True


def _discover_thread_inputs(input_root: Path) -> tuple[list[ThreadInput], list[str]]:
    provider_root = input_root.expanduser()
    if not provider_root.exists() or not provider_root.is_dir():
        raise SemanticNormalizationJobError(f"provider root not found: {provider_root}")

    windows_by_parent = {
        path.parent: path for path in sorted(provider_root.rglob("message_windows.jsonl"))
    }
    parsed_by_parent = {
        path.parent: path for path in sorted(provider_root.rglob("parsed.jsonl"))
    }

    inputs: list[ThreadInput] = []
    invalid: list[str] = []
    for parent in sorted(set(windows_by_parent) | set(parsed_by_parent)):
        windows_path = windows_by_parent.get(parent)
        parsed_path = parsed_by_parent.get(parent)
        if windows_path is not None and parsed_path is None:
            invalid.append(
                f"message_windows.jsonl without sibling parsed.jsonl: {windows_path}"
            )
            continue
        if parsed_path is None:
            continue
        provider_id, conversation_id = detect_header_metadata(parsed_path)
        inputs.append(
            ThreadInput(
                source_mode="stored_windows" if windows_path is not None else "derived_windows",
                parsed_path=parsed_path,
                windows_path=windows_path,
                provider_id=provider_id,
                conversation_id=conversation_id,
            )
        )
    return inputs, invalid


def _load_windows_for_thread(thread_input: ThreadInput) -> list[ReconstructedMessageWindow]:
    try:
        if thread_input.windows_path is not None:
            return load_reconstructed_message_windows(thread_input.windows_path)
        return load_reconstructed_message_windows_from_parsed(thread_input.parsed_path)
    except (FileNotFoundError, ValueError) as exc:
        raise SemanticNormalizationJobError(str(exc)) from exc


def _build_span_row(
    *,
    job_id: str,
    window: ReconstructedMessageWindow,
    source_mode: str,
    window_stride: int,
) -> dict[str, Any]:
    span_id = derive_semantic_span_id(
        provider_id=window.provider_id,
        conversation_id=window.conversation_id,
        message_ids=window.message_ids,
        window_id=window.window_id,
    )
    text = window.text
    return {
        "record_type": SPAN_RECORD_TYPE,
        "schema_version": JOB_SPAN_SCHEMA_VERSION,
        "job_id": job_id,
        "provider_id": window.provider_id,
        "source_mode": source_mode,
        "source_path": str(window.source_path.resolve()),
        "parsed_path": str(window.parsed_path.resolve()),
        "conversation_id": window.conversation_id,
        "span_id": span_id,
        "window_id": window.window_id,
        "message_ids": list(window.message_ids),
        "text_sha1": _text_sha1(text),
        "text_char_count": len(text),
        "message_count": window.message_count,
        "window_size": window.window_size,
        "window_stride": window_stride,
        "ts_start": window.ts_start,
        "ts_end": window.ts_end,
    }


def _build_worklist(
    *,
    input_root: Path,
    job_id: str,
) -> tuple[list[dict[str, Any]], dict[str, int], list[str]]:
    thread_inputs, invalid_threads = _discover_thread_inputs(input_root)
    if not thread_inputs:
        details = "\n".join(invalid_threads[:5])
        suffix = f"\n{details}" if details else ""
        raise SemanticNormalizationJobError(
            f"no usable threads found under: {input_root}{suffix}"
        )

    spans: list[dict[str, Any]] = []
    selected_input_counts = {
        "thread_count": len(thread_inputs),
        "message_windows_files": sum(
            1 for item in thread_inputs if item.source_mode == "stored_windows"
        ),
        "parsed_only_files": sum(
            1 for item in thread_inputs if item.source_mode == "derived_windows"
        ),
        "invalid_threads": len(invalid_threads),
    }
    for thread_input in thread_inputs:
        windows = _load_windows_for_thread(thread_input)
        for window in windows:
            spans.append(
                _build_span_row(
                    job_id=job_id,
                    window=window,
                    source_mode=thread_input.source_mode,
                    window_stride=resolve_message_window_stride(
                        window_size=window.window_size,
                        window_stride=window.window_stride,
                    ),
                )
            )
    if not spans:
        raise SemanticNormalizationJobError(
            f"no usable spans could be reconstructed under: {input_root}"
        )
    spans.sort(key=lambda row: (row["conversation_id"], row["window_id"]))
    return spans, selected_input_counts, invalid_threads


def _client(
    *,
    base_url: str,
    timeout_seconds: float,
) -> LLMClient:
    return OllamaClient(base_url=base_url, timeout=timeout_seconds)


def _load_worklist(job_dir: Path) -> list[dict[str, Any]]:
    return _load_validated_jsonl_rows(
        _spans_path(job_dir),
        validator=_job_span_validator(),
        artifact_name="semantic normalization spans",
    )


def _result_key(row: dict[str, Any]) -> SpanKey:
    return _span_key(
        conversation_id=str(row["conversation_id"]),
        span_id=str(row["span_id"]),
        message_ids=[str(message_id) for message_id in row["message_ids"]],
    )


def _success_index(job_dir: Path) -> dict[SpanKey, dict[str, Any]]:
    index: dict[SpanKey, dict[str, Any]] = {}
    for row in _load_validated_jsonl_rows(
        _results_path(job_dir),
        validator=_job_result_validator(),
        artifact_name="semantic normalization results",
    ):
        index[_result_key(row)] = row
    return index


def _failure_rows(job_dir: Path) -> list[dict[str, Any]]:
    return _load_validated_jsonl_rows(
        _failures_path(job_dir),
        validator=_job_failure_validator(),
        artifact_name="semantic normalization failures",
    )


def _active_failures(job_dir: Path) -> dict[SpanKey, dict[str, Any]]:
    successes = _success_index(job_dir)
    failures: dict[SpanKey, dict[str, Any]] = {}
    for row in _failure_rows(job_dir):
        key = _result_key(row)
        if key in successes:
            continue
        previous = failures.get(key)
        previous_attempt = int(previous["attempt_no"]) if previous is not None else -1
        attempt_no = int(row.get("attempt_no", 0))
        if attempt_no >= previous_attempt:
            failures[key] = row
    return failures


def _load_progress(job_dir: Path) -> dict[str, Any]:
    progress_path = _progress_path(job_dir)
    if not progress_path.exists():
        raise SemanticNormalizationJobError(f"missing required job file: {progress_path}")
    payload = _load_json(progress_path)
    status = payload.get("status")
    if not isinstance(status, str) or status not in JOB_PROGRESS_STATUSES:
        raise SemanticNormalizationJobError(f"invalid job status in {progress_path}")
    return payload


def _job_status(job_dir: Path) -> str:
    progress = _load_progress(job_dir)
    status = str(progress["status"])
    if status == "running" and not _lock_is_live(job_dir):
        return "interrupted"
    return status


def _build_progress(
    *,
    job_id: str,
    status: str,
    total_spans: int,
    success_count: int,
    failure_count: int,
    retryable_failure_count: int,
    created_at: str,
    started_at: str | None,
    completed_at: str | None,
    current_span: dict[str, str] | None,
) -> dict[str, Any]:
    processed_count = success_count + failure_count
    pending_count = max(total_spans - processed_count, 0)
    return {
        "job_id": job_id,
        "status": status,
        "created_at": created_at,
        "started_at": started_at,
        "updated_at": _utc_now_isoformat(),
        "completed_at": completed_at,
        "total_spans": total_spans,
        "processed_count": processed_count,
        "success_count": success_count,
        "failure_count": failure_count,
        "pending_count": pending_count,
        "retryable_failure_count": retryable_failure_count,
        "current_span": current_span,
    }


def _update_progress(
    job_dir: Path,
    *,
    status: str,
    current_span: dict[str, str] | None,
    created_at: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
) -> dict[str, Any]:
    config = _load_job_config(job_dir)
    total_spans = int(config["worklist"]["total_spans"])
    successes = _success_index(job_dir)
    active_failures = _active_failures(job_dir)
    progress = _build_progress(
        job_id=str(config["job_id"]),
        status=status,
        total_spans=total_spans,
        success_count=len(successes),
        failure_count=len(active_failures),
        retryable_failure_count=sum(
            1
            for row in active_failures.values()
            if bool(row.get("retriable"))
        ),
        created_at=created_at or str(config["created_at"]),
        started_at=started_at,
        completed_at=completed_at,
        current_span=current_span,
    )
    _atomic_write_json(_progress_path(job_dir), progress)
    return progress


def _latencies(job_dir: Path) -> list[float]:
    values: list[float] = []
    for row in _load_jsonl_rows(_results_path(job_dir)):
        latency_ms = row.get("latency_ms")
        if isinstance(latency_ms, (int, float)):
            values.append(float(latency_ms))
    return values


def _build_summary(job_dir: Path) -> dict[str, Any]:
    config = _load_job_config(job_dir)
    progress = _load_progress(job_dir)
    statuses = {
        "mapped": 0,
        "needs_review": 0,
        "taxonomy_gap": 0,
        "unmapped": 0,
    }
    results = list(_success_index(job_dir).values())
    for row in results:
        status = row.get("mapping_status")
        if isinstance(status, str) and status in statuses:
            statuses[status] += 1
    latencies = _latencies(job_dir)
    total_successes = len(results)
    summary = {
        "artifact_type": JOB_ARTIFACT_TYPE,
        "schema_version": JOB_CONFIG_SCHEMA_VERSION,
        "job_id": config["job_id"],
        "status": _job_status(job_dir),
        "created_at": config["created_at"],
        "started_at": progress.get("started_at"),
        "updated_at": _utc_now_isoformat(),
        "completed_at": progress.get("completed_at"),
        "input_root": config["input_root"],
        "output_dir": config["output_dir"],
        "model": config["normalization"]["model"],
        "temperature": config["normalization"]["temperature"],
        "counts": {
            "total_spans": config["worklist"]["total_spans"],
            "processed_count": progress["processed_count"],
            "success_count": progress["success_count"],
            "failure_count": progress["failure_count"],
            "pending_count": progress["pending_count"],
            "retryable_failure_count": progress["retryable_failure_count"],
            "mapped_count": statuses["mapped"],
            "needs_review_count": statuses["needs_review"],
            "taxonomy_gap_count": statuses["taxonomy_gap"],
            "unmapped_count": statuses["unmapped"],
        },
        "rates": {
            "mapped_rate": round(statuses["mapped"] / total_successes, 4)
            if total_successes
            else None,
            "needs_review_rate": round(statuses["needs_review"] / total_successes, 4)
            if total_successes
            else None,
            "taxonomy_gap_rate": round(statuses["taxonomy_gap"] / total_successes, 4)
            if total_successes
            else None,
            "unmapped_rate": round(statuses["unmapped"] / total_successes, 4)
            if total_successes
            else None,
        },
        "latency_ms": {
            "median": round(statistics.median(latencies), 3) if latencies else None,
            "p95": round(_percentile(latencies, 0.95), 3) if latencies else None,
        },
        "normalization": config["normalization"],
    }
    _atomic_write_json(_summary_path(job_dir), summary)
    return summary


def _render_summary_text(summary: dict[str, Any]) -> str:
    counts = summary["counts"]
    rates = summary["rates"]
    latency = summary["latency_ms"]
    lines = [
        f"Job: {summary['job_id']}",
        f"Status: {summary['status']}",
        f"Input: {summary['input_root']}",
        f"Model: {summary['model']}",
        f"Temperature: {summary['temperature']}",
        f"Spans: total={counts['total_spans']} processed={counts['processed_count']} success={counts['success_count']} failure={counts['failure_count']} pending={counts['pending_count']}",
        f"Mapping: mapped={counts['mapped_count']} ({rates['mapped_rate']}) needs_review={counts['needs_review_count']} ({rates['needs_review_rate']}) taxonomy_gap={counts['taxonomy_gap_count']} ({rates['taxonomy_gap_rate']}) unmapped={counts['unmapped_count']} ({rates['unmapped_rate']})",
        f"Retryable failures: {counts['retryable_failure_count']}",
        f"Latency ms: median={latency['median']} p95={latency['p95']}",
    ]
    return "\n".join(lines)


def _render_status_text(progress: dict[str, Any]) -> str:
    lines = [
        f"Job: {progress['job_id']}",
        f"Status: {progress['status']}",
        f"Counts: total={progress['total_spans']} processed={progress['processed_count']} success={progress['success_count']} failure={progress['failure_count']} pending={progress['pending_count']}",
        f"Retryable failures: {progress['retryable_failure_count']}",
    ]
    current_span = progress.get("current_span")
    if isinstance(current_span, dict) and current_span:
        lines.append(
            f"Current span: {current_span.get('conversation_id')} / {current_span.get('span_id')}"
        )
    return "\n".join(lines)


def _is_process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _lock_is_live(job_dir: Path) -> bool:
    path = _lock_path(job_dir)
    if not path.exists():
        return False
    try:
        payload = _load_json(path)
    except SemanticNormalizationJobError:
        return False
    pid = payload.get("pid")
    return isinstance(pid, int) and _is_process_alive(pid)


class _JobLock:
    def __init__(self, job_dir: Path) -> None:
        self._job_dir = job_dir
        self._path = _lock_path(job_dir)

    def __enter__(self) -> "_JobLock":
        if self._path.exists():
            if _lock_is_live(self._job_dir):
                raise SemanticNormalizationJobError(
                    f"job is already running: {self._job_dir.name}"
                )
            self._path.unlink(missing_ok=True)
        payload = {
            "pid": os.getpid(),
            "created_at": _utc_now_isoformat(),
        }
        fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, render_artifact_json(payload).encode("utf-8"))
        finally:
            os.close(fd)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._path.unlink(missing_ok=True)


def _span_rows_by_key(rows: list[dict[str, Any]]) -> dict[SpanKey, dict[str, Any]]:
    return {_result_key(row): row for row in rows}


def _validate_resume_inputs(job_dir: Path, target_keys: set[SpanKey]) -> None:
    config = _load_job_config(job_dir)
    input_root = Path(str(config["input_root"]))
    current_rows, _selected_counts, _invalid = _build_worklist(
        input_root=input_root,
        job_id=str(config["job_id"]),
    )
    current_by_key = _span_rows_by_key(current_rows)
    frozen_by_key = _span_rows_by_key(_load_worklist(job_dir))
    for key in sorted(target_keys):
        frozen = frozen_by_key.get(key)
        current = current_by_key.get(key)
        if frozen is None or current is None:
            raise SemanticNormalizationJobError(
                f"input drift detected for pending span: {key[1]}"
            )
        if frozen["text_sha1"] != current["text_sha1"]:
            raise SemanticNormalizationJobError(
                f"input drift detected for pending span: {key[1]}"
            )


def _work_items_pending(job_dir: Path) -> list[dict[str, Any]]:
    worklist = _load_worklist(job_dir)
    successes = _success_index(job_dir)
    failures = _active_failures(job_dir)
    return [
        row
        for row in worklist
        if _result_key(row) not in successes and _result_key(row) not in failures
    ]


def _work_items_failed(
    job_dir: Path,
    *,
    span_ids: list[str] | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    worklist = _load_worklist(job_dir)
    worklist_by_key = _span_rows_by_key(worklist)
    failures = _active_failures(job_dir)
    keys = list(failures.keys())
    if span_ids:
        requested = set(span_ids)
        keys = [key for key in keys if key[1] in requested]
    keys.sort(key=lambda key: (key[0], key[1]))
    if limit is not None:
        keys = keys[:limit]
    return [worklist_by_key[key] for key in keys if key in worklist_by_key]


def _attempt_no(job_dir: Path, key: SpanKey) -> int:
    highest = 0
    for row in _failure_rows(job_dir):
        if _result_key(row) != key:
            continue
        attempt_no = row.get("attempt_no")
        if isinstance(attempt_no, int) and attempt_no > highest:
            highest = attempt_no
    return highest + 1


def _current_text(row: dict[str, Any]) -> str:
    source_mode = str(row["source_mode"])
    parsed_path = Path(str(row["parsed_path"]))
    windows_path = Path(str(row["source_path"])) if source_mode == "stored_windows" else None
    if source_mode == "stored_windows":
        if windows_path is None:
            raise SemanticNormalizationJobError(
                f"missing message_windows source for stored span: {row['span_id']}"
            )
        windows = load_reconstructed_message_windows(windows_path)
    else:
        windows = load_reconstructed_message_windows_from_parsed(parsed_path)
    lookup: dict[WindowKey, ReconstructedMessageWindow] = {
        (window.conversation_id, window.window_id): window for window in windows
    }
    key = (str(row["conversation_id"]), str(row["window_id"]))
    record = lookup.get(key)
    if record is None:
        raise SemanticNormalizationJobError(
            f"input drift detected for pending span: {row['span_id']}"
        )
    return record.text


def _normalize_row(
    *,
    client: LLMClient,
    model: str,
    runtime_options: SemanticNormalizationRuntimeOptions,
    row: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        text = _current_text(row)
        if _text_sha1(text) != str(row["text_sha1"]):
            raise SemanticNormalizationJobError(
                f"input drift detected for pending span: {row['span_id']}"
            )
        started = time.perf_counter()
        result = normalize_representative_span(
            client=client,
            model=model,
            conversation_id=str(row["conversation_id"]),
            span_id=str(row["span_id"]),
            window_id=str(row["window_id"]) if row.get("window_id") is not None else None,
            message_ids=[str(message_id) for message_id in row["message_ids"]],
            text=text,
            runtime_options=runtime_options,
        )
        latency_ms = round((time.perf_counter() - started) * 1000.0, 3)
        return _success_row(
            job_id=str(row["job_id"]),
            row=row,
            result=result,
            latency_ms=latency_ms,
        ), None
    except SemanticNormalizationJobError as exc:
        return None, _failure_row(
            job_id=str(row["job_id"]),
            row=row,
            error_kind="input_resolution_error",
            error_message=str(exc),
            retriable=False,
            attempt_no=1,
        )
    except Exception as exc:
        error_kind, retriable = _classify_failure(exc)
        return None, _failure_row(
            job_id=str(row["job_id"]),
            row=row,
            error_kind=error_kind,
            error_message=str(exc),
            retriable=retriable,
            attempt_no=1,
        )


def _success_row(
    *,
    job_id: str,
    row: dict[str, Any],
    result: SemanticNormalizationResult,
    latency_ms: float,
) -> dict[str, Any]:
    payload = semantic_normalization_to_dict(result)
    return {
        "record_type": RESULT_RECORD_TYPE,
        "schema_version": JOB_RESULT_SCHEMA_VERSION,
        "job_id": job_id,
        "provider_id": row["provider_id"],
        "conversation_id": row["conversation_id"],
        "span_id": row["span_id"],
        "window_id": row["window_id"],
        "message_ids": list(row["message_ids"]),
        "text_sha1": row["text_sha1"],
        "raw_label": payload["raw_label"],
        "normalized_label": payload["normalized_label"],
        "mapping_status": payload["mapping_status"],
        "confidence": payload["confidence"],
        "method": payload["method"],
        "latency_ms": latency_ms,
        "completed_at": _utc_now_isoformat(),
    }


def _failure_row(
    *,
    job_id: str,
    row: dict[str, Any],
    error_kind: str,
    error_message: str,
    retriable: bool,
    attempt_no: int,
) -> dict[str, Any]:
    return {
        "record_type": FAILURE_RECORD_TYPE,
        "schema_version": JOB_FAILURE_SCHEMA_VERSION,
        "job_id": job_id,
        "provider_id": row["provider_id"],
        "conversation_id": row["conversation_id"],
        "span_id": row["span_id"],
        "window_id": row["window_id"],
        "message_ids": list(row["message_ids"]),
        "text_sha1": row["text_sha1"],
        "attempt_no": attempt_no,
        "error_kind": error_kind,
        "error_message": error_message,
        "retriable": retriable,
        "failed_at": _utc_now_isoformat(),
    }


def _write_processing_result(
    job_dir: Path,
    *,
    success_row: dict[str, Any] | None,
    failure_row: dict[str, Any] | None,
) -> None:
    try:
        if success_row is not None:
            _require_valid_payload(
                path=_results_path(job_dir),
                payload=success_row,
                validator=_job_result_validator(),
                artifact_name="semantic normalization results",
            )
            _append_jsonl_row(_results_path(job_dir), success_row)
        elif failure_row is not None:
            _require_valid_payload(
                path=_failures_path(job_dir),
                payload=failure_row,
                validator=_job_failure_validator(),
                artifact_name="semantic normalization failures",
            )
            _append_jsonl_row(_failures_path(job_dir), failure_row)
    except Exception as exc:
        raise SemanticNormalizationJobError(str(exc)) from exc


def _process_rows(
    job_dir: Path,
    *,
    rows: list[dict[str, Any]],
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    config = _load_job_config(job_dir)
    runtime_options = SemanticNormalizationRuntimeOptions(
        temperature=float(config["normalization"]["temperature"]),
        raw_num_predict=int(config["normalization"]["raw_num_predict"]),
        mapping_num_predict=int(config["normalization"]["mapping_num_predict"]),
    )
    client = _client(
        base_url=str(config["normalization"]["base_url"]),
        timeout_seconds=float(config["normalization"]["timeout_seconds"]),
    )
    created_at = str(config["created_at"])
    started_at = _utc_now_isoformat()
    _update_progress(
        job_dir,
        status="running",
        current_span=None,
        created_at=created_at,
        started_at=started_at,
        completed_at=None,
    )
    with _JobLock(job_dir):
        for index, row in enumerate(rows, start=1):
            current_span = {
                "conversation_id": str(row["conversation_id"]),
                "span_id": str(row["span_id"]),
            }
            if progress is not None:
                progress(
                    f"[{index}/{len(rows)}] {row['conversation_id']} / {row['span_id']}"
                )
            _update_progress(
                job_dir,
                status="running",
                current_span=current_span,
                created_at=created_at,
                started_at=started_at,
                completed_at=None,
            )
            key = _result_key(row)
            if key in _success_index(job_dir):
                continue
            success_row, failure_row = _normalize_row(
                client=client,
                model=str(config["normalization"]["model"]),
                runtime_options=runtime_options,
                row=row,
            )
            if failure_row is not None:
                failure_row["attempt_no"] = _attempt_no(job_dir, key)
            try:
                _write_processing_result(
                    job_dir,
                    success_row=success_row,
                    failure_row=failure_row,
                )
            except SemanticNormalizationJobError as exc:
                write_failure = _failure_row(
                    job_id=str(config["job_id"]),
                    row=row,
                    error_kind="write_error",
                    error_message=str(exc),
                    retriable=True,
                    attempt_no=_attempt_no(job_dir, key),
                )
                _append_jsonl_row(_failures_path(job_dir), write_failure)
        final_status = (
            "completed_with_failures" if _active_failures(job_dir) else "completed"
        )
        _update_progress(
            job_dir,
            status=final_status,
            current_span=None,
            created_at=created_at,
            started_at=started_at,
            completed_at=_utc_now_isoformat(),
        )
    return _build_summary(job_dir)


def _build_config(
    *,
    job_id: str,
    input_root: Path,
    output_dir: Path,
    model: str,
    base_url: str,
    timeout_seconds: float,
    runtime_options: SemanticNormalizationRuntimeOptions,
    selected_input_counts: dict[str, int],
    total_spans: int,
) -> dict[str, Any]:
    return {
        "artifact_type": JOB_ARTIFACT_TYPE,
        "schema_version": JOB_CONFIG_SCHEMA_VERSION,
        "job_id": job_id,
        "created_at": _utc_now_isoformat(),
        "input_root": str(input_root.resolve()),
        "output_dir": str(output_dir.resolve()),
        "source_strategy": {
            "mode": "message_windows_preferred_with_parsed_fallback",
            "window_size": DEFAULT_MESSAGE_WINDOW_SIZE,
            "window_stride": resolve_message_window_stride(
                window_size=DEFAULT_MESSAGE_WINDOW_SIZE,
                window_stride=DEFAULT_MESSAGE_WINDOW_STRIDE,
            ),
        },
        "selected_inputs": selected_input_counts,
        "normalization": {
            "model": model,
            "base_url": base_url,
            "timeout_seconds": timeout_seconds,
            **semantic_normalization_runtime_options_to_dict(runtime_options),
            "taxonomy_version": SEED_TAXONOMY_VERSION,
            "confidence_threshold": RAW_LABEL_CONFIDENCE_THRESHOLD,
        },
        "prompt_provenance": semantic_normalization_prompt_hashes(),
        "worklist": {
            "total_spans": total_spans,
            "span_key_kind": "conversation_id+span_id+ordered_message_ids",
        },
    }


def run_semantic_normalization_job(
    input_root: Path,
    *,
    model: str,
    base_url: str = "http://localhost:11434",
    timeout_seconds: float = 120.0,
    temperature: float = 0.0,
    raw_num_predict: int = 180,
    mapping_num_predict: int = 160,
    job_id: str | None = None,
    overwrite: bool = False,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    normalized_model = model.strip()
    if not normalized_model:
        raise SemanticNormalizationJobError("--model is required")
    runtime_options = SemanticNormalizationRuntimeOptions(
        temperature=temperature,
        raw_num_predict=raw_num_predict,
        mapping_num_predict=mapping_num_predict,
    )
    normalized_job_id = _safe_name(job_id or _default_job_id(normalized_model))
    job_dir = _job_dir(input_root, normalized_job_id)
    if job_dir.exists():
        if _lock_is_live(job_dir):
            raise SemanticNormalizationJobError(
                f"job is already running: {job_dir.name}"
            )
        if not overwrite:
            raise SemanticNormalizationJobError(
                f"job already exists: {job_dir} (rerun with --overwrite)"
            )
    spans, selected_input_counts, invalid_threads = _build_worklist(
        input_root=input_root,
        job_id=normalized_job_id,
    )
    if job_dir.exists():
        shutil.rmtree(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    config = _build_config(
        job_id=normalized_job_id,
        input_root=input_root,
        output_dir=job_dir,
        model=normalized_model,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        runtime_options=runtime_options,
        selected_input_counts=selected_input_counts,
        total_spans=len(spans),
    )
    _require_valid_payload(
        path=_config_path(job_dir),
        payload=config,
        validator=_job_config_validator(),
        artifact_name="semantic normalization config",
    )
    _atomic_write_json(_config_path(job_dir), config)
    with _spans_path(job_dir).open("w", encoding="utf-8") as handle:
        for row in spans:
            _require_valid_payload(
                path=_spans_path(job_dir),
                payload=row,
                validator=_job_span_validator(),
                artifact_name="semantic normalization spans",
            )
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    _results_path(job_dir).touch()
    _failures_path(job_dir).touch()
    _atomic_write_json(
        _progress_path(job_dir),
        _build_progress(
            job_id=normalized_job_id,
            status="queued",
            total_spans=len(spans),
            success_count=0,
            failure_count=0,
            retryable_failure_count=0,
            created_at=str(config["created_at"]),
            started_at=None,
            completed_at=None,
            current_span=None,
        ),
    )
    summary = _process_rows(job_dir, rows=spans, progress=progress)
    summary["invalid_threads"] = invalid_threads
    _atomic_write_json(_summary_path(job_dir), summary)
    return {
        "job_id": normalized_job_id,
        "job_dir": str(job_dir),
        "summary_path": str(_summary_path(job_dir)),
        "progress_path": str(_progress_path(job_dir)),
        "invalid_threads": invalid_threads,
        "summary": summary,
    }


def resume_semantic_normalization_job(
    input_root: Path,
    *,
    job_id: str,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    job_dir = _job_dir(input_root, job_id)
    if not job_dir.exists():
        raise SemanticNormalizationJobError(f"job not found: {job_dir}")
    pending = _work_items_pending(job_dir)
    if pending:
        _validate_resume_inputs(job_dir, {_result_key(row) for row in pending})
        summary = _process_rows(job_dir, rows=pending, progress=progress)
    else:
        summary = _build_summary(job_dir)
        _update_progress(
            job_dir,
            status=summary["status"],
            current_span=None,
            created_at=str(_load_json(_config_path(job_dir))["created_at"]),
            started_at=_load_progress(job_dir).get("started_at"),
            completed_at=_load_progress(job_dir).get("completed_at"),
        )
    return {
        "job_id": _load_job_config(job_dir)["job_id"],
        "job_dir": str(job_dir),
        "summary": summary,
    }


def retry_semantic_normalization_job_failures(
    input_root: Path,
    *,
    job_id: str,
    span_ids: list[str] | None = None,
    limit: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    job_dir = _job_dir(input_root, job_id)
    if not job_dir.exists():
        raise SemanticNormalizationJobError(f"job not found: {job_dir}")
    failed_rows = _work_items_failed(job_dir, span_ids=span_ids, limit=limit)
    if failed_rows:
        _validate_resume_inputs(job_dir, {_result_key(row) for row in failed_rows})
        summary = _process_rows(job_dir, rows=failed_rows, progress=progress)
    else:
        summary = _build_summary(job_dir)
    return {
        "job_id": _load_job_config(job_dir)["job_id"],
        "job_dir": str(job_dir),
        "retried_span_count": len(failed_rows),
        "summary": summary,
    }


def semantic_normalization_job_status(
    input_root: Path,
    *,
    job_id: str,
) -> dict[str, Any]:
    job_dir = _job_dir(input_root, job_id)
    if not job_dir.exists():
        raise SemanticNormalizationJobError(f"job not found: {job_dir}")
    progress = _load_progress(job_dir)
    progress["status"] = _job_status(job_dir)
    return progress


def semantic_normalization_job_summary(
    input_root: Path,
    *,
    job_id: str,
) -> dict[str, Any]:
    job_dir = _job_dir(input_root, job_id)
    if not job_dir.exists():
        raise SemanticNormalizationJobError(f"job not found: {job_dir}")
    summary = _build_summary(job_dir)
    summary["status"] = _job_status(job_dir)
    _atomic_write_json(_summary_path(job_dir), summary)
    return summary


def load_semantic_normalization_job_results(
    input_root: Path,
    *,
    job_id: str,
) -> SemanticNormalizationJobResults:
    job_dir = _job_dir(input_root, job_id)
    if not job_dir.exists():
        raise SemanticNormalizationJobError(f"job not found: {job_dir}")
    return SemanticNormalizationJobResults(
        job_dir=job_dir,
        config=_load_job_config(job_dir),
        result_rows=_load_validated_jsonl_rows(
            _results_path(job_dir),
            validator=_job_result_validator(),
            artifact_name="semantic normalization results",
        ),
    )


def render_semantic_normalization_job_status(
    input_root: Path,
    *,
    job_id: str,
    json_output: bool = False,
) -> str:
    progress = semantic_normalization_job_status(input_root, job_id=job_id)
    if json_output:
        return json.dumps(progress, ensure_ascii=False, indent=2)
    return _render_status_text(progress)


def render_semantic_normalization_job_summary(
    input_root: Path,
    *,
    job_id: str,
    json_output: bool = False,
) -> str:
    summary = semantic_normalization_job_summary(input_root, job_id=job_id)
    if json_output:
        return json.dumps(summary, ensure_ascii=False, indent=2)
    return _render_summary_text(summary)
