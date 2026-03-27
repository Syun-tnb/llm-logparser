import logging

import pytest

import llm_logparser.core.i18n as i18n_module
from llm_logparser.cli.cli import main
from llm_logparser.core.i18n import _, resolve_locale, set_locale


def _write_parsed_jsonl(path):
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


def test_resolve_locale_uses_config_when_cli_and_env_are_absent(monkeypatch):
    monkeypatch.delenv("LLP_LOCALE", raising=False)

    assert resolve_locale(config_locale="ja-JP") == "ja-JP"


def test_resolve_locale_does_not_let_config_override_env(monkeypatch):
    monkeypatch.setenv("LLP_LOCALE", "ja-JP")

    assert resolve_locale(config_locale="en-US") == "ja-JP"


def test_resolve_locale_does_not_let_config_override_cli(monkeypatch):
    monkeypatch.setenv("LLP_LOCALE", "ja-JP")

    assert resolve_locale("en-US", config_locale="ja-JP") == "en-US"


def test_unknown_locale_still_falls_back_to_en_us_even_with_config(monkeypatch):
    monkeypatch.setenv("LLP_LOCALE", "fr-FR")

    assert resolve_locale(config_locale="ja-JP") == "en-US"
    assert resolve_locale("fr-FR", config_locale="ja-JP") == "en-US"


def test_runtime_locale_switching_updates_active_locale(monkeypatch):
    monkeypatch.delenv("LLP_LOCALE", raising=False)
    monkeypatch.setitem(
        i18n_module._MESSAGES["en-US"],
        "runtime.export.preview_only",
        "EN PREVIEW",
    )
    monkeypatch.setitem(
        i18n_module._MESSAGES["ja-JP"],
        "runtime.export.preview_only",
        "JA PREVIEW",
    )

    set_locale()
    assert _("runtime.export.preview_only") == "EN PREVIEW"

    set_locale(config_locale="ja-JP")
    assert _("runtime.export.preview_only") == "JA PREVIEW"


def test_bootstrap_cli_locale_overrides_env_and_config(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLP_LOCALE", "en-US")
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "active_profile: default",
                "profiles:",
                "  default:",
                "    locale: en-US",
                "    provider: openai",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc:
        main(["--locale", "ja-JP", "--help"])

    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert _("cli.description") == "LLM Log Parser 用のCLIインターフェース（MVP）"
    assert "LLM Log Parser 用のCLIインターフェース（MVP）" in help_text


def test_env_locale_applies_when_cli_is_absent(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLP_LOCALE", "ja-JP")
    monkeypatch.setitem(
        i18n_module._MESSAGES["ja-JP"],
        "runtime.export.preview_only",
        "JA PREVIEW",
    )
    parsed = tmp_path / "parsed.jsonl"
    _write_parsed_jsonl(parsed)

    caplog.set_level(logging.INFO)
    main(["export", "--input", str(parsed), "--split-preview"])

    assert "JA PREVIEW" in caplog.text


def test_config_locale_applies_when_cli_and_env_are_absent(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LLP_LOCALE", raising=False)
    monkeypatch.setitem(
        i18n_module._MESSAGES["ja-JP"],
        "runtime.export.preview_only",
        "JA CONFIG PREVIEW",
    )
    parsed = tmp_path / "parsed.jsonl"
    _write_parsed_jsonl(parsed)
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
                f"      parsed: {parsed}",
            ]
        ),
        encoding="utf-8",
    )

    caplog.set_level(logging.INFO)
    main(["export", "--split-preview"])

    assert "JA CONFIG PREVIEW" in caplog.text


def test_config_does_not_override_env_locale(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLP_LOCALE", "en-US")
    monkeypatch.setitem(
        i18n_module._MESSAGES["en-US"],
        "runtime.export.preview_only",
        "EN ENV PREVIEW",
    )
    monkeypatch.setitem(
        i18n_module._MESSAGES["ja-JP"],
        "runtime.export.preview_only",
        "JA CONFIG PREVIEW",
    )
    parsed = tmp_path / "parsed.jsonl"
    _write_parsed_jsonl(parsed)
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
                f"      parsed: {parsed}",
            ]
        ),
        encoding="utf-8",
    )

    caplog.set_level(logging.INFO)
    main(["export", "--split-preview"])

    assert "EN ENV PREVIEW" in caplog.text
    assert "JA CONFIG PREVIEW" not in caplog.text


def test_analyze_uses_profile_locale_when_cli_and_env_are_absent(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LLP_LOCALE", raising=False)
    monkeypatch.setitem(
        i18n_module._MESSAGES["ja-JP"],
        "runtime.analyze.top_non_negative",
        "JA ANALYZE TOP",
    )

    parsed = tmp_path / "parsed.jsonl"
    _write_parsed_jsonl(parsed)
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "active_profile: default",
                "profiles:",
                "  default:",
                "    locale: ja-JP",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="JA ANALYZE TOP"):
        main(["analyze", "stats", "--input", str(parsed), "--top", "-1"])
