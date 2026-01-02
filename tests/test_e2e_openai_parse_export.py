# tests/test_e2e_openai_parse_export.py

from pathlib import Path

from llm_logparser.core.exporter import export_thread_md
from llm_logparser.core.parser import parse_to_jsonl


def test_e2e_openai_to_md(tmp_path):
    fixture = Path("tests/fixtures/openai_sample.json")
    stats = parse_to_jsonl(
        "openai",
        fixture,
        tmp_path,
        dry_run=False,
        fail_fast=True,
    )
    assert stats["threads"] == 1

    conv_id = "68b3eea1-1fc4-832c-878a-23896288675a"
    parsed_path = tmp_path / "openai" / f"thread-{conv_id}" / "parsed.jsonl"
    md_path = tmp_path / "conv-1.md"
    export_thread_md(parsed_path, md_path)

    md = md_path.read_text(encoding="utf-8")
    # 期待する文字列の一部をチェック
    assert "user" in md
    assert "おはよう" in md
    assert any("2025/08/31" in md_line for md_line in md.splitlines())
