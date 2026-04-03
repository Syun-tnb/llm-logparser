import json
import sys
from pathlib import Path

import pytest
import tiktoken

from llm_logparser.cli.cli import main


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
                row["content"] = {
                    "content_type": "text",
                    "parts": [message["text"]],
                }
            else:
                row["content"] = {"content_type": "text", "parts": []}

            if "text" in message:
                row["text"] = message["text"]
            if "meta" in message:
                row["meta"] = message["meta"]

            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def _run_cli(monkeypatch, argv: list[str]) -> None:
    monkeypatch.setattr(sys, "argv", argv)
    main()


def _load_artifact(parsed_path: Path) -> dict:
    return json.loads(parsed_path.with_name("token_stats.json").read_text(encoding="utf-8"))


def _write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_analyze_tokens_writes_deterministic_artifact(tmp_path, monkeypatch):
    parsed = tmp_path / "thread-conv-1" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-1",
        [
            {"message_id": "m1", "role": "user", "text": "hello"},
            {"message_id": "m2", "role": "assistant", "text": "world"},
        ],
    )

    argv = [
        "llm-logparser",
        "analyze",
        "tokens",
        "--input",
        str(parsed),
        "--encoding",
        "o200k_base",
    ]
    _run_cli(monkeypatch, argv)
    first = parsed.with_name("token_stats.json").read_text(encoding="utf-8")

    _run_cli(monkeypatch, argv)
    second = parsed.with_name("token_stats.json").read_text(encoding="utf-8")

    assert first == second


def test_analyze_tokens_role_aggregation_and_turn_count(tmp_path, monkeypatch):
    parsed = tmp_path / "thread-conv-roles" / "parsed.jsonl"
    messages = [
        {"message_id": "m1", "role": "system", "text": "system setup"},
        {"message_id": "m2", "role": "user", "text": "hello there"},
        {"message_id": "m3", "role": "assistant", "text": "general kenobi"},
        {"message_id": "m4", "role": "tool", "text": "tool output"},
        {"message_id": "m5", "role": "moderator", "text": "flagged"},
        {"message_id": "m6", "role": "user", "text": "follow up"},
    ]
    _write_parsed_jsonl(parsed, "conv-roles", messages)

    _run_cli(
        monkeypatch,
        [
            "llm-logparser",
            "analyze",
            "tokens",
            "--input",
            str(parsed),
            "--encoding",
            "o200k_base",
        ],
    )

    artifact = _load_artifact(parsed)
    enc = tiktoken.get_encoding("o200k_base")
    expected_counts = {
        item["message_id"]: len(enc.encode_ordinary(item["text"])) for item in messages
    }

    assert artifact["artifact_type"] == "token_stats"
    assert artifact["conversation_id"] == "conv-roles"
    assert artifact["summary"]["message_count"] == 6
    assert artifact["summary"]["turn_count"] == 2
    assert artifact["summary"]["tokens_total"] == sum(expected_counts.values())
    assert artifact["summary"]["tokens_user"] == expected_counts["m2"] + expected_counts["m6"]
    assert artifact["summary"]["tokens_assistant"] == expected_counts["m3"]
    assert artifact["summary"]["avg_tokens_per_message"] == round(
        sum(expected_counts.values()) / 6, 2
    )
    assert artifact["summary"]["avg_tokens_per_turn"] == round(
        sum(expected_counts.values()) / 2, 2
    )
    assert artifact["by_role"]["user"] == {
        "messages": 2,
        "tokens": expected_counts["m2"] + expected_counts["m6"],
    }
    assert artifact["by_role"]["assistant"] == {
        "messages": 1,
        "tokens": expected_counts["m3"],
    }
    assert artifact["by_role"]["system"] == {
        "messages": 1,
        "tokens": expected_counts["m1"],
    }
    assert artifact["by_role"]["tool"] == {
        "messages": 1,
        "tokens": expected_counts["m4"],
    }
    assert artifact["by_role"]["unknown"] == {
        "messages": 1,
        "tokens": expected_counts["m5"],
    }
    assert artifact["messages"][1]["text_source"] == "text"
    assert artifact["messages"][1]["token_count"] == expected_counts["m2"]


def test_analyze_tokens_falls_back_to_content_parts(tmp_path, monkeypatch):
    parsed = tmp_path / "thread-conv-fallback" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-fallback",
        [
            {
                "message_id": "m1",
                "role": "user",
                "content": {"content_type": "text", "parts": ["alpha", "beta"]},
            }
        ],
    )

    _run_cli(
        monkeypatch,
        [
            "llm-logparser",
            "analyze",
            "tokens",
            "--input",
            str(parsed),
            "--encoding",
            "o200k_base",
        ],
    )

    artifact = _load_artifact(parsed)
    enc = tiktoken.get_encoding("o200k_base")
    expected = len(enc.encode_ordinary("alpha\nbeta"))

    assert artifact["summary"]["fallback_text_from_parts"] == 1
    assert artifact["summary"]["empty_text_messages"] == 0
    assert artifact["messages"] == [
        {
            "message_id": "m1",
            "role": "user",
            "token_count": expected,
            "text_source": "content.parts",
        }
    ]


def test_analyze_tokens_prefers_text_over_legacy_content_parts(tmp_path, monkeypatch):
    parsed = tmp_path / "thread-conv-prefer-text" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-prefer-text",
        [
            {
                "message_id": "m1",
                "role": "user",
                "text": "canonical",
                "content": {"content_type": "text", "parts": ["legacy"]},
            }
        ],
    )

    _run_cli(
        monkeypatch,
        [
            "llm-logparser",
            "analyze",
            "tokens",
            "--input",
            str(parsed),
            "--encoding",
            "o200k_base",
        ],
    )

    artifact = _load_artifact(parsed)
    assert artifact["summary"]["fallback_text_from_parts"] == 0
    assert artifact["messages"][0]["text_source"] == "text"


def test_analyze_tokens_counts_empty_text_messages(tmp_path, monkeypatch):
    parsed = tmp_path / "thread-conv-empty" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-empty",
        [
            {
                "message_id": "m1",
                "role": "assistant",
                "text": None,
                "content": {"content_type": "text", "parts": []},
            }
        ],
    )

    _run_cli(
        monkeypatch,
        [
            "llm-logparser",
            "analyze",
            "tokens",
            "--input",
            str(parsed),
            "--encoding",
            "o200k_base",
        ],
    )

    artifact = _load_artifact(parsed)

    assert artifact["summary"]["tokens_total"] == 0
    assert artifact["summary"]["empty_text_messages"] == 1
    assert artifact["messages"][0]["text_source"] == "empty"
    assert artifact["messages"][0]["token_count"] == 0


def test_analyze_tokens_fails_for_unsupported_provider_without_override(
    tmp_path, monkeypatch
):
    parsed = tmp_path / "thread-conv-google" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-google",
        [{"message_id": "m1", "role": "user", "text": "hello"}],
        provider_id="google",
    )

    with pytest.raises(SystemExit) as exc:
        _run_cli(
            monkeypatch,
            [
                "llm-logparser",
                "analyze",
                "tokens",
                "--input",
                str(parsed),
            ],
        )

    assert "unsupported provider for analyze tokens: google" in str(exc.value)


def test_analyze_tokens_allows_encoding_override_for_unsupported_provider(
    tmp_path, monkeypatch
):
    parsed = tmp_path / "thread-conv-google-override" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-google-override",
        [{"message_id": "m1", "role": "user", "text": "hello"}],
        provider_id="google",
    )

    _run_cli(
        monkeypatch,
        [
            "llm-logparser",
            "analyze",
            "tokens",
            "--input",
            str(parsed),
            "--encoding",
            "o200k_base",
        ],
    )

    artifact = _load_artifact(parsed)
    assert artifact["provider_id"] == "google"
    assert artifact["tokenizer"]["resolved_encoding"] == "o200k_base"
    assert artifact["tokenizer"]["resolution_source"] == "explicit_encoding"


def test_analyze_tokens_auto_detects_model_from_message_metadata(tmp_path, monkeypatch):
    parsed = tmp_path / "thread-conv-model-auto" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-model-auto",
        [
            {
                "message_id": "m1",
                "role": "user",
                "text": "hello",
                "meta": {"model": "gpt-4o-mini"},
            },
            {"message_id": "m2", "role": "assistant", "text": "world"},
        ],
    )

    _run_cli(
        monkeypatch,
        [
            "llm-logparser",
            "analyze",
            "tokens",
            "--input",
            str(parsed),
        ],
    )

    artifact = _load_artifact(parsed)
    assert artifact["tokenizer"]["resolved_model"] == "gpt-4o-mini"
    assert artifact["tokenizer"]["resolution_source"] == "model"
    assert artifact["tokenizer"]["resolved_encoding"] == "o200k_base"


def test_analyze_tokens_auto_detects_first_model_signal_during_main_pass(
    tmp_path, monkeypatch
):
    parsed = tmp_path / "thread-conv-model-late" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-model-late",
        [
            {"message_id": "m1", "role": "user", "text": "hello"},
            {
                "message_id": "m2",
                "role": "assistant",
                "text": "world",
                "meta": {"model": "gpt-4o-mini"},
            },
        ],
    )

    _run_cli(
        monkeypatch,
        [
            "llm-logparser",
            "analyze",
            "tokens",
            "--input",
            str(parsed),
        ],
    )

    artifact = _load_artifact(parsed)
    assert artifact["tokenizer"]["resolved_model"] == "gpt-4o-mini"
    assert artifact["tokenizer"]["resolution_source"] == "model"
    assert artifact["tokenizer"]["resolved_encoding"] == "o200k_base"


def test_analyze_tokens_without_model_metadata_uses_provider_default_resolution(
    tmp_path, monkeypatch
):
    parsed = tmp_path / "thread-conv-provider-default" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-provider-default",
        [
            {"message_id": "m1", "role": "user", "text": "hello"},
            {"message_id": "m2", "role": "assistant", "text": "world"},
        ],
    )

    _run_cli(
        monkeypatch,
        [
            "llm-logparser",
            "analyze",
            "tokens",
            "--input",
            str(parsed),
        ],
    )

    artifact = _load_artifact(parsed)
    assert artifact["tokenizer"]["resolved_model"] is None
    assert artifact["tokenizer"]["resolution_source"] == "provider_default"
    assert artifact["tokenizer"]["resolved_encoding"] == "o200k_base"


def test_analyze_tokens_skip_existing_keeps_single_artifact_unchanged(tmp_path, monkeypatch):
    parsed = tmp_path / "thread-conv-skip" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-skip",
        [{"message_id": "m1", "role": "user", "text": "hello"}],
    )
    artifact_path = parsed.with_name("token_stats.json")
    original = '{"sentinel": "keep-me"}\n'
    _write_text(artifact_path, original)

    _run_cli(
        monkeypatch,
        [
            "llm-logparser",
            "analyze",
            "tokens",
            "--input",
            str(parsed),
            "--encoding",
            "o200k_base",
            "--skip-existing",
        ],
    )

    assert artifact_path.read_text(encoding="utf-8") == original


def test_analyze_tokens_without_skip_existing_overwrites_existing_artifact(
    tmp_path, monkeypatch
):
    parsed = tmp_path / "thread-conv-overwrite" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-overwrite",
        [{"message_id": "m1", "role": "user", "text": "hello"}],
    )
    artifact_path = parsed.with_name("token_stats.json")
    _write_text(artifact_path, '{"sentinel": "replace-me"}\n')

    _run_cli(
        monkeypatch,
        [
            "llm-logparser",
            "analyze",
            "tokens",
            "--input",
            str(parsed),
            "--encoding",
            "o200k_base",
        ],
    )

    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["artifact_type"] == "token_stats"
    assert payload["conversation_id"] == "conv-overwrite"
    assert payload["schema_version"] == "1.0"


def test_analyze_tokens_skip_existing_supports_directory_input(tmp_path, monkeypatch):
    root = tmp_path / "parsed"
    parsed_a = root / "a" / "thread-a" / "parsed.jsonl"
    parsed_b = root / "b" / "thread-b" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed_a,
        "conv-a",
        [{"message_id": "m1", "role": "user", "text": "one"}],
    )
    _write_parsed_jsonl(
        parsed_b,
        "conv-b",
        [{"message_id": "m1", "role": "assistant", "text": "two"}],
    )
    original = '{"sentinel": "preserve-a"}\n'
    _write_text(parsed_a.with_name("token_stats.json"), original)

    _run_cli(
        monkeypatch,
        [
            "llm-logparser",
            "--locale",
            "en-US",
            "analyze",
            "tokens",
            "--input",
            str(root),
            "--encoding",
            "o200k_base",
            "--skip-existing",
        ],
    )

    assert parsed_a.with_name("token_stats.json").read_text(encoding="utf-8") == original
    assert _load_artifact(parsed_b)["conversation_id"] == "conv-b"


def test_analyze_tokens_dry_run_does_not_write_and_reports_counts(
    tmp_path, monkeypatch, caplog
):
    root = tmp_path / "parsed"
    parsed_a = root / "a" / "thread-a" / "parsed.jsonl"
    parsed_b = root / "b" / "thread-b" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed_a,
        "conv-a",
        [{"message_id": "m1", "role": "user", "text": "one"}],
    )
    _write_parsed_jsonl(
        parsed_b,
        "conv-b",
        [{"message_id": "m1", "role": "assistant", "text": "two"}],
    )
    _write_text(parsed_a.with_name("token_stats.json"), '{"sentinel": "preserve-a"}\n')

    _run_cli(
        monkeypatch,
        [
            "llm-logparser",
            "--locale",
            "en-US",
            "analyze",
            "tokens",
            "--input",
            str(root),
            "--encoding",
            "o200k_base",
            "--skip-existing",
            "--dry-run",
        ],
    )

    assert parsed_a.with_name("token_stats.json").read_text(encoding="utf-8") == '{"sentinel": "preserve-a"}\n'
    assert not parsed_b.with_name("token_stats.json").exists()
    assert "Previewing token_stats.json generation" in caplog.text
    assert "Detected threads: 2" in caplog.text
    assert "Existing sidecars: 1" in caplog.text
    assert "New sidecars to create: 1" in caplog.text
    assert "Skipped existing sidecars: 1" in caplog.text
    assert "No files written." in caplog.text
