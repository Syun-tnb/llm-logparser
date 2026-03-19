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
                row["content"] = {"content_type": "text", "parts": [message["text"]]}
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


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _build_metrics_fixture(parsed: Path, monkeypatch) -> tuple[dict, dict]:
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
    _run_cli(
        monkeypatch,
        [
            "llm-logparser",
            "analyze",
            "metrics",
            "--input",
            str(parsed),
        ],
    )
    return (
        _load_json(parsed.with_name("token_stats.json")),
        _load_json(parsed.with_name("metrics.json")),
    )


def test_analyze_metrics_writes_deterministic_artifact(tmp_path, monkeypatch):
    parsed = tmp_path / "thread-conv-1" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-1",
        [
            {"message_id": "m1", "role": "user", "text": "hello world"},
            {"message_id": "m2", "role": "assistant", "text": "hi there"},
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
    argv = ["llm-logparser", "analyze", "metrics", "--input", str(parsed)]
    _run_cli(monkeypatch, argv)
    first = parsed.with_name("metrics.json").read_text(encoding="utf-8")

    _run_cli(monkeypatch, argv)
    second = parsed.with_name("metrics.json").read_text(encoding="utf-8")

    assert first == second


def test_analyze_metrics_calculates_ratios_and_character_counts(tmp_path, monkeypatch):
    parsed = tmp_path / "thread-conv-ratios" / "parsed.jsonl"
    messages = [
        {"message_id": "m1", "role": "user", "text": "alpha beta"},
        {"message_id": "m2", "role": "assistant", "text": "gamma delta epsilon"},
        {"message_id": "m3", "role": "user", "text": "zeta"},
    ]
    _write_parsed_jsonl(parsed, "conv-ratios", messages)

    token_stats, metrics = _build_metrics_fixture(parsed, monkeypatch)

    char_total = sum(len(item["text"]) for item in messages)
    char_user = len("alpha beta") + len("zeta")
    char_assistant = len("gamma delta epsilon")

    assert metrics["artifact_type"] == "metrics"
    assert metrics["conversation_id"] == "conv-ratios"
    assert metrics["tokens"]["total"] == token_stats["summary"]["tokens_total"]
    assert metrics["tokens"]["avg_per_message"] == token_stats["summary"]["avg_tokens_per_message"]
    assert metrics["tokens"]["avg_per_turn"] == token_stats["summary"]["avg_tokens_per_turn"]
    assert metrics["characters"]["total"] == char_total
    assert metrics["characters"]["user"] == char_user
    assert metrics["characters"]["assistant"] == char_assistant
    assert metrics["characters"]["avg_per_message"] == round(char_total / 3, 2)
    assert metrics["characters"]["avg_per_turn"] == round(char_total / 2, 2)
    assert metrics["distribution"]["message_total"] == 3
    assert metrics["distribution"]["message_user"] == 2
    assert metrics["distribution"]["message_assistant"] == 1
    assert metrics["distribution"]["messages_per_turn"] == 1.5
    assert metrics["ratios"]["prompt_response_ratio_tokens"] == round(
        token_stats["summary"]["tokens_user"] / token_stats["summary"]["tokens_assistant"],
        4,
    )
    assert metrics["ratios"]["prompt_response_ratio_chars"] == round(
        char_user / char_assistant,
        4,
    )
    assert metrics["ratios"]["assistant_to_user_ratio"] == 0.5


def test_analyze_metrics_handles_zero_division_consistently(tmp_path, monkeypatch):
    parsed = tmp_path / "thread-conv-zero" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-zero",
        [
            {"message_id": "m1", "role": "user", "text": "hello"},
            {"message_id": "m2", "role": "system", "text": "setup"},
        ],
    )

    _token_stats, metrics = _build_metrics_fixture(parsed, monkeypatch)

    assert metrics["ratios"]["prompt_response_ratio_tokens"] == 0.0
    assert metrics["ratios"]["prompt_response_ratio_chars"] == 0.0
    assert metrics["ratios"]["assistant_to_user_ratio"] == 0.0


def test_analyze_metrics_computes_diversity_from_tokenizer_units(tmp_path, monkeypatch):
    parsed = tmp_path / "thread-conv-diversity" / "parsed.jsonl"
    messages = [
        {"message_id": "m1", "role": "user", "text": "alpha beta"},
        {"message_id": "m2", "role": "assistant", "text": "alpha gamma"},
    ]
    _write_parsed_jsonl(parsed, "conv-diversity", messages)

    _token_stats, metrics = _build_metrics_fixture(parsed, monkeypatch)

    enc = tiktoken.get_encoding("o200k_base")
    all_token_ids: list[int] = []
    for item in messages:
        all_token_ids.extend(enc.encode_ordinary(item["text"]))
    expected_ratio = round(len(set(all_token_ids)) / len(all_token_ids), 4)

    assert metrics["diversity"]["type_token_ratio"] == expected_ratio
    assert metrics["diversity"]["unique_token_ratio"] == expected_ratio


def test_analyze_metrics_supports_directory_input(tmp_path, monkeypatch):
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
        [
            {"message_id": "m1", "role": "user", "text": "two"},
            {"message_id": "m2", "role": "assistant", "text": "three"},
        ],
    )

    _run_cli(
        monkeypatch,
        [
            "llm-logparser",
            "analyze",
            "tokens",
            "--input",
            str(root),
            "--encoding",
            "o200k_base",
        ],
    )
    _run_cli(
        monkeypatch,
        [
            "llm-logparser",
            "analyze",
            "metrics",
            "--input",
            str(root),
        ],
    )

    assert parsed_a.with_name("metrics.json").exists()
    assert parsed_b.with_name("metrics.json").exists()
    assert _load_json(parsed_a.with_name("metrics.json"))["conversation_id"] == "conv-a"
    assert _load_json(parsed_b.with_name("metrics.json"))["conversation_id"] == "conv-b"


def test_analyze_metrics_fails_when_token_stats_is_missing(
    tmp_path, monkeypatch, caplog
):
    parsed = tmp_path / "thread-conv-missing" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-missing",
        [{"message_id": "m1", "role": "user", "text": "hello"}],
    )

    monkeypatch.setattr(
        sys,
        "argv",
        ["llm-logparser", "analyze", "metrics", "--input", str(parsed)],
    )

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 2
    assert "required token_stats.json not found" in caplog.text

