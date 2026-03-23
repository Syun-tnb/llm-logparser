import json
from pathlib import Path

from llm_logparser.core.exporter import export_thread_md
from llm_logparser.core.parser import parse_to_jsonl
from llm_logparser.core.providers.google.gemini_activity.adapter import (
    adapter as google_adapter,
    expand_input_records,
    extract_assistant_text,
    extract_user_text,
    html_to_text,
    is_gemini_activity_export,
    synthetic_conversation_id,
    synthetic_message_id,
)


def _record(
    *,
    title: str,
    time: str,
    safe_html_item=None,
    header: str = "Gemini アプリ",
    products=None,
    activity_controls=None,
) -> dict:
    return {
        "header": header,
        "title": title,
        "time": time,
        "products": ["Gemini アプリ"] if products is None else products,
        "activityControls": (
            ["Gemini アプリ アクティビティ"]
            if activity_controls is None
            else activity_controls
        ),
        **({"safeHtmlItem": safe_html_item} if safe_html_item is not None else {}),
    }


def test_google_gemini_activity_detection_accepts_valid_export_shape():
    payload = json.loads(
        Path("tests/fixtures/google_gemini_activity.json").read_text(encoding="utf-8")
    )

    assert is_gemini_activity_export(payload) is True


def test_google_gemini_activity_detection_rejects_unrelated_json():
    payload = [
        {
            "header": "Google Maps",
            "title": "Visited place",
            "time": "2026-03-23T07:18:42.981Z",
            "products": ["Maps"],
            "safeHtmlItem": [{"html": "<p>not gemini</p>"}],
        }
    ]

    assert is_gemini_activity_export(payload) is False


def test_google_gemini_user_text_extraction_from_title():
    assert extract_user_text("送信したメッセージ: 小春？チャンク（Chunk）って何？") == (
        "小春？チャンク（Chunk）って何？"
    )


def test_google_gemini_assistant_text_extraction_from_safe_html_items():
    safe_html_item = [
        {"html": "<p>最初の段落です。</p>"},
        {"html": "<p>次の段落です。</p>"},
    ]

    assert extract_assistant_text(safe_html_item) == "最初の段落です。\n\n次の段落です。"


def test_google_gemini_html_to_text_preserves_readable_boundaries():
    html = "<p>Alpha</p><p>Beta</p><ul><li>One</li><li>Two</li></ul>"

    text = html_to_text(html)

    assert "Alpha\n\nBeta" in text
    assert "- One" in text
    assert "- Two" in text


def test_google_gemini_synthetic_ids_are_deterministic():
    record = _record(
        title="送信したメッセージ: テスト",
        time="2026-03-23T07:18:42.981Z",
        safe_html_item=[{"html": "<p>応答</p>"}],
    )
    record["__llp_source_index"] = 0

    assert synthetic_conversation_id(record) == synthetic_conversation_id(dict(record))
    assert synthetic_message_id(record, "user") == synthetic_message_id(dict(record), "user")
    assert synthetic_message_id(record, "user") != synthetic_message_id(record, "assistant")


def test_google_gemini_adapter_handles_user_only_record():
    record = _record(
        title="送信したメッセージ: 明日の予定を整理して",
        time="2026-03-23T07:20:00.000Z",
    )

    messages = google_adapter(record)

    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["parent_id"] is None
    assert messages[0]["text"] == "明日の予定を整理して"


def test_google_gemini_adapter_handles_assistant_only_record():
    record = _record(
        title="送信したメッセージ: ",
        time="2026-03-23T07:21:00.000Z",
        safe_html_item=[{"html": "<p>こちらが要約です。</p>"}],
    )

    messages = google_adapter(record)

    assert len(messages) == 1
    assert messages[0]["role"] == "assistant"
    assert messages[0]["parent_id"] is None
    assert messages[0]["text"] == "こちらが要約です。"


def test_google_gemini_adapter_builds_mini_thread_from_single_event():
    record = _record(
        title="送信したメッセージ: 旅行プランを考えて",
        time="2026-03-23T07:18:42.981Z",
        safe_html_item=[{"html": "<p>1日目は散策です。</p>"}],
    )

    messages = google_adapter(record)

    assert len(messages) == 2
    user_message, assistant_message = messages
    assert user_message["conversation_id"] == assistant_message["conversation_id"]
    assert user_message["parent_id"] is None
    assert assistant_message["parent_id"] == user_message["message_id"]
    assert user_message["meta"]["service"] == "gemini_activity"
    assert assistant_message["meta"]["raw_title"] == record["title"]


def test_google_gemini_record_expander_sorts_events_by_timestamp_then_synthetic_id():
    later = _record(
        title="送信したメッセージ: later",
        time="2026-03-23T07:19:00.000Z",
        safe_html_item=[{"html": "<p>later response</p>"}],
    )
    same_time_b = _record(
        title="送信したメッセージ: b",
        time="2026-03-23T07:18:42.981Z",
        safe_html_item=[{"html": "<p>b response</p>"}],
    )
    same_time_a = _record(
        title="送信したメッセージ: a",
        time="2026-03-23T07:18:42.981Z",
        safe_html_item=[{"html": "<p>a response</p>"}],
    )

    expanded = expand_input_records([later, same_time_b, same_time_a])

    ordered_ids = [synthetic_conversation_id(record) for record in expanded]
    assert ordered_ids == sorted(ordered_ids[:2]) + [ordered_ids[2]]


def test_parse_to_jsonl_supports_google_gemini_activity_as_event_scoped_threads(tmp_path):
    fixture = Path("tests/fixtures/google_gemini_activity.json")
    raw = json.loads(fixture.read_text(encoding="utf-8"))

    stats = parse_to_jsonl("google", fixture, tmp_path, dry_run=False, fail_fast=True)

    assert stats["threads"] == 3
    assert stats["messages"] == 4

    first_record = dict(raw[0])
    first_record["__llp_source_index"] = 0
    conv_id = synthetic_conversation_id(first_record)
    parsed_path = tmp_path / "google" / f"thread-{conv_id}" / "parsed.jsonl"
    assert parsed_path.exists()

    rows = [
        json.loads(line)
        for line in parsed_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    messages = [row for row in rows if row.get("record_type") == "message"]
    assert [row["role"] for row in messages] == ["user", "assistant"]
    assert messages[0]["text"] == "小春？チャンク（Chunk）って何？"
    assert "Chunk はモデルが処理する単位です。" in messages[1]["text"]


def test_parse_to_jsonl_supports_google_gemini_activity_directory_input(tmp_path):
    input_dir = tmp_path / "takeout"
    input_dir.mkdir()
    fixture = Path("tests/fixtures/google_gemini_activity.json").read_text(encoding="utf-8")
    (input_dir / "gemini.json").write_text(fixture, encoding="utf-8")
    (input_dir / "ignore.json").write_text(
        json.dumps([{"title": "other", "time": "2026-03-23T00:00:00.000Z", "products": ["Maps"]}]),
        encoding="utf-8",
    )

    stats = parse_to_jsonl("google", input_dir, tmp_path / "artifacts", dry_run=False, fail_fast=True)

    assert stats["threads"] == 3
    assert stats["messages"] == 4


def test_e2e_google_gemini_parse_export_smoke(tmp_path):
    fixture = Path("tests/fixtures/google_gemini_activity.json")
    raw = json.loads(fixture.read_text(encoding="utf-8"))

    stats = parse_to_jsonl("google", fixture, tmp_path, dry_run=False, fail_fast=True)

    assert stats["threads"] == 3

    first_record = dict(raw[0])
    first_record["__llp_source_index"] = 0
    conv_id = synthetic_conversation_id(first_record)
    parsed_path = tmp_path / "google" / f"thread-{conv_id}" / "parsed.jsonl"
    md_path = tmp_path / "gemini-event.md"
    export_thread_md(parsed_path, md_path)

    md = md_path.read_text(encoding="utf-8")
    assert "provider: google" in md
    assert "小春？チャンク（Chunk）って何？" in md
    assert "Chunk はモデルが処理する単位です。" in md
