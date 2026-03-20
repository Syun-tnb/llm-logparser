from pathlib import Path

import yaml

import llm_logparser.core.i18n as i18n_module
from llm_logparser.core.i18n import _, get_resource_list, resolve_locale, set_locale


def _locale_payload(locale: str) -> dict:
    path = Path(i18n_module.__file__).resolve().parent.parent / "i18n" / f"{locale}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_scalar_messages_are_loaded_from_yaml():
    en_payload = _locale_payload("en-US")
    ja_payload = _locale_payload("ja-JP")

    assert i18n_module._MESSAGES["en-US"]["cli.description"] == (
        en_payload["messages"]["cli.description"]
    )
    assert i18n_module._MESSAGES["ja-JP"]["error.path"] == (
        ja_payload["messages"]["error.path"]
    )


def test_scalar_translation_and_en_fallback_still_work():
    en_payload = _locale_payload("en-US")

    set_locale("fr-FR")

    assert _("runtime.export.preview_only") == en_payload["messages"][
        "runtime.export.preview_only"
    ]
    assert _("error.path", detail="detail") == en_payload["messages"]["error.path"].format(
        detail="detail"
    )


def test_missing_scalar_key_returns_raw_key():
    set_locale("en-US")

    assert _("missing.scalar.key") == "missing.scalar.key"


def test_structured_analyzer_resources_still_load_from_yaml():
    en_payload = _locale_payload("en-US")
    ja_payload = _locale_payload("ja-JP")

    assert get_resource_list("analysis.refusal.indicators", locale="en") == (
        en_payload["analysis"]["refusal"]["indicators"]
    )
    assert get_resource_list("analysis.refusal.indicators", locale="ja") == (
        ja_payload["analysis"]["refusal"]["indicators"]
    )


def test_locale_aliases_still_resolve_to_canonical_locales():
    assert resolve_locale("en") == "en-US"
    assert resolve_locale("ja") == "ja-JP"


def test_language_aliases_are_derived_from_discovered_locale_filenames():
    aliases = i18n_module._build_locale_aliases(["en-US", "ja-JP", "ko-KR"])

    assert aliases["en"] == "en-US"
    assert aliases["ja"] == "ja-JP"
    assert aliases["ko"] == "ko-KR"


def test_ambiguous_language_aliases_are_not_auto_derived():
    aliases = i18n_module._build_locale_aliases(["en-US", "en-GB", "ja-JP"])

    assert "en" not in aliases
    assert aliases["ja"] == "ja-JP"


def test_partial_locale_still_falls_back_safely(monkeypatch):
    en_payload = _locale_payload("en-US")
    monkeypatch.setitem(i18n_module.LOCALE_ALIASES, "ko", "ko-KR")
    monkeypatch.setitem(
        i18n_module._MESSAGES,
        "ko-KR",
        {"runtime.export.preview_only": "KO PREVIEW"},
    )
    monkeypatch.setitem(i18n_module._RESOURCE_MESSAGES, "ko-KR", {})

    assert i18n_module.t("error.path", "ko-KR", detail="detail") == en_payload["messages"][
        "error.path"
    ].format(detail="detail")
    assert get_resource_list("analysis.refusal.indicators", locale="ko") == (
        en_payload["analysis"]["refusal"]["indicators"]
    )
