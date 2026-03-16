import json
from pathlib import Path

from llm_logparser.core.parser import parse_to_jsonl
from llm_logparser.core.providers.xai.adapter import adapter as xai_adapter
from llm_logparser.core.utils import shorten_id


def _load_export() -> dict:
    fixture = Path("artifacts/prod-grok-backend.json")
    return json.loads(fixture.read_text(encoding="utf-8"))


def test_xai_adapter_maps_grok_conversation_bundle():
    export = _load_export()
    raw = export["conversations"][0]

    messages = list(xai_adapter(raw))

    assert len(messages) == len(raw["responses"])

    first = messages[0]
    first_raw = raw["responses"][0]["response"]
    second = messages[1]
    second_raw = raw["responses"][1]["response"]

    assert first["conversation_id"] == shorten_id(raw["conversation"]["id"])
    assert first["message_id"] == shorten_id(first_raw["_id"])
    assert first["parent_id"] is None
    assert first["role"] == "user"
    assert first["created_at"].endswith("Z")
    assert first["text"] == first_raw["message"]
    assert first["meta"]["model"] == first_raw["model"]

    assert second["message_id"] == shorten_id(second_raw["_id"])
    assert second["parent_id"] == shorten_id(second_raw["parent_response_id"])
    assert second["role"] == "assistant"
    assert second["text"] == second_raw["message"]
    assert second["meta"]["model"] == second_raw["model"]


def test_xai_adapter_accepts_single_conversation_wrapper():
    export = _load_export()
    wrapper = {"conversations": [export["conversations"][0]]}

    messages = list(xai_adapter(wrapper))

    assert messages
    assert messages[0]["conversation_id"] == shorten_id(export["conversations"][0]["conversation"]["id"])


def test_parse_to_jsonl_supports_xai_provider_with_conversation_array(tmp_path):
    export = _load_export()
    fixture = tmp_path / "grok-conversations.json"
    fixture.write_text(
        json.dumps(export["conversations"][:2], ensure_ascii=False),
        encoding="utf-8",
    )

    stats = parse_to_jsonl("xai", fixture, tmp_path, dry_run=False, fail_fast=True)

    assert stats["threads"] == 2
    assert stats["messages"] > 0

    conv_id = shorten_id(export["conversations"][0]["conversation"]["id"])
    parsed_path = tmp_path / "xai" / f"thread-{conv_id}" / "parsed.jsonl"
    assert parsed_path.exists()
