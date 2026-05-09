from __future__ import annotations

import logging

from yaml import YAMLError

from llm_logparser.cli.common import write_or_print
from llm_logparser.core.lexical_inspection import (
    LexicalInspectionError,
    inspect_lexical_candidate,
    inspect_observed_token,
    list_lexical_candidates,
    list_observed_tokens,
    render_lexical_candidate_inspection_text,
    render_lexical_candidate_list_text,
    render_lexical_inspection_json,
    render_observed_token_inspection_text,
    render_observed_token_list_text,
)
from llm_logparser.core.lexical_policy import (
    render_lexical_policy_json,
    render_lexical_policy_resolution_text,
    render_lexical_policy_validation_text,
    resolve_active_lexical_policy,
    summarize_resolved_lexical_policy,
    validate_reviewed_lexical_rule_file,
)
from llm_logparser.resources.cross_thread_lexical import CrossThreadLexicalRulesError


def run_lexical_observed(args, logger: logging.Logger) -> None:
    del logger
    try:
        if args.lexical_observed_command == "list":
            payload = list_observed_tokens(args.input, limit=args.limit)
            rendered = (
                render_lexical_inspection_json(payload)
                if args.json_output
                else render_observed_token_list_text(payload)
            )
            write_or_print(rendered, args.out)
            return
        if args.lexical_observed_command == "inspect":
            payload = inspect_observed_token(args.input, token=args.token)
            rendered = (
                render_lexical_inspection_json(payload)
                if args.json_output
                else render_observed_token_inspection_text(payload)
            )
            write_or_print(rendered, args.out)
            return
    except LexicalInspectionError as exc:
        raise SystemExit(str(exc)) from None
    raise SystemExit(f"unsupported lexical observed command: {args.lexical_observed_command}")


def run_lexical_candidates(args, logger: logging.Logger) -> None:
    del logger
    try:
        if args.lexical_candidates_command == "list":
            payload = list_lexical_candidates(args.input, limit=args.limit)
            rendered = (
                render_lexical_inspection_json(payload)
                if args.json_output
                else render_lexical_candidate_list_text(payload)
            )
            write_or_print(rendered, args.out)
            return
        if args.lexical_candidates_command == "inspect":
            payload = inspect_lexical_candidate(
                args.input,
                candidate_id=args.candidate_id,
            )
            rendered = (
                render_lexical_inspection_json(payload)
                if args.json_output
                else render_lexical_candidate_inspection_text(payload)
            )
            write_or_print(rendered, args.out)
            return
    except LexicalInspectionError as exc:
        raise SystemExit(str(exc)) from None
    raise SystemExit(f"unsupported lexical candidates command: {args.lexical_candidates_command}")


def run_lexical_policy(args, logger: logging.Logger) -> None:
    if args.lexical_policy_command == "validate":
        _run_lexical_policy_validate(args, logger)
        return
    if args.lexical_policy_command == "resolve":
        _run_lexical_policy_resolve(args, logger)
        return
    raise SystemExit(f"unsupported lexical policy command: {args.lexical_policy_command}")


def _run_lexical_policy_validate(args, logger: logging.Logger) -> None:
    del logger
    validations: list[dict] = []
    if args.project_lexical_rules is not None:
        validations.append(
            validate_reviewed_lexical_rule_file(
                args.project_lexical_rules,
                owner_scope="project",
                locale=args.locale,
            )
        )
    if args.user_lexical_rules is not None:
        validations.append(
            validate_reviewed_lexical_rule_file(
                args.user_lexical_rules,
                owner_scope="user",
                locale=args.locale,
            )
        )
    if not validations:
        raise SystemExit(
            "provide --project-lexical-rules, --user-lexical-rules, or both"
        )

    payload = {
        "artifact_type": "lexical_policy_validation",
        "schema_version": "0.1",
        "valid": all(item["valid"] for item in validations),
        "results": validations,
    }
    rendered = (
        render_lexical_policy_json(payload)
        if args.json_output
        else _render_validation_results_text(payload)
    )
    write_or_print(rendered, args.out)
    if not payload["valid"]:
        raise SystemExit(2)


def _run_lexical_policy_resolve(args, logger: logging.Logger) -> None:
    del logger
    try:
        rules = resolve_active_lexical_policy(
            locale=args.policy_locale or args.locale,
            project_lexical_rules=args.project_lexical_rules,
            user_lexical_rules=args.user_lexical_rules,
        )
    except (CrossThreadLexicalRulesError, OSError, YAMLError) as exc:
        raise SystemExit(str(exc)) from None
    payload = summarize_resolved_lexical_policy(rules)
    rendered = (
        render_lexical_policy_json(payload)
        if args.json_output
        else render_lexical_policy_resolution_text(payload)
    )
    write_or_print(rendered, args.out)


def _render_validation_results_text(payload: dict) -> str:
    sections = [
        "Lexical policy validation",
        f"- valid: {str(payload['valid']).lower()}",
        "",
    ]
    for index, result in enumerate(payload["results"]):
        if index:
            sections.append("")
        sections.append(render_lexical_policy_validation_text(result))
    return "\n".join(sections)
