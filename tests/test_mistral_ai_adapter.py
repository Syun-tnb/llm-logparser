import json
from pathlib import Path

from llm_logparser.core.exporter import export_thread_md
from llm_logparser.core.parser import parse_to_jsonl
from llm_logparser.core.providers.mistral_ai.adapter import adapter as mistral_adapter
from llm_logparser.core.providers.mistral_ai.le_chat.adapter import is_le_chat_export
from llm_logparser.core.utils import shorten_id


def _load_fixture() -> list[dict]:
    fixture = Path("tests/fixtures/mistral_le_chat_thread.json")
    return json.loads(fixture.read_text(encoding="utf-8"))


def _write_thread(path: Path, *, chat_id: str, message_suffix: str) -> list[dict]:
    payload = _load_fixture()
    for index, message in enumerate(payload, start=1):
        message["chatId"] = chat_id
        message["id"] = f"{message['id']}-{message_suffix}-{index}"
        if index == 1:
            message["content"] = f"Prompt from {message_suffix}"
        elif index == 2:
            message["content"] = f"Answer {message_suffix}"
            message["contentChunks"] = [{"text": f"Answer {message_suffix}", "type": "text"}]
        else:
            message["context"] = {"note": f"empty-{message_suffix}"}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def test_mistral_le_chat_detection_accepts_valid_export_shape():
    payload = _load_fixture()

    assert is_le_chat_export(payload) is True


def test_mistral_le_chat_detection_rejects_unrelated_json_shape():
    payload = [{"id": "m1", "role": "user", "createdAt": "2026-03-17T10:53:35.234Z"}]

    assert is_le_chat_export(payload) is False


def test_mistral_le_chat_adapter_preserves_provider_native_content():
    raw = _load_fixture()

    messages = list(mistral_adapter(raw))

    assert len(messages) == 3

    user_msg, assistant_msg, empty_msg = messages
    raw_conv_id = raw[0]["chatId"]

    assert user_msg["conversation_id"] == shorten_id(raw_conv_id)
    assert user_msg["conv_id"] == shorten_id(raw_conv_id)
    assert user_msg["message_id"] == shorten_id(raw[0]["id"])
    assert user_msg.get("parent_id") is None
    assert user_msg["role"] == "user"
    assert user_msg["ts"] == 1773744815234
    assert user_msg["created_at"] == raw[0]["createdAt"]
    assert type(user_msg) is dict
    assert user_msg["text"] == raw[0]["content"]
    assert user_msg["content"] == raw[0]["content"]

    assert assistant_msg["message_id"] == shorten_id(raw[1]["id"])
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["text"] == raw[1]["content"]
    assert assistant_msg["content"] == raw[1]["contentChunks"]
    assert assistant_msg["meta"]["service"] == "le_chat"
    assert assistant_msg["meta"]["reactionDetail"] == "helpful"
    assert assistant_msg["meta"]["context"] == {"source": "memory"}

    assert empty_msg["text"] == ""
    assert empty_msg["content"] == raw[2]["content"]
    assert empty_msg.get("parent_id") is None


def test_parse_to_jsonl_supports_mistral_le_chat_single_file(tmp_path):
    fixture = Path("tests/fixtures/mistral_le_chat_thread.json")
    raw = _load_fixture()

    stats = parse_to_jsonl("mistral_ai", fixture, tmp_path, dry_run=False, fail_fast=True, validate_schema=True)

    assert stats["threads"] == 1
    assert stats["messages"] == 3

    conv_id = shorten_id(raw[0]["chatId"])
    parsed_path = tmp_path / "mistral_ai" / f"thread-{conv_id}" / "parsed.jsonl"
    assert parsed_path.exists()

    rows = [
        json.loads(line)
        for line in parsed_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows[0] == {
        "record_type": "thread",
        "provider_id": "mistral_ai",
        "conversation_id": conv_id,
        "message_count": 3,
    }

    messages = [row for row in rows if row.get("record_type") == "message"]
    assert [row["message_id"] for row in messages] == [shorten_id(item["id"]) for item in raw]
    assert messages[1]["meta"]["service"] == "le_chat"
    assert messages[1]["content"] == raw[1]["contentChunks"]
    assert messages[1]["text"] == raw[1]["content"]
    assert messages[2]["content"] == raw[2]["content"]


def test_mistral_text_prefers_non_empty_content_over_chunks():
    raw = _load_fixture()
    raw[1]["content"] = "Canonical text"
    raw[1]["contentChunks"] = [{"text": "Chunk text", "type": "text"}]

    assert mistral_adapter(raw)[1]["text"] == "Canonical text"


def test_mistral_text_falls_back_to_chunks_when_content_is_empty():
    raw = _load_fixture()
    raw[1]["content"] = ""
    raw[1]["contentChunks"] = [
        {"text": "Day 1", "type": "text"},
        {"text": "Day 2", "type": "text"},
        {"type": "image", "url": "https://example.test/img.png"},
    ]

    assert mistral_adapter(raw)[1]["text"] == "Day 1\nDay 2"


def test_parse_to_jsonl_supports_mistral_le_chat_directory_input_and_ignores_non_matching_json(
    tmp_path,
):
    input_dir = tmp_path / "exports"
    input_dir.mkdir()

    first = _write_thread(
        input_dir / "thread-a.json",
        chat_id="thread-a-1111-2222-3333-444444444444",
        message_suffix="a",
    )
    second = _write_thread(
        input_dir / "thread-b.json",
        chat_id="thread-b-1111-2222-3333-555555555555",
        message_suffix="b",
    )
    (input_dir / "ignore.json").write_text(
        json.dumps([{"id": "m1", "role": "user"}], ensure_ascii=True),
        encoding="utf-8",
    )

    stats = parse_to_jsonl("mistral_ai", input_dir, tmp_path / "artifacts", dry_run=False, fail_fast=True)

    assert stats["threads"] == 2
    assert stats["messages"] == 6

    first_conv_id = shorten_id(first[0]["chatId"])
    second_conv_id = shorten_id(second[0]["chatId"])
    first_parsed = tmp_path / "artifacts" / "mistral_ai" / f"thread-{first_conv_id}" / "parsed.jsonl"
    second_parsed = tmp_path / "artifacts" / "mistral_ai" / f"thread-{second_conv_id}" / "parsed.jsonl"

    assert first_parsed.exists()
    assert second_parsed.exists()

    manifest_path = tmp_path / "artifacts" / "mistral_ai" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert [thread["conversation_id"] for thread in manifest["index"]["threads"]] == [
        first_conv_id,
        second_conv_id,
    ]


def test_e2e_mistral_le_chat_parse_export_smoke(tmp_path):
    fixture = Path("tests/fixtures/mistral_le_chat_thread.json")
    raw = _load_fixture()

    stats = parse_to_jsonl("mistral_ai", fixture, tmp_path, dry_run=False, fail_fast=True)

    assert stats["threads"] == 1

    conv_id = shorten_id(raw[0]["chatId"])
    parsed_path = tmp_path / "mistral_ai" / f"thread-{conv_id}" / "parsed.jsonl"
    md_path = tmp_path / "mistral-thread.md"
    export_thread_md(parsed_path, md_path)

    md = md_path.read_text(encoding="utf-8")
    assert "provider: mistral_ai" in md
    assert "Plan a 3 day Kyoto itinerary." in md
    assert "Day 1" in md
