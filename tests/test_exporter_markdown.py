import json

from llm_logparser.core.exporter import export_thread_md


def test_export_thread_md(tmp_path):
    parsed = tmp_path / "parsed.jsonl"
    with parsed.open("w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "record_type": "thread",
                    "provider_id": "openai",
                    "conversation_id": "conv-1",
                    "message_count": 1,
                },
                ensure_ascii=True,
            )
            + "\n"
        )
        f.write(
            json.dumps(
                {
                    "record_type": "message",
                    "provider_id": "openai",
                    "conversation_id": "conv-1",
                    "message_id": "m1",
                    "parent_id": None,
                    "role": "assistant",
                    "ts": 1730000001000,
                    "content": {"content_type": "text", "parts": ["Hi"]},
                    "text": "Hi",
                },
                ensure_ascii=True,
            )
            + "\n"
        )

    out = tmp_path / "thread.md"
    export_thread_md(parsed, out)

    md = out.read_text(encoding="utf-8")
    assert "assistant" in md
    assert "Hi" in md
