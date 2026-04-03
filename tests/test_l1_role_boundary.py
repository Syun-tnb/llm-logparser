import json
from pathlib import Path

from llm_logparser.core.analyzer_stats import analyze_stats
from llm_logparser.core.analyzer_timeline import analyze_timeline
from llm_logparser.core.i18n import set_locale
from llm_logparser.core.l1_derivation import (
    NORMALIZED_ROLE_SET,
    assert_normalized_role,
    build_thread_stats_artifact,
    derive_thread_metrics_from_rows,
)
from llm_logparser.core.message_windows import build_message_window_artifact


def _write_parsed_jsonl(path: Path, conversation_id: str, messages: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "record_type": "thread",
                    "provider_id": "openai",
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
                "provider_id": "openai",
                "conversation_id": conversation_id,
                "message_id": message.get("message_id", f"m{idx}"),
                "content": {"content_type": "text", "parts": [message["text"]]},
                "text": message["text"],
                "ts": message["ts"],
            }
            if "role" in message:
                row["role"] = message["role"]
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def _messy_role_messages() -> list[dict]:
    return [
        {"message_id": "m1", "role": " USER ", "ts": 1, "text": "hello"},
        {
            "message_id": "m2",
            "role": "Assistant",
            "ts": 2,
            "text": "I can't help with that request.",
        },
        {"message_id": "m3", "role": "assistant", "ts": 3, "text": "plain assistant"},
        {"message_id": "m4", "role": "model", "ts": 4, "text": "internal"},
        {"message_id": "m5", "role": "moderator", "ts": 5, "text": "moderated"},
        {"message_id": "m6", "role": None, "ts": 6, "text": "missing"},
        {"message_id": "m7", "role": "system", "ts": 7, "text": "sys"},
        {"message_id": "m8", "role": "tool", "ts": 8, "text": "tool"},
    ]


def _canonical_rows(conversation_id: str = "conv-role-boundary") -> list[dict]:
    return [
        {
            "record_type": "message",
            "provider_id": "openai",
            "conversation_id": conversation_id,
            "message_id": message["message_id"],
            "role": message.get("role"),
            "ts": message["ts"],
            "text": message["text"],
            "content": {"content_type": "text", "parts": [message["text"]]},
        }
        for message in _messy_role_messages()
    ]


def test_assert_normalized_role_accepts_only_canonical_role_set():
    for role in NORMALIZED_ROLE_SET:
        assert_normalized_role(role)


def test_no_provider_role_leakage_in_l1_artifacts(tmp_path):
    set_locale("en-US")
    rows = _canonical_rows()
    metrics = derive_thread_metrics_from_rows(rows)
    thread_stats = build_thread_stats_artifact(metrics, provider_id="openai")
    window = build_message_window_artifact(
        rows,
        window_index=1,
        window_size=len(rows),
        window_stride=len(rows),
    )

    parsed = tmp_path / "thread-conv-role-boundary" / "parsed.jsonl"
    _write_parsed_jsonl(parsed, "conv-role-boundary", _messy_role_messages())
    stats = analyze_stats(parsed)

    assert thread_stats["user_messages"] == 1
    assert thread_stats["assistant_messages"] == 2
    assert thread_stats["other_roles"] == 5
    assert thread_stats["other_role_breakdown"] == {"system": 1, "tool": 1, "unknown": 3}
    assert set(thread_stats["other_role_breakdown"]) <= NORMALIZED_ROLE_SET

    assert window["roles"] == [
        "user",
        "assistant",
        "assistant",
        "unknown",
        "unknown",
        "unknown",
        "system",
        "tool",
    ]
    assert set(window["roles"]) <= NORMALIZED_ROLE_SET
    assert " USER " not in window["text"]
    assert "Assistant:" not in window["text"]
    assert "moderator:" not in window["text"]
    assert "model:" not in window["text"]

    assert stats["user_messages"] == 1
    assert stats["assistant_messages"] == 2
    assert stats["other_roles"] == 5
    assert stats["other_role_breakdown"] == {"system": 1, "tool": 1, "unknown": 3}
    assert set(stats["other_role_breakdown"]) <= NORMALIZED_ROLE_SET
    assert stats["research_summary"]["safety"]["threads_with_refusal"] == 1
    assert stats["research_summary"]["safety"]["threads_with_intervention"] == 1


def test_analyzer_timeline_normalizes_role_variants(tmp_path):
    parsed = tmp_path / "thread-conv-role-boundary" / "parsed.jsonl"
    messages = _messy_role_messages()
    _write_parsed_jsonl(parsed, "conv-role-boundary", messages)

    payload = analyze_timeline(parsed, bucket="day")

    assert payload["timeline"] == [
        {
            "bucket_start": "1970-01-01T00:00:00Z",
            "message_count": 8,
            "user_messages": 1,
            "assistant_messages": 2,
            "other_roles": 5,
            "characters_total": sum(len(message["text"]) for message in messages),
        }
    ]
