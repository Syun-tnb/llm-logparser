from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from llm_logparser.core.analyzer_intra_thread_topic_summaries import (
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


def _load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


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
    assert "launch" in rows[0]["keywords"]
    assert rows[0]["confidence"] == 0.3


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

    invalid = dict(rows[0])
    invalid["conclusion_status"] = "done"
    errors = list(validator.iter_errors(invalid))

    assert errors
