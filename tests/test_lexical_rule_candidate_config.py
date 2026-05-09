from __future__ import annotations

import re
from pathlib import Path

import pytest

from llm_logparser.resources.lexical_rule_candidate_config import (
    LexicalRuleCandidateConfigError,
    lexical_rule_candidate_config_diagnostics,
    load_lexical_rule_candidate_config,
    load_lexical_rule_candidate_config_from_path,
)


def test_builtin_lexical_rule_candidate_config_loads_with_diagnostics():
    config = load_lexical_rule_candidate_config()
    diagnostics = lexical_rule_candidate_config_diagnostics(config)

    assert diagnostics["rule_family"] == "lexical_rule_candidates"
    assert diagnostics["schema_version"] == "0.1"
    assert diagnostics["candidate_type_names"] == [
        "distinctive_allow_token",
        "generic_scoring_token",
        "persona_weak_token",
    ]
    assert diagnostics["enabled_candidate_type_names"] == [
        "distinctive_allow_token",
        "generic_scoring_token",
        "persona_weak_token",
    ]
    assert diagnostics["thresholds"]["generic_scoring_token"][
        "min_conversation_count"
    ] == 8
    assert diagnostics["scoring"]["weights"]["spread_score"] == 0.45
    assert diagnostics["domain_term_counts"]["distinctive_allow_token"] > 0
    assert diagnostics["locale_signal_counts"]["ja"]["honorific_suffixes"] == 4
    layer = diagnostics["layers"][0]
    assert layer["kind"] == "built_in_resource"
    assert layer["path"] == "resources/lexical_rule_candidates/default.yaml"
    assert re.fullmatch(r"[0-9a-f]{40}", layer["sha1"])


def test_invalid_lexical_rule_candidate_config_shape_fails_clearly(tmp_path: Path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        '\n'.join(
            [
                'schema_version: "0.1"',
                'rule_family: "lexical_rule_candidates"',
                "output: []",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        LexicalRuleCandidateConfigError,
        match="output",
    ):
        load_lexical_rule_candidate_config_from_path(path)


def test_invalid_lexical_rule_candidate_config_regex_fails_clearly(tmp_path: Path):
    source = Path("src/llm_logparser/resources/lexical_rule_candidates/default.yaml")
    rendered = source.read_text(encoding="utf-8")
    rendered = re.sub(r'url_like: ".+"', 'url_like: "["', rendered, count=1)
    path = tmp_path / "bad-regex.yaml"
    path.write_text(rendered, encoding="utf-8")

    with pytest.raises(
        LexicalRuleCandidateConfigError,
        match="regex",
    ):
        load_lexical_rule_candidate_config_from_path(path)
