from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from llm_logparser.core import analyzer_intra_thread_topic_summaries as summaries
from llm_logparser.core.analyzer_intra_thread_topic_summaries import (
    DEFAULT_LOCAL_LLM_MODEL,
    IntraThreadTopicSummaryError,
    build_intra_thread_topic_summary_rows,
    intra_thread_topic_summaries_artifact_path,
    write_intra_thread_topic_summaries,
)
from llm_logparser.core.schema_validation import (
    load_intra_thread_topic_summary_validator,
)


def _message_row(
    *,
    provider_id: str = "openai",
    conversation_id: str = "conv-a",
    message_id: str,
    role: str,
    ts: int,
    text: str,
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


def _write_parsed_jsonl(
    path: Path,
    *,
    provider_id: str = "openai",
    conversation_id: str = "conv-a",
    messages: list[dict],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "record_type": "thread",
                    "provider_id": provider_id,
                    "conversation_id": conversation_id,
                    "message_count": len(messages),
                },
                ensure_ascii=True,
            )
            + "\n"
        )
        for row in messages:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def _write_segments_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _segment_row(
    *,
    message_ids: list[str],
    text: str,
    segment_id: str = "segment_a",
    start_index: int = 0,
    end_index: int = 1,
) -> dict:
    return {
        "record_type": "intra_thread_segment",
        "schema_version": "0.1",
        "provider_id": "openai",
        "conversation_id": "conv-a",
        "segment_id": segment_id,
        "start_index": start_index,
        "end_index": end_index,
        "message_ids": message_ids,
        "message_count": len(message_ids),
        "text_sha1": _sha1(text),
    }


def _write_basic_thread_with_segments(tmp_path: Path) -> Path:
    parsed_path = tmp_path / "openai" / "thread-conv-a" / "parsed.jsonl"
    messages = [
        _message_row(
            message_id="m1",
            role="user",
            ts=100,
            text="Plan the launch checklist for the parser release.",
        ),
        _message_row(
            message_id="m2",
            role="assistant",
            ts=101,
            text="Draft release notes, validate schemas, and run focused tests.",
        ),
    ]
    _write_parsed_jsonl(parsed_path, messages=messages)
    segment_text = "\n\n".join(row["text"] for row in messages)
    _write_segments_jsonl(
        parsed_path.parent / "l3" / "intra-thread-topics" / "segments.jsonl",
        [_segment_row(message_ids=["m1", "m2"], text=segment_text)],
    )
    return parsed_path


def _write_thread_with_single_segment(root: Path, thread_id: str, text: str) -> Path:
    parsed_path = root / thread_id / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed_path,
        messages=[
            _message_row(message_id=f"{thread_id}-m1", role="user", ts=100, text=text),
        ],
    )
    _write_segments_jsonl(
        parsed_path.parent / "l3" / "intra-thread-topics" / "segments.jsonl",
        [
            _segment_row(
                message_ids=[f"{thread_id}-m1"],
                text=text,
                segment_id=f"{thread_id}-segment",
                end_index=0,
            )
        ],
    )
    return parsed_path


def _load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class FakeLLMClient:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def generate_text(
        self,
        model: str,
        prompt: str,
        *,
        response_format: str | None = None,
        options: dict[str, object] | None = None,
    ) -> str:
        self.calls.append(
            {
                "model": model,
                "prompt": prompt,
                "response_format": response_format,
                "options": options or {},
            }
        )
        if self.responses:
            return self.responses.pop(0)
        return "{}"

    def embeddings(self, model: str, prompt: str) -> list[float]:
        raise AssertionError("embeddings should not be called")

    def generate_json(self, model: str, prompt: str) -> dict:
        raise AssertionError("generate_json should not be called")


def test_intra_thread_topic_summaries_fail_when_segments_missing(tmp_path):
    parsed_path = tmp_path / "openai" / "thread-conv-a" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed_path,
        messages=[
            _message_row(message_id="m1", role="user", ts=100, text="hello"),
        ],
    )

    with pytest.raises(IntraThreadTopicSummaryError, match="segments artifact not found"):
        build_intra_thread_topic_summary_rows(parsed_path)


def test_intra_thread_topic_summaries_write_successful_heuristic_output(tmp_path):
    parsed_path = _write_basic_thread_with_segments(tmp_path)

    result = write_intra_thread_topic_summaries(parsed_path)
    output_path = intra_thread_topic_summaries_artifact_path(parsed_path)
    rows = _load_jsonl(output_path)

    assert result["threads"] == 1
    assert result["summaries"] == 1
    assert result["artifacts"] == [output_path]
    assert rows[0]["record_type"] == "intra_thread_topic_summary"
    assert rows[0]["schema_version"] == "0.1"
    assert rows[0]["segment_id"] == "segment_a"
    assert rows[0]["message_ids"] == ["m1", "m2"]
    assert rows[0]["message_count"] == 2
    assert rows[0]["title"].startswith("Plan the launch checklist")
    assert "Draft release notes" in rows[0]["summary"]
    assert rows[0]["conclusion_text"] is None
    assert rows[0]["conclusion_status"] == "unknown"
    assert rows[0]["source"] == "heuristic"
    assert "model" not in rows[0]
    assert "prompt_variant" not in rows[0]
    assert "prompt_hash" not in rows[0]
    assert "launch" in rows[0]["keywords"]
    assert rows[0]["confidence"] == 0.3


def test_intra_thread_topic_summaries_provider_root_skips_existing_and_writes_missing(
    tmp_path,
):
    root = tmp_path / "openai"
    existing_parsed = _write_thread_with_single_segment(
        root,
        "thread-existing",
        "Existing artifact should be preserved.",
    )
    missing_parsed = _write_thread_with_single_segment(
        root,
        "thread-missing",
        "Missing artifact should be generated.",
    )
    existing_output = intra_thread_topic_summaries_artifact_path(existing_parsed)
    existing_output.parent.mkdir(parents=True, exist_ok=True)
    existing_output.write_text('{"sentinel": true}\n', encoding="utf-8")

    result = write_intra_thread_topic_summaries(root)

    assert result["threads_found"] == 2
    assert result["written_threads"] == 1
    assert result["skipped_existing"] == 1
    assert result["summaries"] == 1
    assert result["jobs"] == 1
    assert result["skipped_artifacts"] == [existing_output]
    assert existing_output.read_text(encoding="utf-8") == '{"sentinel": true}\n'
    missing_rows = _load_jsonl(intra_thread_topic_summaries_artifact_path(missing_parsed))
    assert missing_rows[0]["source"] == "heuristic"


def test_intra_thread_topic_summaries_single_thread_skips_existing(tmp_path):
    parsed_path = _write_basic_thread_with_segments(tmp_path)
    output_path = intra_thread_topic_summaries_artifact_path(parsed_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text('{"sentinel": true}\n', encoding="utf-8")

    result = write_intra_thread_topic_summaries(parsed_path)

    assert result["threads_found"] == 1
    assert result["written_threads"] == 0
    assert result["skipped_existing"] == 1
    assert result["summaries"] == 0
    assert result["artifacts"] == []
    assert output_path.read_text(encoding="utf-8") == '{"sentinel": true}\n'


def test_intra_thread_topic_summaries_overwrite_replaces_existing(tmp_path):
    parsed_path = _write_basic_thread_with_segments(tmp_path)
    output_path = intra_thread_topic_summaries_artifact_path(parsed_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text('{"sentinel": true}\n', encoding="utf-8")

    result = write_intra_thread_topic_summaries(parsed_path, overwrite=True)
    rows = _load_jsonl(output_path)

    assert result["written_threads"] == 1
    assert result["skipped_existing"] == 0
    assert rows[0]["record_type"] == "intra_thread_topic_summary"
    assert "sentinel" not in rows[0]


def test_intra_thread_topic_summaries_skipped_existing_does_not_call_llm(tmp_path):
    parsed_path = _write_basic_thread_with_segments(tmp_path)
    output_path = intra_thread_topic_summaries_artifact_path(parsed_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text('{"sentinel": true}\n', encoding="utf-8")
    client = FakeLLMClient(["{}"])

    result = write_intra_thread_topic_summaries(
        parsed_path,
        source="local_llm",
        client=client,
    )

    assert result["written_threads"] == 0
    assert result["skipped_existing"] == 1
    assert result["local_llm_summaries"] == 0
    assert result["local_llm_failures"] == 0
    assert client.calls == []


def test_intra_thread_topic_summaries_jobs_two_processes_missing_threads(tmp_path):
    root = tmp_path / "openai"
    first = _write_thread_with_single_segment(root, "thread-a", "First thread text.")
    second = _write_thread_with_single_segment(root, "thread-b", "Second thread text.")

    result = write_intra_thread_topic_summaries(root, jobs=2)

    assert result["threads_found"] == 2
    assert result["written_threads"] == 2
    assert result["skipped_existing"] == 0
    assert result["summaries"] == 2
    assert result["jobs"] == 2
    assert intra_thread_topic_summaries_artifact_path(first).exists()
    assert intra_thread_topic_summaries_artifact_path(second).exists()


def test_intra_thread_topic_summaries_parallel_skips_existing_artifacts(tmp_path):
    root = tmp_path / "openai"
    existing = _write_thread_with_single_segment(
        root,
        "thread-existing",
        "Existing artifact should be skipped.",
    )
    missing_a = _write_thread_with_single_segment(root, "thread-a", "First text.")
    missing_b = _write_thread_with_single_segment(root, "thread-b", "Second text.")
    existing_output = intra_thread_topic_summaries_artifact_path(existing)
    existing_output.parent.mkdir(parents=True, exist_ok=True)
    existing_output.write_text('{"sentinel": true}\n', encoding="utf-8")

    result = write_intra_thread_topic_summaries(root, jobs=2)

    assert result["threads_found"] == 3
    assert result["written_threads"] == 2
    assert result["skipped_existing"] == 1
    assert result["summaries"] == 2
    assert result["jobs"] == 2
    assert existing_output.read_text(encoding="utf-8") == '{"sentinel": true}\n'
    assert intra_thread_topic_summaries_artifact_path(missing_a).exists()
    assert intra_thread_topic_summaries_artifact_path(missing_b).exists()


def test_intra_thread_topic_summaries_rejects_invalid_jobs(tmp_path):
    parsed_path = _write_basic_thread_with_segments(tmp_path)

    with pytest.raises(IntraThreadTopicSummaryError, match="--jobs"):
        write_intra_thread_topic_summaries(parsed_path, jobs=0)


def test_intra_thread_topic_summaries_local_llm_writes_valid_response(tmp_path):
    parsed_path = _write_basic_thread_with_segments(tmp_path)
    client = FakeLLMClient(
        [
            json.dumps(
                {
                    "title": "Launch checklist schema",
                    "summary": "The segment discusses adding deterministic schema-backed topic summaries.",
                    "conclusion_text": None,
                    "conclusion_status": "unknown",
                    "keywords": ["schema", "topic summaries", "tests"],
                    "confidence": 0.82,
                }
            )
        ]
    )

    result = write_intra_thread_topic_summaries(
        parsed_path,
        source="local_llm",
        model="mistral-nemo:latest",
        client=client,
    )
    row = _load_jsonl(intra_thread_topic_summaries_artifact_path(parsed_path))[0]

    assert result["local_llm_summaries"] == 1
    assert result["local_llm_failures"] == 0
    assert result["jobs"] == 1
    assert row["source"] == "local_llm"
    assert row["title"] == "Launch checklist schema"
    assert row["model"] == "ollama/mistral-nemo:latest"
    assert row["prompt_variant"] == "intra_thread_topic_summary_v0"
    assert row["prompt_hash"].startswith("sha256:")
    assert client.calls[0]["response_format"] == "json"


def test_intra_thread_topic_summaries_empty_segment_skips_local_llm(tmp_path):
    parsed_path = tmp_path / "openai" / "thread-conv-a" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed_path,
        messages=[
            _message_row(message_id="m1", role="user", ts=100, text=""),
        ],
    )
    _write_segments_jsonl(
        parsed_path.parent / "l3" / "intra-thread-topics" / "segments.jsonl",
        [_segment_row(message_ids=["m1"], text="", end_index=0)],
    )
    client = FakeLLMClient(
        [
            json.dumps(
                {
                    "title": "No segment provided",
                    "summary": "No conversation segment was provided for summarization.",
                    "conclusion_text": None,
                    "conclusion_status": "unknown",
                    "keywords": ["empty"],
                    "confidence": 0.0,
                }
            )
        ]
    )

    result = write_intra_thread_topic_summaries(
        parsed_path,
        source="local_llm",
        client=client,
    )
    row = _load_jsonl(intra_thread_topic_summaries_artifact_path(parsed_path))[0]

    assert client.calls == []
    assert result["local_llm_summaries"] == 0
    assert result["local_llm_failures"] == 1
    assert row["source"] == "heuristic"
    assert row["title"] == ""
    assert row["summary"] == ""
    assert row["confidence"] == 0.0
    assert "model" not in row
    assert "prompt_variant" not in row
    assert "prompt_hash" not in row


def test_intra_thread_topic_summaries_local_llm_defaults_model(tmp_path):
    parsed_path = _write_basic_thread_with_segments(tmp_path)
    client = FakeLLMClient(
        [
            json.dumps(
                {
                    "title": "Launch checklist schema",
                    "summary": "The segment discusses schema-backed topic summaries.",
                    "conclusion_text": None,
                    "conclusion_status": "unknown",
                    "keywords": ["schema"],
                    "confidence": 0.7,
                }
            )
        ]
    )

    rows = build_intra_thread_topic_summary_rows(
        parsed_path,
        source="local_llm",
        client=client,
    )

    assert rows[0]["model"] == f"ollama/{DEFAULT_LOCAL_LLM_MODEL}"
    assert client.calls[0]["model"] == DEFAULT_LOCAL_LLM_MODEL


def test_intra_thread_topic_summaries_allows_explicit_not_recommended_model(
    tmp_path,
):
    parsed_path = _write_basic_thread_with_segments(tmp_path)
    client = FakeLLMClient(
        [
            json.dumps(
                {
                    "title": "Launch checklist schema",
                    "summary": "The segment discusses schema-backed topic summaries.",
                    "conclusion_text": None,
                    "conclusion_status": "unknown",
                    "keywords": ["schema"],
                    "confidence": 0.7,
                }
            )
        ]
    )

    rows = build_intra_thread_topic_summary_rows(
        parsed_path,
        source="local_llm",
        model="lfm-thinking:latest",
        client=client,
    )

    assert rows[0]["source"] == "local_llm"
    assert rows[0]["model"] == "ollama/lfm-thinking:latest"
    assert client.calls[0]["model"] == "lfm-thinking:latest"


def test_intra_thread_topic_summaries_local_llm_truncates_prompt_head_and_tail(
    tmp_path,
):
    parsed_path = tmp_path / "openai" / "thread-conv-a" / "parsed.jsonl"
    head_text = "HEAD_CONTEXT keep this launch requirement."
    middle_text = ("x" * 15000) + "MIDDLE_SHOULD_BE_REMOVED" + ("y" * 15000)
    tail_text = "TAIL_CONTEXT keep this final decision. Decision: Use the schema."
    message_text = f"{head_text}\n{middle_text}\n{tail_text}"
    _write_parsed_jsonl(
        parsed_path,
        messages=[
            _message_row(message_id="m1", role="user", ts=100, text=message_text),
        ],
    )
    _write_segments_jsonl(
        parsed_path.parent / "l3" / "intra-thread-topics" / "segments.jsonl",
        [_segment_row(message_ids=["m1"], text=message_text, end_index=0)],
    )
    client = FakeLLMClient(
        [
            json.dumps(
                {
                    "title": "Schema decision",
                    "summary": "The segment preserves the launch requirement and final schema decision.",
                    "conclusion_text": "Decision: Use the schema.",
                    "conclusion_status": "explicit",
                    "keywords": ["schema", "decision"],
                    "confidence": 0.8,
                }
            )
        ]
    )

    rows = build_intra_thread_topic_summary_rows(
        parsed_path,
        source="local_llm",
        client=client,
    )
    prompt = str(client.calls[0]["prompt"])

    assert rows[0]["source"] == "local_llm"
    assert head_text in prompt
    assert tail_text in prompt
    assert "\n...\n" in prompt
    assert "MIDDLE_SHOULD_BE_REMOVED" not in prompt
    assert len(prompt) < len(message_text)
    assert (
        len(summaries._LocalPromptTokenizer().encode(prompt))
        <= summaries.LOCAL_LLM_MAX_PROMPT_TOKENS
    )


def test_intra_thread_topic_summaries_prompt_truncation_falls_back_without_tokenizer(
    monkeypatch,
):
    monkeypatch.setattr(summaries, "_load_prompt_tokenizer", lambda: None)
    head_text = "HEAD_CONTEXT keep this launch requirement."
    middle_text = ("x" * 9000) + "MIDDLE_SHOULD_BE_REMOVED" + ("y" * 9000)
    tail_text = "TAIL_CONTEXT keep this final decision."
    segment_text = f"{head_text}\n{middle_text}\n{tail_text}"

    truncated = summaries._truncate_segment_text_for_prompt(segment_text)

    assert head_text in truncated
    assert tail_text in truncated
    assert "\n...\n" in truncated
    assert "MIDDLE_SHOULD_BE_REMOVED" not in truncated
    assert len(truncated) < len(segment_text)


def test_intra_thread_topic_summaries_invalid_json_falls_back_to_heuristic(tmp_path):
    parsed_path = _write_basic_thread_with_segments(tmp_path)
    client = FakeLLMClient(["not-json", "still-not-json"])

    result = write_intra_thread_topic_summaries(
        parsed_path,
        source="local_llm",
        client=client,
    )
    row = _load_jsonl(intra_thread_topic_summaries_artifact_path(parsed_path))[0]

    assert result["local_llm_summaries"] == 0
    assert result["local_llm_failures"] == 1
    assert row["source"] == "heuristic"
    assert "model" not in row


def test_intra_thread_topic_summaries_invalid_schema_falls_back_to_heuristic(
    tmp_path,
):
    parsed_path = _write_basic_thread_with_segments(tmp_path)
    client = FakeLLMClient(
        [
            json.dumps(
                {
                    "title": "Launch checklist schema",
                    "summary": "The segment discusses schema-backed topic summaries.",
                    "conclusion_text": None,
                    "conclusion_status": "unknown",
                    "keywords": ["schema"],
                    "confidence": 0.7,
                    "extra": "not allowed",
                }
            )
        ]
    )

    result = write_intra_thread_topic_summaries(
        parsed_path,
        source="local_llm",
        client=client,
    )
    row = _load_jsonl(intra_thread_topic_summaries_artifact_path(parsed_path))[0]

    assert result["local_llm_failures"] == 1
    assert row["source"] == "heuristic"


def test_intra_thread_topic_summaries_invalid_conclusion_status_falls_back(
    tmp_path,
):
    parsed_path = _write_basic_thread_with_segments(tmp_path)
    client = FakeLLMClient(
        [
            json.dumps(
                {
                    "title": "Launch checklist schema",
                    "summary": "The segment discusses schema-backed topic summaries.",
                    "conclusion_text": None,
                    "conclusion_status": "done",
                    "keywords": ["schema"],
                    "confidence": 0.7,
                }
            )
        ]
    )

    rows = build_intra_thread_topic_summary_rows(
        parsed_path,
        source="local_llm",
        client=client,
    )

    assert rows[0]["source"] == "heuristic"


def test_intra_thread_topic_summaries_assistant_suggestion_not_explicit(
    tmp_path,
):
    parsed_path = _write_basic_thread_with_segments(tmp_path)
    client = FakeLLMClient(
        [
            json.dumps(
                {
                    "title": "Launch checklist schema",
                    "summary": "The assistant suggests validating schemas and running tests.",
                    "conclusion_text": "Draft release notes, validate schemas, and run focused tests.",
                    "conclusion_status": "explicit",
                    "keywords": ["schema", "tests"],
                    "confidence": 0.9,
                }
            )
        ]
    )

    rows = build_intra_thread_topic_summary_rows(
        parsed_path,
        source="local_llm",
        client=client,
    )

    assert rows[0]["source"] == "heuristic"


def test_intra_thread_topic_summaries_fail_on_sha1_drift(tmp_path):
    parsed_path = _write_basic_thread_with_segments(tmp_path)
    segments_path = parsed_path.parent / "l3" / "intra-thread-topics" / "segments.jsonl"
    rows = _load_jsonl(segments_path)
    rows[0]["text_sha1"] = "0" * 40
    _write_segments_jsonl(segments_path, rows)

    with pytest.raises(IntraThreadTopicSummaryError, match="text_sha1 drift"):
        build_intra_thread_topic_summary_rows(parsed_path)


def test_intra_thread_topic_summary_schema_validation(tmp_path):
    parsed_path = _write_basic_thread_with_segments(tmp_path)
    rows = build_intra_thread_topic_summary_rows(parsed_path)
    validator = load_intra_thread_topic_summary_validator()

    assert list(validator.iter_errors(rows[0])) == []
    assert "model" not in rows[0]
    assert "prompt_variant" not in rows[0]
    assert "prompt_hash" not in rows[0]

    invalid = dict(rows[0])
    invalid["conclusion_status"] = "done"
    errors = list(validator.iter_errors(invalid))

    assert errors


def test_intra_thread_topic_summary_schema_accepts_local_llm_provenance(tmp_path):
    parsed_path = _write_basic_thread_with_segments(tmp_path)
    row = build_intra_thread_topic_summary_rows(parsed_path)[0]
    row["source"] = "local_llm"
    row["model"] = "ollama/test-model"
    row["prompt_variant"] = "intra_thread_topic_summary_v0"
    row["prompt_hash"] = "sha256:" + ("a" * 64)

    validator = load_intra_thread_topic_summary_validator()

    assert list(validator.iter_errors(row)) == []


def test_intra_thread_topic_summary_schema_rejects_invalid_source(tmp_path):
    parsed_path = _write_basic_thread_with_segments(tmp_path)
    row = build_intra_thread_topic_summary_rows(parsed_path)[0]
    row["source"] = "api_llm"

    validator = load_intra_thread_topic_summary_validator()
    errors = list(validator.iter_errors(row))

    assert errors
