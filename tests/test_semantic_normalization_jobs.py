from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from llm_logparser.cli.cli import main
from llm_logparser.core.semantic_normalization import (
    MappingStatus,
    SemanticNormalizationMethod,
    SemanticNormalizationResult,
    semantic_normalization_prompt_provenance,
)
from llm_logparser.core.semantic_normalization_jobs import (
    SemanticNormalizationJobError,
    load_semantic_normalization_job_results,
    render_semantic_normalization_job_compare,
    render_semantic_normalization_job_status,
    render_semantic_normalization_job_summary,
    resume_semantic_normalization_job,
    retry_semantic_normalization_job_failures,
    run_semantic_normalization_job,
)
from llm_logparser.core.schema_validation import (
    load_semantic_normalization_job_config_validator,
    load_semantic_normalization_job_failure_validator,
    load_semantic_normalization_job_result_validator,
    load_semantic_normalization_job_span_validator,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def _write_parsed_thread(
    thread_dir: Path,
    *,
    provider_id: str,
    conversation_id: str,
    messages: list[dict],
) -> Path:
    parsed_path = thread_dir / "parsed.jsonl"
    rows = [
        {
            "record_type": "thread",
            "provider_id": provider_id,
            "conversation_id": conversation_id,
            "message_count": len(messages),
        },
        *messages,
    ]
    _write_jsonl(parsed_path, rows)
    return parsed_path


def _message(
    conversation_id: str,
    message_id: str,
    *,
    role: str,
    text: str,
    ts: int,
    provider_id: str = "openai",
) -> dict:
    return {
        "record_type": "message",
        "provider_id": provider_id,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "role": role,
        "ts": ts,
        "text": text,
        "content": {"content_type": "text", "parts": [text]},
    }


def _window_row(
    conversation_id: str,
    window_id: str,
    *,
    message_ids: list[str],
    char_count: int,
    provider_id: str = "openai",
    ts_start: int = 1,
    ts_end: int = 2,
) -> dict:
    return {
        "record_type": "message_window",
        "schema_version": "3.0",
        "provider_id": provider_id,
        "conversation_id": conversation_id,
        "window_id": window_id,
        "message_ids": message_ids,
        "char_count": char_count,
        "ts_start": ts_start,
        "ts_end": ts_end,
        "window_size": len(message_ids),
        "window_stride": len(message_ids),
    }


def _fake_result(
    *,
    conversation_id: str,
    span_id: str,
    window_id: str | None,
    message_ids: list[str],
    raw_label: str = "request",
    normalized_label: str | None = "request",
    mapping_status: MappingStatus = "mapped",
    confidence: float | None = 0.91,
) -> SemanticNormalizationResult:
    return SemanticNormalizationResult(
        conversation_id=conversation_id,
        span_id=span_id,
        window_id=window_id,
        message_ids=message_ids,
        unit_kind="representative_span",
        raw_label=raw_label,
        normalized_label=normalized_label,
        mapping_status=mapping_status,
        confidence=confidence,
        method=SemanticNormalizationMethod(kind="llm", model="test-model"),
    )


def _make_provider_root(tmp_path: Path) -> Path:
    return tmp_path / "openai"


def _setup_provider_with_windows_and_parsed(root: Path) -> None:
    thread_a = root / "thread-conv-a"
    messages_a = [
        _message("conv-a", "a-1", role="user", text="Please ship the release notes", ts=1),
        _message("conv-a", "a-2", role="assistant", text="I will draft them now", ts=2),
    ]
    _write_parsed_thread(
        thread_a,
        provider_id="openai",
        conversation_id="conv-a",
        messages=messages_a,
    )
    _write_jsonl(
        thread_a / "message_windows.jsonl",
        [
            _window_row(
                "conv-a",
                "window-0001",
                message_ids=["a-1", "a-2"],
                char_count=len(messages_a[0]["text"]) + len(messages_a[1]["text"]),
            )
        ],
    )


def _setup_provider_with_parsed_only(root: Path) -> None:
    thread_b = root / "thread-conv-b"
    _write_parsed_thread(
        thread_b,
        provider_id="openai",
        conversation_id="conv-b",
        messages=[
            _message("conv-b", "b-1", role="user", text="What is the next step?", ts=3),
            _message("conv-b", "b-2", role="assistant", text="Review the checklist", ts=4),
        ],
    )


def _setup_provider_with_extra_parsed_only(root: Path) -> None:
    thread_c = root / "thread-conv-c"
    _write_parsed_thread(
        thread_c,
        provider_id="openai",
        conversation_id="conv-c",
        messages=[
            _message("conv-c", "c-1", role="user", text="Confirm the launch gate", ts=5),
            _message("conv-c", "c-2", role="assistant", text="The gate is still yellow", ts=6),
        ],
    )


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def test_run_accepts_provider_root_with_usable_threads_and_creates_job_layout(tmp_path):
    root = _make_provider_root(tmp_path)
    _setup_provider_with_windows_and_parsed(root)
    _setup_provider_with_parsed_only(root)
    invalid_thread = root / "thread-invalid"
    _write_jsonl(
        invalid_thread / "message_windows.jsonl",
        [
            _window_row(
                "conv-invalid",
                "window-0001",
                message_ids=["x-1"],
                char_count=12,
            )
        ],
    )

    def _normalize(**kwargs):
        return _fake_result(
            conversation_id=kwargs["conversation_id"],
            span_id=kwargs["span_id"],
            window_id=kwargs["window_id"],
            message_ids=kwargs["message_ids"],
        )

    with patch(
        "llm_logparser.core.semantic_normalization_jobs.normalize_representative_span",
        side_effect=_normalize,
    ):
        result = run_semantic_normalization_job(root, model="test-model")

    job_dir = Path(result["job_dir"])
    assert (_config := job_dir / "config.json").exists()
    assert (job_dir / "spans.jsonl").exists()
    assert (job_dir / "results.jsonl").exists()
    assert (job_dir / "failures.jsonl").exists()
    assert (job_dir / "progress.json").exists()
    assert (job_dir / "summary.json").exists()

    config = _read_json(_config)
    assert config["selected_inputs"]["thread_count"] == 2
    assert config["selected_inputs"]["message_windows_files"] == 1
    assert config["selected_inputs"]["parsed_only_files"] == 1
    assert config["prompt_provenance"] == semantic_normalization_prompt_provenance()
    assert len(result["invalid_threads"]) == 1


def test_semantic_normalization_job_artifacts_validate_against_schemas(tmp_path):
    root = _make_provider_root(tmp_path)
    _setup_provider_with_windows_and_parsed(root)
    _setup_provider_with_parsed_only(root)

    with patch(
        "llm_logparser.core.semantic_normalization_jobs.normalize_representative_span",
        side_effect=lambda **kwargs: _fake_result(
            conversation_id=kwargs["conversation_id"],
            span_id=kwargs["span_id"],
            window_id=kwargs["window_id"],
            message_ids=kwargs["message_ids"],
        ),
    ):
        result = run_semantic_normalization_job(root, model="test-model", job_id="schema-job")

    job_dir = Path(result["job_dir"])
    config_validator = load_semantic_normalization_job_config_validator()
    span_validator = load_semantic_normalization_job_span_validator()
    result_validator = load_semantic_normalization_job_result_validator()
    failure_validator = load_semantic_normalization_job_failure_validator()

    config = _read_json(job_dir / "config.json")
    span_rows = _read_jsonl(job_dir / "spans.jsonl")
    result_rows = _read_jsonl(job_dir / "results.jsonl")
    failure_rows = _read_jsonl(job_dir / "failures.jsonl")

    assert list(config_validator.iter_errors(config)) == []
    assert all(list(span_validator.iter_errors(row)) == [] for row in span_rows)
    assert all(list(result_validator.iter_errors(row)) == [] for row in result_rows)
    assert all(list(failure_validator.iter_errors(row)) == [] for row in failure_rows)


def test_run_rejects_provider_root_with_no_usable_threads(tmp_path):
    root = _make_provider_root(tmp_path)
    invalid_thread = root / "thread-invalid"
    _write_jsonl(
        invalid_thread / "message_windows.jsonl",
        [
            _window_row(
                "conv-invalid",
                "window-0001",
                message_ids=["x-1"],
                char_count=12,
            )
        ],
    )

    with pytest.raises(SemanticNormalizationJobError) as exc:
        run_semantic_normalization_job(root, model="test-model", job_id="snorm_invalid")

    assert "no usable threads found" in str(exc.value)
    assert not (root / "l3" / "semantic-normalization" / "jobs" / "snorm_invalid").exists()


def test_run_collision_behavior_requires_overwrite(tmp_path):
    root = _make_provider_root(tmp_path)
    _setup_provider_with_windows_and_parsed(root)

    def _normalize(**kwargs):
        return _fake_result(
            conversation_id=kwargs["conversation_id"],
            span_id=kwargs["span_id"],
            window_id=kwargs["window_id"],
            message_ids=kwargs["message_ids"],
        )

    with patch(
        "llm_logparser.core.semantic_normalization_jobs.normalize_representative_span",
        side_effect=_normalize,
    ):
        run_semantic_normalization_job(root, model="test-model", job_id="same-job")
        with pytest.raises(SemanticNormalizationJobError):
            run_semantic_normalization_job(root, model="test-model", job_id="same-job")
        result = run_semantic_normalization_job(
            root,
            model="test-model",
            job_id="same-job",
            overwrite=True,
        )

    assert Path(result["job_dir"]).exists()


def test_run_writes_success_and_failure_rows_and_updates_summary(tmp_path):
    root = _make_provider_root(tmp_path)
    _setup_provider_with_windows_and_parsed(root)
    _setup_provider_with_parsed_only(root)
    calls: list[str] = []

    def _normalize(**kwargs):
        calls.append(kwargs["span_id"])
        if len(calls) == 2:
            raise RuntimeError("Ollama request failed for /api/generate: timeout")
        return _fake_result(
            conversation_id=kwargs["conversation_id"],
            span_id=kwargs["span_id"],
            window_id=kwargs["window_id"],
            message_ids=kwargs["message_ids"],
        )

    with patch(
        "llm_logparser.core.semantic_normalization_jobs.normalize_representative_span",
        side_effect=_normalize,
    ):
        result = run_semantic_normalization_job(root, model="test-model", job_id="job-run")

    job_dir = Path(result["job_dir"])
    results_rows = _read_jsonl(job_dir / "results.jsonl")
    failure_rows = _read_jsonl(job_dir / "failures.jsonl")
    summary = _read_json(job_dir / "summary.json")
    progress = _read_json(job_dir / "progress.json")

    assert len(calls) == 2
    assert len(results_rows) == 1
    assert len(failure_rows) == 1
    assert failure_rows[0]["error_kind"] == "transport_error"
    assert summary["status"] == "completed_with_failures"
    assert summary["counts"]["success_count"] == 1
    assert summary["counts"]["failure_count"] == 1
    assert progress["status"] == "completed_with_failures"


def test_resume_processes_pending_spans_only_and_blocks_on_input_drift(tmp_path):
    root = _make_provider_root(tmp_path)
    _setup_provider_with_windows_and_parsed(root)
    _setup_provider_with_parsed_only(root)

    def _normalize(**kwargs):
        return _fake_result(
            conversation_id=kwargs["conversation_id"],
            span_id=kwargs["span_id"],
            window_id=kwargs["window_id"],
            message_ids=kwargs["message_ids"],
        )

    with patch(
        "llm_logparser.core.semantic_normalization_jobs.normalize_representative_span",
        side_effect=_normalize,
    ):
        result = run_semantic_normalization_job(root, model="test-model", job_id="resume-job")

    job_dir = Path(result["job_dir"])
    all_results = _read_jsonl(job_dir / "results.jsonl")
    pending_result = all_results[1]
    with (job_dir / "results.jsonl").open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(all_results[0], ensure_ascii=False) + "\n")
    (job_dir / "failures.jsonl").write_text("", encoding="utf-8")
    progress = _read_json(job_dir / "progress.json")
    progress["status"] = "interrupted"
    progress["success_count"] = 1
    progress["failure_count"] = 0
    progress["pending_count"] = 1
    (job_dir / "progress.json").write_text(json.dumps(progress, ensure_ascii=False), encoding="utf-8")

    calls: list[str] = []

    def _resume_normalize(**kwargs):
        calls.append(kwargs["span_id"])
        return _fake_result(
            conversation_id=kwargs["conversation_id"],
            span_id=kwargs["span_id"],
            window_id=kwargs["window_id"],
            message_ids=kwargs["message_ids"],
        )

    with patch(
        "llm_logparser.core.semantic_normalization_jobs.normalize_representative_span",
        side_effect=_resume_normalize,
    ):
        resume_semantic_normalization_job(root, job_id="resume-job")

    assert calls == [pending_result["span_id"]]

    with (job_dir / "results.jsonl").open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(all_results[0], ensure_ascii=False) + "\n")
    progress = _read_json(job_dir / "progress.json")
    progress["status"] = "interrupted"
    progress["success_count"] = 1
    progress["failure_count"] = 0
    progress["pending_count"] = 1
    (job_dir / "progress.json").write_text(json.dumps(progress, ensure_ascii=False), encoding="utf-8")
    parsed_only = root / "thread-conv-b" / "parsed.jsonl"
    rows = _read_jsonl(parsed_only)
    rows[-1]["text"] = "Changed text after the job was frozen"
    rows[-1]["content"]["parts"] = [rows[-1]["text"]]
    _write_jsonl(parsed_only, rows)
    with pytest.raises(SemanticNormalizationJobError) as exc:
        resume_semantic_normalization_job(root, job_id="resume-job")
    assert "input drift detected" in str(exc.value)


def test_retry_failures_retries_failed_spans_only_and_supports_explicit_span_id(tmp_path):
    root = _make_provider_root(tmp_path)
    _setup_provider_with_windows_and_parsed(root)
    _setup_provider_with_parsed_only(root)

    call_index = {"count": 0}

    def _normalize(**kwargs):
        call_index["count"] += 1
        if call_index["count"] == 1:
            raise RuntimeError("structured LLM response was not valid JSON after 2 attempts")
        return _fake_result(
            conversation_id=kwargs["conversation_id"],
            span_id=kwargs["span_id"],
            window_id=kwargs["window_id"],
            message_ids=kwargs["message_ids"],
        )

    with patch(
        "llm_logparser.core.semantic_normalization_jobs.normalize_representative_span",
        side_effect=_normalize,
    ):
        result = run_semantic_normalization_job(root, model="test-model", job_id="retry-job")

    job_dir = Path(result["job_dir"])
    failed_span_id = _read_jsonl(job_dir / "failures.jsonl")[0]["span_id"]

    with patch(
        "llm_logparser.core.semantic_normalization_jobs.normalize_representative_span",
        side_effect=lambda **kwargs: (_ for _ in ()).throw(
            RuntimeError("Ollama request failed for /api/generate: timeout")
        ),
    ):
        retry_semantic_normalization_job_failures(
            root,
            job_id="retry-job",
            span_ids=[failed_span_id],
        )

    failure_rows = _read_jsonl(job_dir / "failures.jsonl")
    matching = [row for row in failure_rows if row["span_id"] == failed_span_id]
    assert matching[-1]["attempt_no"] >= 2

    with patch(
        "llm_logparser.core.semantic_normalization_jobs.normalize_representative_span",
        side_effect=lambda **kwargs: _fake_result(
            conversation_id=kwargs["conversation_id"],
            span_id=kwargs["span_id"],
            window_id=kwargs["window_id"],
            message_ids=kwargs["message_ids"],
        ),
    ):
        retry_semantic_normalization_job_failures(
            root,
            job_id="retry-job",
            span_ids=[failed_span_id],
        )

    results_rows = _read_jsonl(job_dir / "results.jsonl")
    assert any(row["span_id"] == failed_span_id for row in results_rows)


def test_loading_results_fails_clearly_for_malformed_result_rows(tmp_path):
    root = _make_provider_root(tmp_path)
    _setup_provider_with_windows_and_parsed(root)

    with patch(
        "llm_logparser.core.semantic_normalization_jobs.normalize_representative_span",
        side_effect=lambda **kwargs: _fake_result(
            conversation_id=kwargs["conversation_id"],
            span_id=kwargs["span_id"],
            window_id=kwargs["window_id"],
            message_ids=kwargs["message_ids"],
        ),
    ):
        result = run_semantic_normalization_job(root, model="test-model", job_id="bad-result-job")

    job_dir = Path(result["job_dir"])
    rows = _read_jsonl(job_dir / "results.jsonl")
    rows[0].pop("mapping_status")
    _write_jsonl(job_dir / "results.jsonl", rows)

    with pytest.raises(SemanticNormalizationJobError) as exc:
        load_semantic_normalization_job_results(root, job_id="bad-result-job")

    assert "semantic normalization results schema validation failed" in str(exc.value)


def test_loading_old_job_config_without_prompt_paths_remains_compatible(tmp_path):
    root = _make_provider_root(tmp_path)
    _setup_provider_with_windows_and_parsed(root)

    with patch(
        "llm_logparser.core.semantic_normalization_jobs.normalize_representative_span",
        side_effect=lambda **kwargs: _fake_result(
            conversation_id=kwargs["conversation_id"],
            span_id=kwargs["span_id"],
            window_id=kwargs["window_id"],
            message_ids=kwargs["message_ids"],
        ),
    ):
        result = run_semantic_normalization_job(
            root,
            model="test-model",
            job_id="old-prompt-job",
        )

    job_dir = Path(result["job_dir"])
    config = _read_json(job_dir / "config.json")
    config["prompt_provenance"] = {
        "raw_label_prompt_sha1": config["prompt_provenance"]["raw_label_prompt_sha1"],
        "mapping_prompt_sha1": config["prompt_provenance"]["mapping_prompt_sha1"],
    }
    (job_dir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False),
        encoding="utf-8",
    )

    loaded = load_semantic_normalization_job_results(root, job_id="old-prompt-job")
    assert loaded.config["job_id"] == "old-prompt-job"


def test_resume_fails_clearly_for_malformed_span_rows(tmp_path):
    root = _make_provider_root(tmp_path)
    _setup_provider_with_windows_and_parsed(root)

    with patch(
        "llm_logparser.core.semantic_normalization_jobs.normalize_representative_span",
        side_effect=lambda **kwargs: _fake_result(
            conversation_id=kwargs["conversation_id"],
            span_id=kwargs["span_id"],
            window_id=kwargs["window_id"],
            message_ids=kwargs["message_ids"],
        ),
    ):
        result = run_semantic_normalization_job(root, model="test-model", job_id="bad-span-job")

    job_dir = Path(result["job_dir"])
    results_rows = _read_jsonl(job_dir / "results.jsonl")
    (job_dir / "results.jsonl").write_text("", encoding="utf-8")
    progress = _read_json(job_dir / "progress.json")
    progress["status"] = "interrupted"
    progress["success_count"] = 0
    progress["failure_count"] = 0
    progress["pending_count"] = len(results_rows)
    (job_dir / "progress.json").write_text(
        json.dumps(progress, ensure_ascii=False),
        encoding="utf-8",
    )

    span_rows = _read_jsonl(job_dir / "spans.jsonl")
    span_rows[0].pop("text_sha1")
    _write_jsonl(job_dir / "spans.jsonl", span_rows)

    with pytest.raises(SemanticNormalizationJobError) as exc:
        resume_semantic_normalization_job(root, job_id="bad-span-job")

    assert "semantic normalization spans schema validation failed" in str(exc.value)


def test_status_and_summary_commands_render_text_and_json(tmp_path, capsys):
    root = _make_provider_root(tmp_path)
    _setup_provider_with_windows_and_parsed(root)

    with patch(
        "llm_logparser.core.semantic_normalization_jobs.normalize_representative_span",
        side_effect=lambda **kwargs: _fake_result(
            conversation_id=kwargs["conversation_id"],
            span_id=kwargs["span_id"],
            window_id=kwargs["window_id"],
            message_ids=kwargs["message_ids"],
        ),
    ):
        result = run_semantic_normalization_job(root, model="test-model", job_id="cli-job")

    main(
        [
            "analyze",
            "semantic-normalization",
            "status",
            "--input",
            str(root),
            "--job-id",
            "cli-job",
        ]
    )
    status_output = capsys.readouterr().out
    assert "Status:" in status_output

    main(
        [
            "analyze",
            "semantic-normalization",
            "summary",
            "--input",
            str(root),
            "--job-id",
            "cli-job",
            "--json",
        ]
    )
    summary_output = capsys.readouterr().out
    summary_payload = json.loads(summary_output)
    assert summary_payload["job_id"] == "cli-job"
    assert summary_payload["status"] == "completed"
    assert summary_payload["labels"]["top_raw_labels"][0]["label"] == "request"
    assert summary_payload["labels"]["top_raw_labels"][0]["count"] == 1
    assert summary_payload["labels"]["top_normalized_labels"][0]["label"] == "request"
    assert summary_payload["failures"]["by_error_kind"] == []

    assert "Job:" in render_semantic_normalization_job_status(root, job_id="cli-job")
    summary_text = render_semantic_normalization_job_summary(root, job_id="cli-job")
    assert "Job:" in summary_text
    assert "Top raw labels:" in summary_text


def test_compare_reports_overlap_and_label_changes_between_two_jobs(tmp_path, capsys):
    root = _make_provider_root(tmp_path)
    _setup_provider_with_windows_and_parsed(root)
    _setup_provider_with_parsed_only(root)
    _setup_provider_with_extra_parsed_only(root)

    def _normalize_job_a(**kwargs):
        conversation_id = kwargs["conversation_id"]
        if conversation_id == "conv-c":
            raise RuntimeError("Ollama request failed for /api/generate: timeout")
        if conversation_id == "conv-b":
            return _fake_result(
                conversation_id=conversation_id,
                span_id=kwargs["span_id"],
                window_id=kwargs["window_id"],
                message_ids=kwargs["message_ids"],
                raw_label="clarify",
                normalized_label="clarification",
                mapping_status="mapped",
            )
        return _fake_result(
            conversation_id=conversation_id,
            span_id=kwargs["span_id"],
            window_id=kwargs["window_id"],
            message_ids=kwargs["message_ids"],
            raw_label="request",
            normalized_label="request",
            mapping_status="mapped",
        )

    with patch(
        "llm_logparser.core.semantic_normalization_jobs.normalize_representative_span",
        side_effect=_normalize_job_a,
    ):
        run_semantic_normalization_job(root, model="test-model", job_id="compare-a")

    def _normalize_job_b(**kwargs):
        conversation_id = kwargs["conversation_id"]
        if conversation_id == "conv-a":
            raise RuntimeError("Ollama request failed for /api/generate: timeout")
        if conversation_id == "conv-b":
            return _fake_result(
                conversation_id=conversation_id,
                span_id=kwargs["span_id"],
                window_id=kwargs["window_id"],
                message_ids=kwargs["message_ids"],
                raw_label="follow up",
                normalized_label="question",
                mapping_status="needs_review",
                confidence=0.44,
            )
        return _fake_result(
            conversation_id=conversation_id,
            span_id=kwargs["span_id"],
            window_id=kwargs["window_id"],
            message_ids=kwargs["message_ids"],
            raw_label="decision",
            normalized_label="decision",
            mapping_status="mapped",
        )

    with patch(
        "llm_logparser.core.semantic_normalization_jobs.normalize_representative_span",
        side_effect=_normalize_job_b,
    ):
        run_semantic_normalization_job(root, model="test-model", job_id="compare-b")

    main(
        [
            "analyze",
            "semantic-normalization",
            "compare",
            "--input",
            str(root),
            "--job-a",
            "compare-a",
            "--job-b",
            "compare-b",
            "--json",
        ]
    )
    compare_output = capsys.readouterr().out
    compare_payload = json.loads(compare_output)
    assert compare_payload["job_a"]["job_id"] == "compare-a"
    assert compare_payload["job_b"]["job_id"] == "compare-b"
    assert compare_payload["counts"]["rows_a"] == 2
    assert compare_payload["counts"]["rows_b"] == 2
    assert compare_payload["counts"]["shared_span_count"] == 1
    assert compare_payload["counts"]["only_in_a_count"] == 1
    assert compare_payload["counts"]["only_in_b_count"] == 1
    assert compare_payload["counts"]["raw_label_changed_count"] == 1
    assert compare_payload["counts"]["normalized_label_changed_count"] == 1
    assert compare_payload["counts"]["mapping_status_changed_count"] == 1
    assert compare_payload["counts"]["unchanged_shared_span_count"] == 0
    assert compare_payload["changes"]["top_raw_label_changes"] == [
        {"from": "clarify", "to": "follow up", "count": 1}
    ]
    assert compare_payload["changes"]["top_normalized_label_changes"] == [
        {"from": "clarification", "to": "question", "count": 1}
    ]
    assert compare_payload["changes"]["top_mapping_status_changes"] == [
        {"from": "mapped", "to": "needs_review", "count": 1}
    ]

    compare_text = render_semantic_normalization_job_compare(
        root,
        job_a="compare-a",
        job_b="compare-b",
    )
    assert "Job A: compare-a" in compare_text
    assert "shared=1" in compare_text
    assert "clarification->question=1" in compare_text
