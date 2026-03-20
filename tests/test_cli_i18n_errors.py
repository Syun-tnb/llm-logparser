import logging

import pytest

import llm_logparser.cli.cli as cli_module
from llm_logparser.cli.common import validate_path
from llm_logparser.core.i18n import _, set_locale


def test_validate_path_message_uses_i18n(tmp_path):
    missing = tmp_path / "missing.json"
    set_locale("ja-JP")

    with pytest.raises(FileNotFoundError) as exc:
        validate_path(missing, expect_file=True)

    assert str(exc.value) == _("error.path_not_found", path=missing.expanduser())


def test_missing_required_message_uses_i18n_and_en_fallback():
    set_locale("fr-FR")

    message = cli_module._missing_arg_message("parse", ["provider", "input"])

    assert message.splitlines()[0] == _("error.missing_required", command="parse")
    assert _("error.missing_required_hint.provider") in message
    assert _("error.missing_required_hint.input") in message


def test_migrated_keys_fall_back_to_english_for_unknown_locale():
    set_locale("fr-FR")

    assert _("error.path", detail="detail") == "Path error: detail"
    assert _("error.permission", detail="detail") == "Permission error: detail"
    assert _("error.unexpected", detail="detail") == "Unexpected error: detail"
    assert _("error.path_expected_dir", path="/tmp/example") == (
        "Directory path expected: /tmp/example"
    )


def test_top_level_path_error_uses_i18n_and_not_hardcoded_japanese(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.chdir(tmp_path)
    missing = tmp_path / "missing.json"
    caplog.set_level(logging.ERROR)

    with pytest.raises(SystemExit) as exc:
        cli_module.main(
            [
                "--locale",
                "fr-FR",
                "parse",
                "--provider",
                "openai",
                "--input",
                str(missing),
            ]
        )

    assert exc.value.code == 2
    assert "Path error:" in caplog.text
    assert "Path does not exist:" in caplog.text
    assert "パスエラー" not in caplog.text


def test_top_level_permission_error_uses_i18n_and_not_hardcoded_japanese(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.chdir(tmp_path)
    caplog.set_level(logging.ERROR)

    def raise_permission(_args, _logger):
        raise PermissionError("denied")

    monkeypatch.setattr(cli_module, "_dispatch", raise_permission)

    with pytest.raises(SystemExit) as exc:
        cli_module.main(
            [
                "--locale",
                "fr-FR",
                "parse",
                "--provider",
                "openai",
                "--input",
                "input.json",
            ]
        )

    assert exc.value.code == 3
    assert "Permission error: denied" in caplog.text
    assert "アクセス権限エラー" not in caplog.text


def test_top_level_unexpected_error_uses_i18n_and_not_hardcoded_japanese(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.chdir(tmp_path)
    caplog.set_level(logging.ERROR)

    def raise_unexpected(_args, _logger):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli_module, "_dispatch", raise_unexpected)

    with pytest.raises(SystemExit) as exc:
        cli_module.main(
            [
                "--locale",
                "fr-FR",
                "parse",
                "--provider",
                "openai",
                "--input",
                "input.json",
            ]
        )

    assert exc.value.code == 99
    assert "Unexpected error: boom" in caplog.text
    assert "予期しないエラー" not in caplog.text
