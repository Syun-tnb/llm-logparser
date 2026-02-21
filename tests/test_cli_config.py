import json
import sys
from pathlib import Path

import pytest

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


def test_non_interactive_missing_required_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["llm-logparser", "--non-interactive", "parse"],
    )

    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2

    output = capsys.readouterr().out
    assert "Missing required options for 'parse'" in output
    assert "provider" in output
    assert "input" in output


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
