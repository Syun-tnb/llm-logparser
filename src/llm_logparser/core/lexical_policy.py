from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from yaml import YAMLError

from llm_logparser.resources.cross_thread_lexical import (
    CrossThreadLexicalRules,
    CrossThreadLexicalRulesError,
    cross_thread_lexical_rules_diagnostics,
    load_cross_thread_lexical_rules,
)

ReviewedLexicalRuleScope = Literal["project", "user"]


def validate_reviewed_lexical_rule_file(
    path: Path | str,
    *,
    owner_scope: ReviewedLexicalRuleScope,
    locale: str | None = None,
) -> dict[str, Any]:
    """Validate one explicitly reviewed lexical rule file.

    This is a read-only API for reviewed policy files. It intentionally uses
    the same resolver path as analyzers so validation and runtime behavior stay
    aligned.
    """

    target = Path(path).expanduser()
    result: dict[str, Any] = {
        "valid": False,
        "schema_version": "0.1",
        "owner_scope": owner_scope,
        "path": str(target),
        "errors": [],
        "warnings": [],
    }
    try:
        if owner_scope == "project":
            rules = load_cross_thread_lexical_rules(
                locale,
                project_rules_path=target,
            )
            layer_kind = "reviewed_project_rules"
        else:
            rules = load_cross_thread_lexical_rules(
                locale,
                user_rules_path=target,
            )
            layer_kind = "reviewed_user_rules"
    except (CrossThreadLexicalRulesError, OSError, YAMLError) as exc:
        result["errors"].append(str(exc))
        return result

    layer = next(
        (item for item in rules.provenance.layers if item.kind == layer_kind),
        None,
    )
    if layer is None:
        result["errors"].append(f"reviewed {owner_scope} lexical rules were not loaded")
        return result

    result.update(
        {
            "valid": True,
            "path": layer.path,
            "sha1": layer.sha1,
            "loaded_layer": {
                "kind": layer.kind,
                "path": layer.path,
                "sha1": layer.sha1,
                "owner_scope": layer.owner_scope,
                "schema_version": layer.schema_version,
            },
        }
    )
    return result


def resolve_active_lexical_policy(
    *,
    locale: str | None = None,
    project_lexical_rules: Path | str | None = None,
    user_lexical_rules: Path | str | None = None,
) -> CrossThreadLexicalRules:
    """Resolve active cross-thread lexical policy layers without writing artifacts."""

    return load_cross_thread_lexical_rules(
        locale,
        project_rules_path=project_lexical_rules,
        user_rules_path=user_lexical_rules,
    )


def summarize_resolved_lexical_policy(
    rules: CrossThreadLexicalRules,
) -> dict[str, Any]:
    """Return JSON-safe diagnostics for a resolved lexical policy."""

    diagnostics = cross_thread_lexical_rules_diagnostics(rules)
    return {
        "artifact_type": "resolved_lexical_policy_diagnostics",
        "schema_version": diagnostics["schema_version"],
        "rule_family": diagnostics["rule_family"],
        "locale": diagnostics["resolved_locale"],
        "requested_locale": diagnostics["requested_locale"],
        "resolved_locale": diagnostics["resolved_locale"],
        "locale_chain": diagnostics["locale_chain"],
        "layers": diagnostics["layers"],
        "project_rules": diagnostics["project_rules"],
        "user_rules": diagnostics["user_rules"],
        "category_counts": diagnostics["category_counts"],
        "warnings": [],
    }


def render_lexical_policy_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def render_lexical_policy_validation_text(payload: dict[str, Any]) -> str:
    lines = [
        "Lexical policy validation",
        f"- owner_scope: {payload['owner_scope']}",
        f"- path: {payload['path']}",
        f"- valid: {str(payload['valid']).lower()}",
    ]
    if payload.get("sha1"):
        lines.append(f"- sha1: {payload['sha1']}")
    errors = payload.get("errors", [])
    if errors:
        lines.append("- errors:")
        lines.extend(f"  - {item}" for item in errors)
    warnings = payload.get("warnings", [])
    if warnings:
        lines.append("- warnings:")
        lines.extend(f"  - {item}" for item in warnings)
    return "\n".join(lines)


def render_lexical_policy_resolution_text(payload: dict[str, Any]) -> str:
    lines = [
        "Resolved lexical policy",
        f"- rule_family: {payload['rule_family']}",
        f"- schema_version: {payload['schema_version']}",
        f"- requested_locale: {payload['requested_locale']}",
        f"- resolved_locale: {payload['resolved_locale']}",
        f"- locale_chain: {', '.join(payload['locale_chain'])}",
        "- layers:",
    ]
    for layer in payload["layers"]:
        label = layer["kind"]
        if "locale" in layer:
            label += f" ({layer['locale']})"
        if "owner_scope" in layer:
            label += f" ({layer['owner_scope']})"
        lines.append(f"  - {label}: {layer['path']} sha1={layer['sha1']}")
    lines.append("- category_counts:")
    for key, value in sorted(payload["category_counts"].items()):
        lines.append(f"  - {key}: {value}")
    if payload.get("warnings"):
        lines.append("- warnings:")
        lines.extend(f"  - {item}" for item in payload["warnings"])
    return "\n".join(lines)
