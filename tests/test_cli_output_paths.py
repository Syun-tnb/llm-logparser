import json
import logging
from pathlib import Path

from llm_logparser.cli.cli import main


def _write_parsed_jsonl(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                '{"record_type":"thread","provider_id":"openai","conversation_id":"conv-1","message_count":1}',
                '{"record_type":"message","provider_id":"openai","conversation_id":"conv-1","message_id":"m1","parent_id":null,"role":"assistant","ts":1730000001000,"content":{"content_type":"text","parts":["Hi"]},"text":"Hi"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_openai_export(path: Path) -> None:
    payload = [
        {
            "id": "conv-1",
            "title": "Thread One",
            "mapping": {
                "root": {"id": "root", "message": None, "parent": None, "children": ["m1"]},
                "m1": {
                    "id": "m1",
                    "parent": "root",
                    "children": [],
                    "message": {
                        "id": "m1",
                        "author": {"role": "user"},
                        "content": {"content_type": "text", "parts": ["hello"]},
                        "create_time": 1730000001.0,
                    },
                },
            },
        }
    ]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_parse_logs_absolute_output_directory(tmp_path, monkeypatch, caplog):
    fixture = Path(__file__).parent / "fixtures" / "openai_sample.json"
    monkeypatch.chdir(tmp_path)
    caplog.set_level(logging.INFO)

    main(["parse", "--provider", "openai", "--input", str(fixture), "--outdir", "artifacts/output"])

    expected = (tmp_path / "artifacts" / "output" / "openai").resolve()
    assert f"Output directory: {expected}" in caplog.text


def test_export_logs_absolute_output_path(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    parsed = tmp_path / "parsed.jsonl"
    _write_parsed_jsonl(parsed)

    caplog.set_level(logging.INFO)
    main(["export", "--input", "parsed.jsonl", "--out", "nested/thread.md"])

    expected = (tmp_path / "nested" / "thread.md").resolve()
    assert f"Output MD  : {expected}" in caplog.text


def test_extract_logs_absolute_output_paths(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    input_path = tmp_path / "openai_export.json"
    _write_openai_export(input_path)

    caplog.set_level(logging.INFO)
    main(
        [
            "extract",
            "--provider",
            "openai",
            "--input",
            "openai_export.json",
            "--conversation-id",
            "conv-1",
            "--outdir",
            "artifacts",
            "--dry-run",
        ]
    )

    root = (tmp_path / "artifacts").resolve()
    extract_path = (tmp_path / "artifacts" / "openai" / "thread-conv-1" / "extract.json").resolve()
    assert f"Output root: {root}" in caplog.text
    assert str(extract_path) in caplog.text


def test_chain_logs_absolute_output_paths(tmp_path, monkeypatch, caplog):
    fixture = Path(__file__).parent / "fixtures" / "openai_sample.json"
    monkeypatch.chdir(tmp_path)

    caplog.set_level(logging.INFO)
    main(
        [
            "chain",
            "--provider",
            "openai",
            "--input",
            str(fixture),
            "--outdir",
            "artifacts",
            "--export-outdir",
            "markdown",
        ]
    )

    root = (tmp_path / "artifacts").resolve()
    parse_root = (tmp_path / "artifacts").resolve()
    export_root = (tmp_path / "markdown").resolve()
    exported = next((tmp_path / "markdown").glob("*.md")).resolve()

    assert f"[chain] Root     : {root}" in caplog.text
    assert f"[chain] Parsing into: {parse_root}" in caplog.text
    assert f"[chain] Export outdir: {export_root}" in caplog.text
    assert str(exported) in caplog.text
