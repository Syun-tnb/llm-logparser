from __future__ import annotations

import json
from pathlib import Path

from llm_logparser.core.analyzer_metrics import build_metrics_artifact
from llm_logparser.core.analyzer_tokens import (
    build_token_stats_artifact,
    write_token_stats_artifact,
)
from llm_logparser.core.i18n import set_locale
from llm_logparser.core.schema_validation import (
    load_metrics_validator,
    load_token_stats_validator,
)


def _write_parsed_jsonl(
    path: Path,
    conversation_id: str,
    messages: list[dict],
    *,
    provider_id: str = "openai",
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
        for idx, message in enumerate(messages, start=1):
            row = {
                "record_type": "message",
                "provider_id": provider_id,
                "conversation_id": conversation_id,
                "message_id": message.get("message_id", f"m{idx}"),
                "role": message.get("role", "assistant"),
                "ts": message.get("ts", 1704067200000 + idx),
            }
            if "content" in message:
                row["content"] = message["content"]
            elif "text" in message and isinstance(message.get("text"), str):
                row["content"] = {"content_type": "text", "parts": [message["text"]]}
            else:
                row["content"] = {"content_type": "text", "parts": []}

            if "text" in message:
                row["text"] = message["text"]
            if "meta" in message:
                row["meta"] = message["meta"]

            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def _load_snapshot(name: str) -> dict:
    snapshot_path = Path(__file__).parent / "fixtures" / name
    return json.loads(snapshot_path.read_text(encoding="utf-8"))


def _normalize_token_stats_snapshot_fields(artifact: dict) -> dict:
    normalized = json.loads(json.dumps(artifact))
    normalized["tokenizer"]["library_version"] = "<tiktoken-version>"
    return normalized


def test_token_stats_artifact_matches_schema_and_snapshot(tmp_path):
    set_locale("en-US")
    parsed = tmp_path / "thread-token-contract" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-token-contract",
        [
            {"message_id": "m1", "role": "user", "text": "alpha beta"},
            {
                "message_id": "m2",
                "role": "assistant",
                "content": {"content_type": "text", "parts": ["line one", "line two"]},
            },
            {
                "message_id": "m3",
                "role": "assistant",
                "content": {"content_type": "text", "parts": []},
            },
        ],
    )

    artifact = build_token_stats_artifact(parsed, encoding_override="o200k_base")
    validator = load_token_stats_validator()
    assert list(validator.iter_errors(artifact)) == []
    assert artifact["schema_version"] == "1.0"
    assert _normalize_token_stats_snapshot_fields(artifact) == _load_snapshot(
        "token_stats_contract_snapshot.json"
    )


def test_metrics_artifact_matches_schema_and_snapshot(tmp_path):
    set_locale("en-US")
    parsed = tmp_path / "thread-metrics-contract" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-metrics-contract",
        [
            {
                "message_id": "m1",
                "role": "user",
                "text": "Please summarize this article in three bullets.",
            },
            {
                "message_id": "m2",
                "role": "assistant",
                "text": "I can't help with that request.",
            },
            {
                "message_id": "m3",
                "role": "user",
                "text": "In other words, summarize only the safe parts.",
            },
        ],
    )

    token_stats = build_token_stats_artifact(parsed, encoding_override="o200k_base")
    write_token_stats_artifact(parsed, token_stats)
    artifact = build_metrics_artifact(parsed)

    validator = load_metrics_validator()
    assert list(validator.iter_errors(artifact)) == []
    assert artifact["schema_version"] == "1.0"
    assert artifact == _load_snapshot("metrics_contract_snapshot.json")


def test_token_stats_schema_requires_schema_version(tmp_path):
    set_locale("en-US")
    parsed = tmp_path / "thread-token-invalid" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-token-invalid",
        [{"message_id": "m1", "role": "user", "text": "hello"}],
    )

    artifact = build_token_stats_artifact(parsed, encoding_override="o200k_base")
    artifact.pop("schema_version")

    validator = load_token_stats_validator()
    errors = list(validator.iter_errors(artifact))

    assert errors
    assert any(error.validator == "required" for error in errors)


def test_metrics_schema_requires_schema_version(tmp_path):
    set_locale("en-US")
    parsed = tmp_path / "thread-metrics-invalid" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-metrics-invalid",
        [
            {"message_id": "m1", "role": "user", "text": "hello"},
            {"message_id": "m2", "role": "assistant", "text": "world"},
        ],
    )

    token_stats = build_token_stats_artifact(parsed, encoding_override="o200k_base")
    write_token_stats_artifact(parsed, token_stats)
    artifact = build_metrics_artifact(parsed)
    artifact.pop("schema_version")

    validator = load_metrics_validator()
    errors = list(validator.iter_errors(artifact))

    assert errors
    assert any(error.validator == "required" for error in errors)
