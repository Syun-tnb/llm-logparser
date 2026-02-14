import json
import sys
from pathlib import Path

import pytest

from llm_logparser.cli.cli import main


def _write_multi_thread_export(path: Path) -> list[dict]:
    data = [
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
                        "content": {"content_type": "text", "parts": ["hello from thread 1"]},
                        "create_time": 1730000001.0,
                    },
                },
            },
        },
        {
            "conversation_id": "conv-2",
            "title": "Thread Two",
            "api_key": "my-secret-key",
            "token": "top-secret-token",
            "user_password": "pw-123",
            "nested": {
                "authorization_header": "Bearer abc",
                "cookie_value": "abc=1",
                "safe": "ok",
            },
            "mapping": {
                "root": {"id": "root", "message": None, "parent": None, "children": ["m2"]},
                "m2": {
                    "id": "m2",
                    "parent": "root",
                    "children": [],
                    "message": {
                        "id": "m2",
                        "author": {"role": "user"},
                        "content": {
                            "content_type": "text",
                            "parts": [
                                "contact me at test.user@example.com",
                                "phone: +1 (415) 555-2671",
                            ],
                        },
                        "create_time": 1730000002.0,
                    },
                },
            },
        },
    ]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def test_extract_success(tmp_path, monkeypatch):
    input_path = tmp_path / "openai_export.json"
    source_data = _write_multi_thread_export(input_path)
    outdir = tmp_path / "artifacts"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "llm-logparser",
            "extract",
            "--provider",
            "openai",
            "--input",
            str(input_path),
            "--conversation-id",
            "conv-1",
            "--outdir",
            str(outdir),
        ],
    )
    main()

    extract_path = outdir / "openai" / "thread-conv-1" / "extract.json"
    assert extract_path.exists()
    payload = json.loads(extract_path.read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert payload[0]["id"] == "conv-1"
    assert payload[0]["title"] == source_data[0]["title"]


def test_extract_not_found(tmp_path, monkeypatch):
    input_path = tmp_path / "openai_export.json"
    _write_multi_thread_export(input_path)
    outdir = tmp_path / "artifacts"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "llm-logparser",
            "extract",
            "--provider",
            "openai",
            "--input",
            str(input_path),
            "--conversation-id",
            "missing-conv",
            "--outdir",
            str(outdir),
        ],
    )
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code != 0


def test_extract_sanitize(tmp_path, monkeypatch):
    input_path = tmp_path / "openai_export.json"
    _write_multi_thread_export(input_path)
    outdir = tmp_path / "artifacts"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "llm-logparser",
            "extract",
            "--provider",
            "openai",
            "--input",
            str(input_path),
            "--conversation-id",
            "conv-2",
            "--outdir",
            str(outdir),
        ],
    )
    main()

    extract_path = outdir / "openai" / "thread-conv-2" / "extract.json"
    payload = json.loads(extract_path.read_text(encoding="utf-8"))
    item = payload[0]
    assert item["api_key"] == "REDACTED"
    assert item["token"] == "REDACTED"
    assert item["user_password"] == "REDACTED"
    assert item["nested"]["authorization_header"] == "REDACTED"
    assert item["nested"]["cookie_value"] == "REDACTED"
    parts = item["mapping"]["m2"]["message"]["content"]["parts"]
    assert "[REDACTED_EMAIL]" in parts[0]
    assert "[REDACTED_PHONE]" in parts[1]


def test_extract_dry_run(tmp_path, monkeypatch):
    input_path = tmp_path / "openai_export.json"
    _write_multi_thread_export(input_path)
    outdir = tmp_path / "artifacts"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "llm-logparser",
            "extract",
            "--provider",
            "openai",
            "--input",
            str(input_path),
            "--conversation-id",
            "conv-1",
            "--outdir",
            str(outdir),
            "--dry-run",
        ],
    )
    main()

    extract_path = outdir / "openai" / "thread-conv-1" / "extract.json"
    assert not extract_path.exists()
