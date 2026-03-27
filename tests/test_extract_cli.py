import json
import sys
from pathlib import Path

import pytest

from llm_logparser.cli.cli import main
from llm_logparser.cli.config_loader import load_config_file


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
            "credential_blob": "cred-raw",
            "notes": "email outside parts: outside.user@example.com",
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
                                "reference acct-1234",
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
    monkeypatch.chdir(tmp_path)

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
    monkeypatch.chdir(tmp_path)

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
    monkeypatch.chdir(tmp_path)

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
    assert parts[0] == "contact me at REDACTED"
    assert parts[1] == "phone: REDACTED"
    assert item["notes"] == "email outside parts: outside.user@example.com"

    meta_path = outdir / "openai" / "thread-conv-2" / "extract.meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["sanitize"]["enabled"] is True
    assert meta["sanitize"]["scope"] == "content_parts"
    assert meta["sanitize"]["replacement"] == "REDACTED"
    assert meta["sanitize"]["custom_keywords_supplied"] is False
    assert meta["sanitize"]["custom_mask_patterns_supplied"] is False


def test_extract_dry_run(tmp_path, monkeypatch):
    input_path = tmp_path / "openai_export.json"
    _write_multi_thread_export(input_path)
    outdir = tmp_path / "artifacts"
    monkeypatch.chdir(tmp_path)

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


def test_extract_respects_config_disabled_sanitize(tmp_path, monkeypatch):
    input_path = tmp_path / "openai_export.json"
    _write_multi_thread_export(input_path)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "active_profile: default",
                "profiles:",
                "  default:",
                "    provider: openai",
                "    input:",
                f"      path: {input_path.name}",
                "    extract:",
                "      outdir: artifacts",
                "      conversation_id: conv-2",
                "    sanitize:",
                "      enabled: false",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    main(["extract"])

    extract_path = tmp_path / "artifacts" / "openai" / "thread-conv-2" / "extract.json"
    payload = json.loads(extract_path.read_text(encoding="utf-8"))
    item = payload[0]
    assert item["api_key"] == "my-secret-key"
    assert item["notes"] == "email outside parts: outside.user@example.com"
    assert item["mapping"]["m2"]["message"]["content"]["parts"][0] == "contact me at test.user@example.com"

    meta = json.loads(
        (tmp_path / "artifacts" / "openai" / "thread-conv-2" / "extract.meta.json").read_text(
            encoding="utf-8"
        )
    )
    assert meta["sanitize"]["enabled"] is False


def test_extract_sanitize_custom_replacement_and_extra_keywords(tmp_path, monkeypatch):
    input_path = tmp_path / "openai_export.json"
    _write_multi_thread_export(input_path)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "active_profile: default",
                "profiles:",
                "  default:",
                "    provider: openai",
                "    input:",
                f"      path: {input_path.name}",
                "    extract:",
                "      outdir: artifacts",
                "      conversation_id: conv-2",
                "    sanitize:",
                "      enabled: true",
                '      replacement: "***"',
                "      scope: content_parts",
                "      extra_keywords:",
                "        - credential",
                "      mask_patterns:",
                "        - acct-\\d+",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    main(["extract"])

    item = json.loads(
        (tmp_path / "artifacts" / "openai" / "thread-conv-2" / "extract.json").read_text(
            encoding="utf-8"
        )
    )[0]
    assert item["credential_blob"] == "***"
    parts = item["mapping"]["m2"]["message"]["content"]["parts"]
    assert parts[0] == "contact me at test.user@example.com"
    assert parts[1] == "phone: +1 (415) 555-2671"
    assert parts[2] == "reference ***"

    meta = json.loads(
        (tmp_path / "artifacts" / "openai" / "thread-conv-2" / "extract.meta.json").read_text(
            encoding="utf-8"
        )
    )
    assert meta["sanitize"]["replacement"] == "***"
    assert meta["sanitize"]["extra_keyword_count"] == 1
    assert meta["sanitize"]["custom_keywords_supplied"] is True
    assert meta["sanitize"]["custom_mask_patterns_supplied"] is True


def test_extract_sanitize_empty_mask_patterns_disables_regex_only(tmp_path, monkeypatch, caplog):
    input_path = tmp_path / "openai_export.json"
    _write_multi_thread_export(input_path)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "active_profile: default",
                "profiles:",
                "  default:",
                "    provider: openai",
                "    input:",
                f"      path: {input_path.name}",
                "    extract:",
                "      outdir: artifacts",
                "      conversation_id: conv-2",
                "    sanitize:",
                "      enabled: true",
                "      mask_patterns: []",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    main(["extract"])

    item = json.loads(
        (tmp_path / "artifacts" / "openai" / "thread-conv-2" / "extract.json").read_text(
            encoding="utf-8"
        )
    )[0]
    assert item["api_key"] == "REDACTED"
    assert item["token"] == "REDACTED"
    assert item["mapping"]["m2"]["message"]["content"]["parts"][0] == "contact me at test.user@example.com"
    assert item["mapping"]["m2"]["message"]["content"]["parts"][1] == "phone: +1 (415) 555-2671"
    assert "built-in email/phone masking is not active" in caplog.text


def test_extract_sanitize_all_strings_scope_masks_non_content_strings(tmp_path, monkeypatch):
    input_path = tmp_path / "openai_export.json"
    _write_multi_thread_export(input_path)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "active_profile: default",
                "profiles:",
                "  default:",
                "    provider: openai",
                "    input:",
                f"      path: {input_path.name}",
                "    extract:",
                "      outdir: artifacts",
                "      conversation_id: conv-2",
                "    sanitize:",
                "      enabled: true",
                "      replacement: MASKED",
                "      scope: all_strings",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    main(["extract"])

    item = json.loads(
        (tmp_path / "artifacts" / "openai" / "thread-conv-2" / "extract.json").read_text(
            encoding="utf-8"
        )
    )[0]
    assert item["notes"] == "email outside parts: MASKED"
    assert item["mapping"]["m2"]["message"]["content"]["parts"][0] == "contact me at MASKED"


def test_invalid_sanitize_scope_fails_config_validation(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "profiles:",
                "  default:",
                "    provider: openai",
                "    sanitize:",
                "      scope: everywhere",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="sanitize.scope must be one of"):
        load_config_file(config_path)


def test_unknown_sanitize_key_warns_but_loads(tmp_path, caplog):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "profiles:",
                "  default:",
                "    provider: openai",
                "    sanitize:",
                "      enabled: true",
                "      typo_key: foo",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config_file(config_path)

    assert config.profiles["default"].sanitize.enabled is True
    assert "Unknown sanitize config key(s) under profiles.default.sanitize: typo_key" in caplog.text
