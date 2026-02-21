import json
import sys
from pathlib import Path

from llm_logparser.cli.cli import main


def _write_minimal_parsed_jsonl(path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
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


def test_export_without_config_keeps_default_utc(tmp_path, monkeypatch):
    parsed = tmp_path / "parsed.jsonl"
    out = tmp_path / "thread.md"
    _write_minimal_parsed_jsonl(parsed)

    monkeypatch.setattr(
        sys,
        "argv",
        ["llm-logparser", "export", "--input", str(parsed), "--out", str(out)],
    )
    main()

    md = out.read_text(encoding="utf-8")
    assert "2024-10-27 03:33" in md


def test_export_uses_timezone_from_explicit_config_profile(tmp_path, monkeypatch):
    parsed = tmp_path / "parsed.jsonl"
    out = tmp_path / "thread.md"
    config = tmp_path / "config.yaml"
    _write_minimal_parsed_jsonl(parsed)
    config.write_text(
        "\n".join(
            [
                "active_profile: default",
                "profiles:",
                "  default:",
                "    locale: ja-JP",
                "    timezone: Asia/Tokyo",
                "    provider: chatgpt",
                "    input:",
                "      paths: []",
                "    output:",
                "      split_by: size",
                "      split_size_mb: 20",
                "    logging:",
                "      level: info",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "llm-logparser",
            "--config",
            str(config),
            "export",
            "--input",
            str(parsed),
            "--out",
            str(out),
        ],
    )
    main()

    md = out.read_text(encoding="utf-8")
    assert "2024-10-27 12:33" in md


def test_cli_timezone_overrides_config_timezone(tmp_path, monkeypatch):
    parsed = tmp_path / "parsed.jsonl"
    out = tmp_path / "thread.md"
    config = tmp_path / "config.yaml"
    _write_minimal_parsed_jsonl(parsed)
    config.write_text(
        "\n".join(
            [
                "profiles:",
                "  default:",
                "    timezone: Asia/Tokyo",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "llm-logparser",
            "--config",
            str(config),
            "--profile",
            "default",
            "export",
            "--input",
            str(parsed),
            "--out",
            str(out),
            "--timezone",
            "UTC",
        ],
    )
    main()

    md = out.read_text(encoding="utf-8")
    assert "2024-10-27 03:33" in md
