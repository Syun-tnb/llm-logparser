import json
import sys
from pathlib import Path

import pytest
import tiktoken

from llm_logparser.cli.cli import main


@pytest.fixture(autouse=True)
def _isolate_from_repo_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


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


def _write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _metrics_artifact_text(parsed: Path) -> str:
    return parsed.with_name("metrics.json").read_text(encoding="utf-8")


def _build_metrics_fixture(
    parsed: Path,
    monkeypatch,
    *,
    metrics_args: list[str] | None = None,
) -> tuple[dict, dict]:
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
            *(metrics_args or []),
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
            {
                "message_id": "m2",
                "role": "assistant",
                "text": "I can't help with that request.",
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


def test_analyze_metrics_counts_rapid_revisions(tmp_path, monkeypatch):
    parsed = tmp_path / "thread-conv-rapid-revision" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-rapid-revision",
        [
            {"message_id": "m1", "role": "assistant", "text": "Draft answer", "ts": 1_704_067_200_000},
            {"message_id": "m2", "role": "user", "text": "Please change it", "ts": 1_704_067_230_000},
        ],
    )

    _token_stats, metrics = _build_metrics_fixture(parsed, monkeypatch)

    assert metrics["user_effort"]["rapid_revisions"] == 1
    assert metrics["user_effort"]["response_length_ratio"] == round(
        len("Draft answer") / len("Please change it"),
        4,
    )
    assert metrics["user_effort"]["negative_deltas"] == 0
    assert metrics["user_effort"]["human_read_time"] == {
        "avg_seconds": 30,
        "median_seconds": 30,
        "min_seconds": 30,
        "max_seconds": 30,
        "sample_count": 1,
        "excluded_long_gaps": 0,
        "session_gap_seconds": 3600,
    }


def test_analyze_metrics_excludes_long_read_gaps(tmp_path, monkeypatch):
    parsed = tmp_path / "thread-conv-long-gap" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-long-gap",
        [
            {"message_id": "m1", "role": "assistant", "text": "Long gap answer", "ts": 1_704_067_200_000},
            {"message_id": "m2", "role": "user", "text": "Delayed reply", "ts": 1_704_071_200_000},
        ],
    )

    _token_stats, metrics = _build_metrics_fixture(parsed, monkeypatch)

    assert metrics["user_effort"]["rapid_revisions"] == 0
    assert metrics["user_effort"]["negative_deltas"] == 0
    assert metrics["user_effort"]["human_read_time"] == {
        "avg_seconds": None,
        "median_seconds": None,
        "min_seconds": None,
        "max_seconds": None,
        "sample_count": 0,
        "excluded_long_gaps": 1,
        "session_gap_seconds": 3600,
    }


def test_analyze_metrics_user_effort_handles_mixed_valid_and_excluded_pairs(
    tmp_path, monkeypatch
):
    parsed = tmp_path / "thread-conv-user-effort-mixed" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-user-effort-mixed",
        [
            {"message_id": "m1", "role": "assistant", "text": "Answer one", "ts": 1_704_067_200_000},
            {"message_id": "m2", "role": "user", "text": "Reply one", "ts": 1_704_067_320_000},
            {"message_id": "m3", "role": "assistant", "text": "Answer two", "ts": 1_704_067_400_000},
            {"message_id": "m4", "role": "user", "text": "Reply two", "ts": 1_704_071_500_000},
        ],
    )

    _token_stats, metrics = _build_metrics_fixture(parsed, monkeypatch)

    assert metrics["user_effort"]["rapid_revisions"] == 0
    assert metrics["user_effort"]["negative_deltas"] == 0
    assert metrics["user_effort"]["human_read_time"] == {
        "avg_seconds": 120,
        "median_seconds": 120,
        "min_seconds": 120,
        "max_seconds": 120,
        "sample_count": 1,
        "excluded_long_gaps": 1,
        "session_gap_seconds": 3600,
    }


def test_analyze_metrics_user_effort_ratio_is_null_without_user_text(tmp_path, monkeypatch):
    parsed = tmp_path / "thread-conv-user-effort-no-user" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-user-effort-no-user",
        [
            {"message_id": "m1", "role": "assistant", "text": "hello"},
            {"message_id": "m2", "role": "system", "text": "setup"},
        ],
    )

    _token_stats, metrics = _build_metrics_fixture(parsed, monkeypatch)

    assert metrics["user_effort"]["response_length_ratio"] is None
    assert metrics["user_effort"]["human_read_time"]["sample_count"] == 0


def test_analyze_metrics_user_effort_ignores_missing_timestamps_safely(
    tmp_path, monkeypatch
):
    parsed = tmp_path / "thread-conv-user-effort-missing-ts" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-user-effort-missing-ts",
        [
            {"message_id": "m1", "role": "assistant", "text": "hello", "ts": None},
            {"message_id": "m2", "role": "user", "text": "reply", "ts": 1_704_067_230_000},
        ],
    )

    _token_stats, metrics = _build_metrics_fixture(parsed, monkeypatch)

    assert metrics["user_effort"]["rapid_revisions"] == 0
    assert metrics["user_effort"]["negative_deltas"] == 0
    assert metrics["user_effort"]["human_read_time"] == {
        "avg_seconds": None,
        "median_seconds": None,
        "min_seconds": None,
        "max_seconds": None,
        "sample_count": 0,
        "excluded_long_gaps": 0,
        "session_gap_seconds": 3600,
    }


def test_analyze_metrics_does_not_parse_iso_timestamp_strings(tmp_path, monkeypatch):
    parsed = tmp_path / "thread-conv-user-effort-iso-ts" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-user-effort-iso-ts",
        [
            {
                "message_id": "m1",
                "role": "assistant",
                "text": "hello",
                "ts": "2024-01-01T00:00:00Z",
            },
            {
                "message_id": "m2",
                "role": "user",
                "text": "reply",
                "ts": "2024-01-01T00:00:30Z",
            },
        ],
    )

    _token_stats, metrics = _build_metrics_fixture(parsed, monkeypatch)

    assert metrics["user_effort"]["rapid_revisions"] == 0
    assert metrics["user_effort"]["negative_deltas"] == 0
    assert metrics["user_effort"]["human_read_time"]["sample_count"] == 0


def test_analyze_metrics_user_effort_handles_non_alternating_roles(
    tmp_path, monkeypatch
):
    parsed = tmp_path / "thread-conv-user-effort-nonalternating" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-user-effort-nonalternating",
        [
            {"message_id": "m1", "role": "assistant", "text": "first answer", "ts": 1_704_067_200_000},
            {"message_id": "m2", "role": "assistant", "text": "second answer", "ts": 1_704_067_220_000},
            {"message_id": "m3", "role": "user", "text": "follow-up", "ts": 1_704_067_250_000},
        ],
    )

    _token_stats, metrics = _build_metrics_fixture(parsed, monkeypatch)

    assert metrics["user_effort"]["rapid_revisions"] == 1
    assert metrics["user_effort"]["negative_deltas"] == 0
    assert metrics["user_effort"]["human_read_time"] == {
        "avg_seconds": 30,
        "median_seconds": 30,
        "min_seconds": 30,
        "max_seconds": 30,
        "sample_count": 1,
        "excluded_long_gaps": 0,
        "session_gap_seconds": 3600,
    }


def test_analyze_metrics_tracks_negative_deltas_without_affecting_read_time(
    tmp_path, monkeypatch
):
    parsed = tmp_path / "thread-conv-user-effort-negative-delta" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-user-effort-negative-delta",
        [
            {"message_id": "m1", "role": "assistant", "text": "Draft answer", "ts": 200_000},
            {"message_id": "m2", "role": "user", "text": "Please revise", "ts": 150_000},
        ],
    )

    _token_stats, metrics = _build_metrics_fixture(parsed, monkeypatch)

    assert metrics["user_effort"]["negative_deltas"] == 1
    assert metrics["user_effort"]["rapid_revisions"] == 0
    assert metrics["user_effort"]["human_read_time"] == {
        "avg_seconds": None,
        "median_seconds": None,
        "min_seconds": None,
        "max_seconds": None,
        "sample_count": 0,
        "excluded_long_gaps": 0,
        "session_gap_seconds": 3600,
    }


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
    assert metrics["safety"]["refusal_rate"] == 0.0
    assert metrics["interaction"]["revision_rate"] == 0.0


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
            "--locale",
            "en-US",
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


def test_analyze_metrics_detects_refusal_in_assistant_message(tmp_path, monkeypatch):
    parsed = tmp_path / "thread-conv-refusal" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-refusal",
        [
            {"message_id": "m1", "role": "user", "text": "please do the thing"},
            {
                "message_id": "m2",
                "role": "assistant",
                "text": "I can't help with that request.",
            },
        ],
    )

    _token_stats, metrics = _build_metrics_fixture(parsed, monkeypatch)

    assert metrics["safety"]["refusal_count"] == 1
    assert metrics["safety"]["refusal_rate"] == 1.0
    assert metrics["safety"]["intervention_count"] == 1
    assert metrics["safety"]["intervention_rate"] == 1.0
    assert metrics["safety"]["trigger_types"] == {"refusal": 1, "caveat": 0}


def test_analyze_metrics_refusal_count_stays_zero_without_match(tmp_path, monkeypatch):
    parsed = tmp_path / "thread-conv-no-refusal" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-no-refusal",
        [
            {"message_id": "m1", "role": "user", "text": "hello"},
            {"message_id": "m2", "role": "assistant", "text": "Here is the answer."},
        ],
    )

    _token_stats, metrics = _build_metrics_fixture(parsed, monkeypatch)

    assert metrics["safety"]["refusal_count"] == 0
    assert metrics["safety"]["refusal_rate"] == 0.0
    assert metrics["safety"]["intervention_count"] == 0
    assert metrics["safety"]["intervention_rate"] == 0.0
    assert metrics["safety"]["trigger_types"] == {"refusal": 0, "caveat": 0}


def test_analyze_metrics_does_not_count_user_refusal_like_text(tmp_path, monkeypatch):
    parsed = tmp_path / "thread-conv-user-refusal" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-user-refusal",
        [
            {
                "message_id": "m1",
                "role": "user",
                "text": "I can't help with that request.",
            },
            {"message_id": "m2", "role": "assistant", "text": "Sure, here you go."},
        ],
    )

    _token_stats, metrics = _build_metrics_fixture(parsed, monkeypatch)

    assert metrics["safety"]["refusal_count"] == 0
    assert metrics["safety"]["refusal_rate"] == 0.0
    assert metrics["safety"]["intervention_count"] == 0
    assert metrics["safety"]["intervention_rate"] == 0.0
    assert metrics["safety"]["trigger_types"] == {"refusal": 0, "caveat": 0}


def test_analyze_metrics_computes_partial_refusal_rate(tmp_path, monkeypatch):
    parsed = tmp_path / "thread-conv-partial-refusal" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-partial-refusal",
        [
            {"message_id": "m1", "role": "user", "text": "hello"},
            {
                "message_id": "m2",
                "role": "assistant",
                "text": "I cannot provide that information.",
            },
            {"message_id": "m3", "role": "assistant", "text": "But I can explain the safe version."},
        ],
    )

    _token_stats, metrics = _build_metrics_fixture(parsed, monkeypatch)

    assert metrics["safety"]["refusal_count"] == 1
    assert metrics["safety"]["refusal_rate"] == 0.5
    assert metrics["safety"]["intervention_count"] == 1
    assert metrics["safety"]["intervention_rate"] == 0.5
    assert metrics["safety"]["trigger_types"] == {"refusal": 1, "caveat": 0}


def test_analyze_metrics_output_is_identical_across_locales(tmp_path, monkeypatch):
    parsed = tmp_path / "thread-conv-cross-locale" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-cross-locale",
        [
            {"message_id": "m1", "role": "user", "text": "Explain recursion."},
            {
                "message_id": "m2",
                "role": "assistant",
                "text": "I can't help with that request, but I can provide safer alternatives.",
            },
            {
                "message_id": "m3",
                "role": "user",
                "text": "To clarify, explain it using a factorial example instead.",
            },
        ],
    )

    _build_metrics_fixture(
        parsed,
        monkeypatch,
        metrics_args=["--locale", "en-US"],
    )
    first = _metrics_artifact_text(parsed)

    _build_metrics_fixture(
        parsed,
        monkeypatch,
        metrics_args=["--locale", "ja-JP"],
    )
    second = _metrics_artifact_text(parsed)

    assert first == second


def test_analyze_metrics_detects_caveat_only_intervention(tmp_path, monkeypatch):
    parsed = tmp_path / "thread-conv-caveat-only" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-caveat-only",
        [
            {"message_id": "m1", "role": "user", "text": "Explain the risks."},
            {
                "message_id": "m2",
                "role": "assistant",
                "text": "It's important to note that you should validate the source first.",
            },
        ],
    )

    _token_stats, metrics = _build_metrics_fixture(parsed, monkeypatch)

    assert metrics["safety"]["refusal_count"] == 0
    assert metrics["safety"]["refusal_rate"] == 0.0
    assert metrics["safety"]["intervention_count"] == 1
    assert metrics["safety"]["intervention_rate"] == 1.0
    assert metrics["safety"]["trigger_types"] == {"refusal": 0, "caveat": 1}


def test_analyze_metrics_counts_both_trigger_types_in_one_message(tmp_path, monkeypatch):
    parsed = tmp_path / "thread-conv-safety-overlap" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-safety-overlap",
        [
            {"message_id": "m1", "role": "user", "text": "Help with this unsafe action."},
            {
                "message_id": "m2",
                "role": "assistant",
                "text": "I can't help with that request, but I can provide safer alternatives.",
            },
        ],
    )

    _token_stats, metrics = _build_metrics_fixture(parsed, monkeypatch)

    assert metrics["safety"]["refusal_count"] == 1
    assert metrics["safety"]["refusal_rate"] == 1.0
    assert metrics["safety"]["intervention_count"] == 1
    assert metrics["safety"]["intervention_rate"] == 1.0
    assert metrics["safety"]["trigger_types"] == {"refusal": 1, "caveat": 1}


def test_analyze_metrics_intervention_count_stays_zero_without_safety_signal(
    tmp_path, monkeypatch
):
    parsed = tmp_path / "thread-conv-no-safety-signal" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-no-safety-signal",
        [
            {"message_id": "m1", "role": "user", "text": "hello"},
            {"message_id": "m2", "role": "assistant", "text": "Here is a normal answer."},
        ],
    )

    _token_stats, metrics = _build_metrics_fixture(parsed, monkeypatch)

    assert metrics["safety"]["refusal_count"] == 0
    assert metrics["safety"]["refusal_rate"] == 0.0
    assert metrics["safety"]["intervention_count"] == 0
    assert metrics["safety"]["intervention_rate"] == 0.0
    assert metrics["safety"]["trigger_types"] == {"refusal": 0, "caveat": 0}


def test_analyze_metrics_detects_similarity_revision_across_assistant_gap(
    tmp_path, monkeypatch
):
    parsed = tmp_path / "thread-conv-revision-similarity" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-revision-similarity",
        [
            {
                "message_id": "m1",
                "role": "user",
                "text": "Please summarize this article about climate policy in simple bullet points.",
            },
            {"message_id": "m2", "role": "assistant", "text": "Sure, here is a summary."},
            {
                "message_id": "m3",
                "role": "user",
                "text": "Please summarize this article about climate policy in simple bullet point format.",
            },
        ],
    )

    _token_stats, metrics = _build_metrics_fixture(parsed, monkeypatch)

    assert metrics["interaction"]["revision_count"] == 1
    assert metrics["interaction"]["revision_rate"] == 0.5
    assert metrics["interaction"]["retry_count"] == 1
    assert metrics["interaction"]["correction_count"] == 0
    assert metrics["interaction"]["clarification_count"] == 0


def test_analyze_metrics_does_not_count_unrelated_user_topic_change(
    tmp_path, monkeypatch
):
    parsed = tmp_path / "thread-conv-revision-unrelated" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-revision-unrelated",
        [
            {"message_id": "m1", "role": "user", "text": "Explain how DNS works."},
            {"message_id": "m2", "role": "assistant", "text": "Here is a DNS overview."},
            {"message_id": "m3", "role": "user", "text": "Now recommend a coffee grinder."},
        ],
    )

    _token_stats, metrics = _build_metrics_fixture(parsed, monkeypatch)

    assert metrics["interaction"]["revision_count"] == 0
    assert metrics["interaction"]["revision_rate"] == 0.0
    assert metrics["interaction"]["retry_count"] == 0


def test_analyze_metrics_does_not_count_very_short_user_messages_as_revisions(
    tmp_path, monkeypatch
):
    parsed = tmp_path / "thread-conv-revision-short" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-revision-short",
        [
            {"message_id": "m1", "role": "user", "text": "draft"},
            {"message_id": "m2", "role": "assistant", "text": "What do you want revised?"},
            {"message_id": "m3", "role": "user", "text": "again"},
        ],
    )

    _token_stats, metrics = _build_metrics_fixture(parsed, monkeypatch)

    assert metrics["interaction"]["revision_count"] == 0
    assert metrics["interaction"]["revision_rate"] == 0.0
    assert metrics["interaction"]["correction_count"] == 0
    assert metrics["interaction"]["clarification_count"] == 0
    assert metrics["interaction"]["retry_count"] == 0


def test_analyze_metrics_detects_correction_via_machine_cues(tmp_path, monkeypatch):
    parsed = tmp_path / "thread-conv-correction-cue" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-correction-cue",
        [
            {"message_id": "m1", "role": "user", "text": "Explain sorting algorithms."},
            {"message_id": "m2", "role": "assistant", "text": "Here is a short overview."},
            {
                "message_id": "m3",
                "role": "user",
                "text": "No, that's not what I meant. Compare merge sort and quicksort instead.",
            },
        ],
    )

    _token_stats, metrics = _build_metrics_fixture(parsed, monkeypatch)

    assert metrics["interaction"]["revision_count"] == 1
    assert metrics["interaction"]["revision_rate"] == 0.5
    assert metrics["interaction"]["correction_count"] == 1
    assert metrics["interaction"]["clarification_count"] == 0
    assert metrics["interaction"]["retry_count"] == 0
    assert metrics["interaction"]["correction_rate"] == 0.5


def test_analyze_metrics_detects_clarification_via_machine_cues(tmp_path, monkeypatch):
    parsed = tmp_path / "thread-conv-clarification-cue" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-clarification-cue",
        [
            {"message_id": "m1", "role": "user", "text": "Explain recursion."},
            {"message_id": "m2", "role": "assistant", "text": "Here is the overview."},
            {
                "message_id": "m3",
                "role": "user",
                "text": "In other words, explain recursion using a simple factorial example.",
            },
        ],
    )

    _token_stats, metrics = _build_metrics_fixture(parsed, monkeypatch)

    assert metrics["interaction"]["revision_count"] == 1
    assert metrics["interaction"]["correction_count"] == 0
    assert metrics["interaction"]["clarification_count"] == 1
    assert metrics["interaction"]["retry_count"] == 0
    assert metrics["interaction"]["clarification_rate"] == 0.5


def test_analyze_metrics_does_not_count_revision_cue_in_assistant_text(
    tmp_path, monkeypatch
):
    parsed = tmp_path / "thread-conv-revision-assistant-cue" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-revision-assistant-cue",
        [
            {"message_id": "m1", "role": "user", "text": "Explain recursion."},
            {
                "message_id": "m2",
                "role": "assistant",
                "text": "In other words, recursion is a function calling itself.",
            },
        ],
    )

    _token_stats, metrics = _build_metrics_fixture(parsed, monkeypatch)

    assert metrics["interaction"]["revision_count"] == 0
    assert metrics["interaction"]["revision_rate"] == 0.0
    assert metrics["interaction"]["correction_count"] == 0
    assert metrics["interaction"]["clarification_count"] == 0
    assert metrics["interaction"]["retry_count"] == 0


def test_analyze_metrics_revision_rate_is_zero_with_zero_or_one_user_message(
    tmp_path, monkeypatch
):
    parsed = tmp_path / "thread-conv-revision-minimal" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-revision-minimal",
        [{"message_id": "m1", "role": "assistant", "text": "hello"}],
    )

    _token_stats, metrics = _build_metrics_fixture(parsed, monkeypatch)

    assert metrics["interaction"]["revision_count"] == 0
    assert metrics["interaction"]["revision_rate"] == 0.0
    assert metrics["interaction"]["correction_count"] == 0
    assert metrics["interaction"]["clarification_count"] == 0
    assert metrics["interaction"]["retry_count"] == 0


def test_analyze_metrics_detects_retry_when_revision_has_no_subtype_cues(
    tmp_path, monkeypatch
):
    parsed = tmp_path / "thread-conv-retry" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-retry",
        [
            {
                "message_id": "m1",
                "role": "user",
                "text": "Please summarize this article about astronomy in five bullet points.",
            },
            {"message_id": "m2", "role": "assistant", "text": "Here is a summary."},
            {
                "message_id": "m3",
                "role": "user",
                "text": "Please summarize this astronomy article in five concise bullet points.",
            },
        ],
    )

    _token_stats, metrics = _build_metrics_fixture(parsed, monkeypatch)

    assert metrics["interaction"]["revision_count"] == 1
    assert metrics["interaction"]["correction_count"] == 0
    assert metrics["interaction"]["clarification_count"] == 0
    assert metrics["interaction"]["retry_count"] == 1
    assert metrics["interaction"]["retry_rate"] == 0.5


def test_analyze_metrics_prioritizes_correction_when_similarity_and_cue_both_match(
    tmp_path, monkeypatch
):
    parsed = tmp_path / "thread-conv-correction-priority" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-correction-priority",
        [
            {
                "message_id": "m1",
                "role": "user",
                "text": "Please explain OAuth scopes for a small internal dashboard.",
            },
            {"message_id": "m2", "role": "assistant", "text": "Here is a brief answer."},
            {
                "message_id": "m3",
                "role": "user",
                "text": "No, that's not what I meant. Please explain OAuth scopes for a small internal dashboard app.",
            },
        ],
    )

    _token_stats, metrics = _build_metrics_fixture(parsed, monkeypatch)

    assert metrics["interaction"]["revision_count"] == 1
    assert metrics["interaction"]["correction_count"] == 1
    assert metrics["interaction"]["clarification_count"] == 0
    assert metrics["interaction"]["retry_count"] == 0


def test_analyze_metrics_interaction_subtype_counts_sum_to_revision_count(
    tmp_path, monkeypatch
):
    parsed = tmp_path / "thread-conv-subtype-sum" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-subtype-sum",
        [
            {"message_id": "m1", "role": "user", "text": "Explain TLS handshakes."},
            {"message_id": "m2", "role": "assistant", "text": "Here is the first pass."},
            {
                "message_id": "m3",
                "role": "user",
                "text": "No, that's not what I meant. Focus on the browser side only.",
            },
            {"message_id": "m4", "role": "assistant", "text": "Understood."},
            {
                "message_id": "m5",
                "role": "user",
                "text": "In other words, explain only the certificate validation part in a short list.",
            },
            {"message_id": "m6", "role": "assistant", "text": "Sure."},
            {
                "message_id": "m7",
                "role": "user",
                "text": "Explain only the certificate validation part in a short list.",
            },
        ],
    )

    _token_stats, metrics = _build_metrics_fixture(parsed, monkeypatch)
    interaction = metrics["interaction"]

    assert interaction["revision_count"] == 3
    assert interaction["correction_count"] == 1
    assert interaction["clarification_count"] == 1
    assert interaction["retry_count"] == 1
    assert (
        interaction["correction_count"]
        + interaction["clarification_count"]
        + interaction["retry_count"]
        == interaction["revision_count"]
    )


def test_analyze_metrics_fails_with_actionable_message_when_token_stats_is_missing(
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
    assert "LP7100" in caplog.text
    assert "token_stats.json not found" in caplog.text
    assert "analyze tokens" in caplog.text


def test_analyze_metrics_skip_existing_keeps_single_artifact_unchanged(
    tmp_path, monkeypatch
):
    parsed = tmp_path / "thread-conv-metrics-skip" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-metrics-skip",
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
            "--encoding",
            "o200k_base",
        ],
    )
    metrics_path = parsed.with_name("metrics.json")
    original = '{"sentinel": "keep-metrics"}\n'
    _write_text(metrics_path, original)

    _run_cli(
        monkeypatch,
        [
            "llm-logparser",
            "analyze",
            "metrics",
            "--input",
            str(parsed),
            "--skip-existing",
        ],
    )

    assert metrics_path.read_text(encoding="utf-8") == original


def test_analyze_metrics_without_skip_existing_overwrites_existing_artifact(
    tmp_path, monkeypatch
):
    parsed = tmp_path / "thread-conv-metrics-overwrite" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-metrics-overwrite",
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
            "--encoding",
            "o200k_base",
        ],
    )
    metrics_path = parsed.with_name("metrics.json")
    _write_text(metrics_path, '{"sentinel": "replace-metrics"}\n')

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

    payload = _load_json(metrics_path)
    assert payload["artifact_type"] == "metrics"
    assert payload["conversation_id"] == "conv-metrics-overwrite"
    assert payload["schema_version"] == "1.0"


def test_analyze_metrics_skip_existing_supports_directory_input(tmp_path, monkeypatch):
    root = tmp_path / "parsed"
    parsed_a = root / "a" / "thread-a" / "parsed.jsonl"
    parsed_b = root / "b" / "thread-b" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed_a,
        "conv-a",
        [
            {"message_id": "m1", "role": "user", "text": "one"},
            {"message_id": "m2", "role": "assistant", "text": "two"},
        ],
    )
    _write_parsed_jsonl(
        parsed_b,
        "conv-b",
        [
            {"message_id": "m1", "role": "user", "text": "three"},
            {"message_id": "m2", "role": "assistant", "text": "four"},
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
    original = '{"sentinel": "preserve-metrics-a"}\n'
    _write_text(parsed_a.with_name("metrics.json"), original)

    _run_cli(
        monkeypatch,
        [
            "llm-logparser",
            "--locale",
            "en-US",
            "analyze",
            "metrics",
            "--input",
            str(root),
            "--skip-existing",
        ],
    )

    assert parsed_a.with_name("metrics.json").read_text(encoding="utf-8") == original
    assert _load_json(parsed_b.with_name("metrics.json"))["conversation_id"] == "conv-b"


def test_analyze_metrics_dry_run_does_not_write_and_reports_counts(
    tmp_path, monkeypatch, caplog
):
    root = tmp_path / "parsed"
    parsed_a = root / "a" / "thread-a" / "parsed.jsonl"
    parsed_b = root / "b" / "thread-b" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed_a,
        "conv-a",
        [
            {"message_id": "m1", "role": "user", "text": "one"},
            {"message_id": "m2", "role": "assistant", "text": "two"},
        ],
    )
    _write_parsed_jsonl(
        parsed_b,
        "conv-b",
        [
            {"message_id": "m1", "role": "user", "text": "three"},
            {"message_id": "m2", "role": "assistant", "text": "four"},
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
    _write_text(parsed_a.with_name("metrics.json"), '{"sentinel": "preserve-metrics-a"}\n')

    _run_cli(
        monkeypatch,
        [
            "llm-logparser",
            "analyze",
            "metrics",
            "--input",
            str(root),
            "--skip-existing",
            "--dry-run",
        ],
    )

    assert parsed_a.with_name("metrics.json").read_text(encoding="utf-8") == '{"sentinel": "preserve-metrics-a"}\n'
    assert not parsed_b.with_name("metrics.json").exists()
    assert "Previewing metrics.json generation" in caplog.text
    assert "Detected threads: 2" in caplog.text
    assert "Existing sidecars: 1" in caplog.text
    assert "New sidecars to create: 1" in caplog.text
    assert "Skipped existing sidecars: 1" in caplog.text
    assert "No files written." in caplog.text
