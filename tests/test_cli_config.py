import json
import sys
from pathlib import Path

import pytest

from llm_logparser.cli.cli import main
from llm_logparser.cli.config_loader import load_config_file
from llm_logparser.cli.config_model import AppConfig


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
    monkeypatch.chdir(tmp_path)
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


def test_autodiscovery_uses_cwd_config_for_export(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    parsed = tmp_path / "parsed.jsonl"
    _write_minimal_parsed_jsonl(parsed)
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            [
                "active_profile: default",
                "profiles:",
                "  default:",
                "    timezone: Asia/Tokyo",
                "    input:",
                "      parsed: parsed.jsonl",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(sys, "argv", ["llm-logparser", "export"])
    main()

    out = tmp_path / f"{tmp_path.name}.md"
    md = out.read_text(encoding="utf-8")
    assert "2024-10-27 12:33" in md


def test_explicit_cli_overrides_config_timezone(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    parsed = tmp_path / "parsed.jsonl"
    out = tmp_path / "thread.md"
    _write_minimal_parsed_jsonl(parsed)
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            [
                "profiles:",
                "  default:",
                "    timezone: Asia/Tokyo",
                "    input:",
                "      parsed: parsed.jsonl",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "llm-logparser",
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


def test_non_interactive_missing_required_exits_2(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["llm-logparser", "--non-interactive", "parse"],
    )

    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2

    assert "Missing required options for 'parse'" in caplog.text
    assert "provider" in caplog.text
    assert "input" in caplog.text


def test_interactive_prompt_fills_missing_input(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    parsed = tmp_path / "parsed.jsonl"
    _write_minimal_parsed_jsonl(parsed)

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    answers = iter([str(parsed)])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))

    monkeypatch.setattr(sys, "argv", ["llm-logparser", "export"])
    main()

    out = tmp_path / f"{tmp_path.name}.md"
    assert out.exists()


def test_env_config_path_has_priority_over_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    parsed = tmp_path / "parsed.jsonl"
    _write_minimal_parsed_jsonl(parsed)

    cwd_config = tmp_path / "config.yaml"
    cwd_config.write_text(
        "\n".join(
            [
                "active_profile: default",
                "profiles:",
                "  default:",
                "    timezone: UTC",
                "    input:",
                "      parsed: parsed.jsonl",
            ]
        ),
        encoding="utf-8",
    )

    env_dir = tmp_path / "env"
    env_dir.mkdir(parents=True, exist_ok=True)
    env_config = env_dir / "env-config.yaml"
    env_config.write_text(
        "\n".join(
            [
                "active_profile: default",
                "profiles:",
                "  default:",
                "    timezone: Asia/Tokyo",
                "    input:",
                "      parsed: ../parsed.jsonl",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_LOGPARSER_CONFIG", str(env_config))

    monkeypatch.setattr(sys, "argv", ["llm-logparser", "export"])
    main()

    out = tmp_path / f"{tmp_path.name}.md"
    md = out.read_text(encoding="utf-8")
    assert "2024-10-27 12:33" in md


def test_non_interactive_multiple_input_paths_exits_2(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "config.yaml"
    (tmp_path / "a.jsonl").write_text("{}", encoding="utf-8")
    (tmp_path / "b.jsonl").write_text("{}", encoding="utf-8")
    config.write_text(
        "\n".join(
            [
                "active_profile: default",
                "profiles:",
                "  default:",
                "    input:",
                "      paths: [a.jsonl, b.jsonl]",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        sys,
        "argv",
        ["llm-logparser", "--non-interactive", "export"],
    )

    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


def test_load_config_file_returns_typed_config_and_normalizes_legacy_keys(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "schema_version: 1.0",
                "active_profile: default",
                "profiles:",
                "  default:",
                "    provider: openai",
                "    input:",
                "      path: export.json",
                "    outdir: artifacts",
                "    dry_run: true",
                "    parse:",
                "      validate_schema: true",
                "    extract:",
                "      conversation_id: conv-42",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config_file(config_path)

    assert isinstance(config, AppConfig)
    assert config.schema_version == "1"
    profile = config.profiles["default"]
    assert profile.parse.outdir == "artifacts"
    assert profile.chain.outdir == "artifacts"
    assert profile.extract.outdir == "artifacts"
    assert profile.parse.dry_run is True
    assert profile.parse.validate_schema is True
    assert profile.extract.conversation_id == "conv-42"


def test_unsupported_config_schema_version_exits(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "schema_version: 2",
                "profiles:",
                "  default:",
                "    provider: openai",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="Unsupported config schema_version '2'"):
        load_config_file(config_path)


def test_export_uses_canonical_output_path_from_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    parsed = tmp_path / "parsed.jsonl"
    out = tmp_path / "nested" / "thread.md"
    _write_minimal_parsed_jsonl(parsed)
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "active_profile: default",
                "profiles:",
                "  default:",
                "    timezone: Asia/Tokyo",
                "    input:",
                "      parsed: parsed.jsonl",
                "    output:",
                "      path: nested/thread.md",
            ]
        ),
        encoding="utf-8",
    )

    main(["export"])

    assert out.exists()
    md = out.read_text(encoding="utf-8")
    assert "2024-10-27 12:33" in md


def test_config_path_subcommand_prints_resolved_path(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "config.yaml"
    config.write_text("profiles: {}\n", encoding="utf-8")

    main(["config", "path"])

    captured = capsys.readouterr()
    assert captured.out.strip() == str(config)


def test_config_validate_subcommand_reports_valid(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "active_profile: default",
                "profiles:",
                "  default:",
                "    provider: openai",
            ]
        ),
        encoding="utf-8",
    )

    main(["config", "validate"])

    assert "Config structure is valid:" in caplog.text
    assert str(config) in caplog.text
    assert "(profile: default)" in caplog.text


def test_config_show_subcommand_prints_selected_profile(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "active_profile: default",
                "profiles:",
                "  default:",
                "    provider: openai",
                "    input:",
                "      path: exports/messages.json",
                "    output:",
                "      path: artifacts/thread.md",
            ]
        ),
        encoding="utf-8",
    )

    main(["config", "show"])

    captured = capsys.readouterr()
    assert "selected_profile: default" in captured.out
    assert "provider: openai" in captured.out
    assert "path: exports/messages.json" in captured.out
    assert "path: artifacts/thread.md" in captured.out


def test_config_show_preserves_unicode_yaml_output(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "active_profile: default",
                "profiles:",
                "  default:",
                "    locale: ja-JP",
                "    input:",
                "      path: 日本語/messages.json",
            ]
        ),
        encoding="utf-8",
    )

    main(["config", "show"])

    captured = capsys.readouterr()
    assert "日本語/messages.json" in captured.out
