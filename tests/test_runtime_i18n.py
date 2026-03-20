import logging

import pytest

import llm_logparser.core.i18n as i18n_module
from llm_logparser.cli.cli import main
from llm_logparser.cli.common import resolve_timezone, validate_split_option
from llm_logparser.cli.prompts import prompt_text
from llm_logparser.core.i18n import _, set_locale


def test_runtime_keys_fall_back_to_english_for_unknown_locale():
    set_locale("fr-FR")

    assert _("runtime.split_invalid", raw="bad") == "invalid --split: bad"
    assert _("runtime.config.no_file") == "No config file found."
    assert _("runtime.export.preview_only") == "Preview only (no files written)"


def test_validate_split_option_uses_runtime_i18n_key(monkeypatch):
    set_locale("en-US")
    monkeypatch.setitem(
        i18n_module._MESSAGES["en-US"],
        "runtime.split_invalid",
        "SENTINEL split {raw}",
    )

    with pytest.raises(SystemExit, match="SENTINEL split bad"):
        validate_split_option("bad")


def test_prompt_text_uses_runtime_i18n_key(monkeypatch, capsys):
    set_locale("en-US")
    monkeypatch.setitem(
        i18n_module._MESSAGES["en-US"],
        "runtime.prompt.enter_value",
        "SENTINEL enter value",
    )
    answers = iter(["", "ok"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))

    assert prompt_text("Label:") == "ok"
    captured = capsys.readouterr()
    assert "SENTINEL enter value" in captured.out


def test_resolve_timezone_uses_runtime_i18n_key(monkeypatch, caplog):
    set_locale("en-US")
    monkeypatch.setitem(
        i18n_module._MESSAGES["en-US"],
        "runtime.timezone_unknown",
        "SENTINEL timezone {timezone_name}",
    )
    logger = logging.getLogger("test.runtime.timezone")
    caplog.set_level(logging.WARNING, logger=logger.name)

    resolve_timezone("Bad/Zone", logger)

    assert "SENTINEL timezone Bad/Zone" in caplog.text


def test_config_validate_runtime_message_uses_i18n_key(tmp_path, monkeypatch, caplog):
    set_locale("en-US")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(
        i18n_module._MESSAGES["en-US"],
        "runtime.config.valid",
        "SENTINEL config valid {path} {selected}",
    )
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

    caplog.set_level(logging.INFO)
    main(["config", "validate"])

    assert f"SENTINEL config valid {config} default" in caplog.text


def test_export_runtime_message_uses_i18n_key(tmp_path, monkeypatch, caplog):
    set_locale("en-US")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(
        i18n_module._MESSAGES["en-US"],
        "runtime.export.preview_only",
        "SENTINEL export preview",
    )

    parsed = tmp_path / "parsed.jsonl"
    parsed.write_text(
        "\n".join(
            [
                '{"record_type":"thread","provider_id":"openai","conversation_id":"conv-1","message_count":1}',
                '{"record_type":"message","provider_id":"openai","conversation_id":"conv-1","message_id":"m1","parent_id":null,"role":"assistant","ts":1730000001000,"content":{"content_type":"text","parts":["Hi"]},"text":"Hi"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    caplog.set_level(logging.INFO)
    main(["export", "--input", str(parsed), "--split-preview"])

    assert "SENTINEL export preview" in caplog.text
