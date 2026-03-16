import json
from pathlib import Path

from llm_logparser.core.parser import parse_to_jsonl
from llm_logparser.core.providers.xai.adapter import (
    adapter as xai_adapter,
    get_record_expander,
)
from llm_logparser.core.utils import shorten_id


def _load_fixture() -> dict:
    fixture = Path("tests/fixtures/grok_wrapper_sample.json")
    return json.loads(fixture.read_text(encoding="utf-8"))


def test_xai_record_expander_unwraps_top_level_conversations_wrapper():
    payload = _load_fixture()

    bundles = list(get_record_expander()(payload))

    assert len(bundles) == 2
    assert bundles[0]["conversation"]["id"] == "826b2d30-4a1d-4cad-8267-638de72e975e"
    assert bundles[1]["conversation"]["id"] == "b3e22762-44c3-4fc4-bb8c-b5036bc7dbf5"


def test_xai_adapter_maps_single_grok_conversation_bundle():
    payload = _load_fixture()
    raw = payload["conversations"][0]

    messages = list(xai_adapter(raw))

    assert len(messages) == 2

    user_msg, assistant_msg = messages
    assert user_msg["conversation_id"] == shorten_id(raw["conversation"]["id"])
    assert user_msg["thread_title"] == raw["conversation"]["title"]
    assert user_msg["message_id"] == shorten_id(raw["responses"][0]["response"]["_id"])
    assert user_msg["parent_id"] is None
    assert user_msg["role"] == "user"
    assert user_msg["ts"] == 1771392805185
    assert user_msg["created_at"] == "2026-02-18T05:33:25.185Z"
    assert user_msg["text"] == raw["responses"][0]["response"]["message"]
    assert user_msg["meta"]["model"] == "grok-4-auto"

    assert assistant_msg["message_id"] == shorten_id(raw["responses"][1]["response"]["_id"])
    assert assistant_msg["parent_id"] == shorten_id(raw["responses"][1]["response"]["parent_response_id"])
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["ts"] == 1771392853861
    assert assistant_msg["created_at"] == "2026-02-18T05:34:13.861Z"
    assert assistant_msg["text"] == raw["responses"][1]["response"]["message"]
    assert assistant_msg["meta"]["model"] == "grok-4"


def test_parse_to_jsonl_supports_real_grok_wrapper_shape(tmp_path):
    fixture = Path("tests/fixtures/grok_wrapper_sample.json")

    stats = parse_to_jsonl("xai", fixture, tmp_path, dry_run=False, fail_fast=True)

    assert stats["threads"] == 2
    assert stats["messages"] == 4

    first_conv_id = shorten_id("826b2d30-4a1d-4cad-8267-638de72e975e")
    second_conv_id = shorten_id("b3e22762-44c3-4fc4-bb8c-b5036bc7dbf5")
    first_parsed = tmp_path / "xai" / f"thread-{first_conv_id}" / "parsed.jsonl"
    second_parsed = tmp_path / "xai" / f"thread-{second_conv_id}" / "parsed.jsonl"

    assert first_parsed.exists()
    assert second_parsed.exists()

    first_rows = [
        json.loads(line)
        for line in first_parsed.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    second_rows = [
        json.loads(line)
        for line in second_parsed.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    first_messages = [row for row in first_rows if row.get("record_type") == "message"]
    second_messages = [row for row in second_rows if row.get("record_type") == "message"]

    assert [row["message_id"] for row in first_messages] == [
        shorten_id("b7068bc6-2393-4766-82f2-36bab6bb576d"),
        shorten_id("2130f537-c8bc-432e-8c7b-fda36f49d970"),
    ]
    assert first_messages[1]["parent_id"] == shorten_id("b7068bc6-2393-4766-82f2-36bab6bb576d")
    assert first_messages[1]["text"] == (
        "プロジェクト案への意見です。検索結果を踏まえた上で、"
        "差分プライバシーと検疫ゲートウェイの案は筋が良いと思います。"
    )

    assert [row["message_id"] for row in second_messages] == [
        shorten_id("0f8db0db-cf5a-4793-b8d8-7ff111111111"),
        shorten_id("c1743bb2-f29c-4812-b024-b76f0b6d66e4"),
    ]
    assert [row["role"] for row in second_messages] == ["user", "assistant"]
    assert second_messages[1]["created_at"] == "2026-02-19T07:06:45.000Z"
    assert second_messages[1]["meta"]["attachments"][0]["type"] == "file_attachment"
