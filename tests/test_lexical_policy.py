from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pytest

from llm_logparser.cli.cli import main
from llm_logparser.core.lexical_policy import (
    resolve_active_lexical_policy,
    summarize_resolved_lexical_policy,
    validate_reviewed_lexical_rule_file,
)


def _write_reviewed_lexical_rules(
    path: Path,
    *,
    owner_scope: str,
    scoring: dict[str, list[str]],
) -> Path:
    lines = [
        'schema_version: "0.1"',
        f'owner_scope: "{owner_scope}"',
        "rules:",
        "  topic_summary:",
        "    scoring:",
    ]
    for key, values in scoring.items():
        lines.append(f"      {key}:")
        if values:
            for value in values:
                lines.append(f"        - {json.dumps(value, ensure_ascii=False)}")
        else:
            lines.append("        []")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_validate_reviewed_project_lexical_rules_reports_valid(tmp_path: Path):
    path = _write_reviewed_lexical_rules(
        tmp_path / "project.yaml",
        owner_scope="project",
        scoring={"generic_tokens": ["projectnoise"]},
    )

    result = validate_reviewed_lexical_rule_file(path, owner_scope="project")

    assert result["valid"] is True
    assert result["owner_scope"] == "project"
    assert result["loaded_layer"]["kind"] == "reviewed_project_rules"
    assert re.fullmatch(r"[0-9a-f]{40}", result["sha1"])
    assert result["errors"] == []


def test_validate_reviewed_lexical_rules_reports_schema_errors(tmp_path: Path):
    path = _write_reviewed_lexical_rules(
        tmp_path / "project.yaml",
        owner_scope="user",
        scoring={"generic_tokens": ["wrongscope"]},
    )

    result = validate_reviewed_lexical_rule_file(path, owner_scope="project")

    assert result["valid"] is False
    assert result["errors"]
    assert "owner_scope" in result["errors"][0]


def test_validate_reviewed_lexical_rules_reports_malformed_yaml(tmp_path: Path):
    path = tmp_path / "bad.yaml"
    path.write_text("schema_version: [unterminated\n", encoding="utf-8")

    result = validate_reviewed_lexical_rule_file(path, owner_scope="project")

    assert result["valid"] is False
    assert result["errors"]


def test_resolve_active_lexical_policy_applies_project_then_user_precedence(
    tmp_path: Path,
):
    project = _write_reviewed_lexical_rules(
        tmp_path / "project.yaml",
        owner_scope="project",
        scoring={
            "generic_tokens": ["projectnoise"],
            "persona_weak_tokens": ["ProjectPersona"],
        },
    )
    user = _write_reviewed_lexical_rules(
        tmp_path / "user.yaml",
        owner_scope="user",
        scoring={
            "generic_tokens": ["usernoise", "projectnoise"],
            "persona_weak_tokens": ["UserPersona"],
        },
    )

    rules = resolve_active_lexical_policy(
        locale="en-US",
        project_lexical_rules=project,
        user_lexical_rules=user,
    )
    diagnostics = summarize_resolved_lexical_policy(rules)

    assert "projectnoise" in rules.topic_summary_scoring.generic_tokens
    assert "usernoise" in rules.topic_summary_scoring.generic_tokens
    assert rules.topic_summary_scoring.generic_tokens.count("projectnoise") == 1
    assert "projectpersona" in rules.topic_summary_scoring.persona_weak_tokens
    assert "userpersona" in rules.topic_summary_scoring.persona_weak_tokens
    reviewed_kinds = [
        layer["kind"]
        for layer in diagnostics["layers"]
        if layer["kind"].startswith("reviewed_")
    ]
    assert reviewed_kinds == ["reviewed_project_rules", "reviewed_user_rules"]
    assert diagnostics["project_rules"]["status"] == "loaded"
    assert diagnostics["user_rules"]["status"] == "loaded"
    assert diagnostics["category_counts"]["topic_summary_scoring.generic_tokens"] >= 2
    serialized = json.dumps(diagnostics, ensure_ascii=False)
    assert "projectnoise" not in serialized
    assert "usernoise" not in serialized


def test_lexical_policy_validate_cli_outputs_json(tmp_path: Path, capsys):
    logging.getLogger("llm_logparser").handlers.clear()
    path = _write_reviewed_lexical_rules(
        tmp_path / "project.yaml",
        owner_scope="project",
        scoring={"generic_tokens": ["projectnoise"]},
    )

    main(
        [
            "lexical",
            "policy",
            "validate",
            "--project-lexical-rules",
            str(path),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is True
    assert payload["results"][0]["owner_scope"] == "project"


def test_lexical_policy_validate_cli_fails_without_paths(capsys):
    logging.getLogger("llm_logparser").handlers.clear()
    with pytest.raises(SystemExit) as exc:
        main(["lexical", "policy", "validate"])

    assert exc.value.code != 0
    assert "provide --project-lexical-rules" in str(exc.value)


def test_lexical_policy_validate_cli_fails_for_invalid_yaml(
    tmp_path: Path,
    capsys,
):
    logging.getLogger("llm_logparser").handlers.clear()
    path = _write_reviewed_lexical_rules(
        tmp_path / "user.yaml",
        owner_scope="project",
        scoring={"generic_tokens": ["wrongscope"]},
    )

    with pytest.raises(SystemExit) as exc:
        main(
            [
                "lexical",
                "policy",
                "validate",
                "--user-lexical-rules",
                str(path),
                "--json",
            ]
        )

    assert exc.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is False
    assert payload["results"][0]["errors"]


def test_lexical_policy_resolve_cli_writes_diagnostics_json(
    tmp_path: Path,
):
    project = _write_reviewed_lexical_rules(
        tmp_path / "project.yaml",
        owner_scope="project",
        scoring={"generic_tokens": ["projectnoise"]},
    )
    out = tmp_path / "resolved.json"

    main(
        [
            "lexical",
            "policy",
            "resolve",
            "--locale",
            "ja-Kansai",
            "--project-lexical-rules",
            str(project),
            "--json",
            "--out",
            str(out),
        ]
    )

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["artifact_type"] == "resolved_lexical_policy_diagnostics"
    assert payload["requested_locale"] == "ja-Kansai"
    assert payload["resolved_locale"] == "ja-Kansai"
    assert payload["locale_chain"] == ["ja-Kansai", "ja-JP", "en-US"]
    assert payload["project_rules"]["status"] == "loaded"
    assert payload["user_rules"]["status"] == "not_provided"
    assert "topic_summary_scoring.generic_tokens" in payload["category_counts"]
    assert "projectnoise" not in json.dumps(payload, ensure_ascii=False)
