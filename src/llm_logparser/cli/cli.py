# src/llm_logparser/cli.py
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from llm_logparser.cli.common import (
    setup_logger,
    validate_path,
    validate_split_option,
)
from llm_logparser.cli.config_apply import (
    apply_profile_defaults,
    missing_required_fields,
    parse_explicit_flags,
    resolve_profile,
)
from llm_logparser.cli.config_loader import load_config_with_discovery
from llm_logparser.cli.handlers import (
    run_analyze_stats,
    run_analyze_timeline,
    run_chain,
    run_export,
    run_extract,
    run_parse,
)
from llm_logparser.cli.parser_builder import build_parser
from llm_logparser.cli.prompts import (
    interactive_enabled,
    prompt_choice,
    prompt_existing_file,
    prompt_text,
)
from llm_logparser.core.i18n import set_locale


def _missing_arg_message(command: str, missing: list[str]) -> str:
    key_hint = {
        "provider": "--provider / config: provider",
        "input": "--input / config: input.path, input.paths, input.parsed(export)",
        "conversation_id": "--conversation-id / config: conversation_id",
    }
    lines = [f"Missing required options for '{command}':"]
    for name in missing:
        lines.append(f"  - {name}: {key_hint.get(name, 'CLI option or config value')}")
    return "\n".join(lines)


def _resolve_profile(
    args,
    *,
    can_prompt: bool,
) -> tuple[dict[str, Any] | None, dict[str, Any], Any, Any]:
    config, config_path = load_config_with_discovery(args.config)
    profile: dict[str, Any] | None = None
    profiles: dict[str, Any] = {}
    if config is not None:
        profile, profiles = resolve_profile(config, args.profile)
        if profile is None and can_prompt and len(profiles) > 1:
            selected = prompt_choice("Select profile:", list(profiles.keys()), allow_skip=True)
            if selected is not None:
                candidate = profiles.get(selected)
                if isinstance(candidate, dict):
                    profile = candidate
    return profile, profiles, config, config_path


def _apply_profile_input_defaults(
    args,
    profile: dict[str, Any] | None,
    *,
    explicit_flags: set[str],
    config_path,
) -> None:
    if profile is None:
        return

    extra_info = apply_profile_defaults(
        args,
        profile,
        explicit_flags,
        base_dir=config_path.parent if config_path is not None else None,
    )

    input_candidates = extra_info.get("input_candidates")
    can_prompt = interactive_enabled(
        non_interactive=args.non_interactive
        or os.getenv("LLM_LOGPARSER_NON_INTERACTIVE") == "1"
    )
    if (
        args.command in ("parse", "export", "chain", "extract")
        and args.input is None
        and isinstance(input_candidates, list)
        and input_candidates
    ):
        if can_prompt:
            selected_input = prompt_choice("Select input file:", [str(p) for p in input_candidates])
            if selected_input:
                args.input = Path(selected_input)
        else:
            print(
                "Multiple input paths found in config. Resolve ambiguity with --input "
                "or keep a single input.path/input.paths entry.",
                file=sys.stderr,
            )
            sys.exit(2)


def _prompt_missing_required(args, profile: dict[str, Any] | None, *, can_prompt: bool, logger) -> None:
    missing = missing_required_fields(args)
    if missing:
        if can_prompt:
            if "provider" in missing:
                provider_default = profile.get("provider") if isinstance(profile, dict) else None
                args.provider = prompt_text("Provider (e.g., openai):", default=provider_default)
            if "input" in missing:
                default_input = None
                if isinstance(profile, dict):
                    input_cfg = profile.get("input")
                    if isinstance(input_cfg, dict):
                        value = input_cfg.get("path") or input_cfg.get("parsed")
                        if isinstance(value, str) and value.strip():
                            default_input = value
                args.input = prompt_existing_file("Input file path:", default=default_input)
            if "conversation_id" in missing:
                conv_default = profile.get("conversation_id") if isinstance(profile, dict) else None
                args.conversation_id = prompt_text(
                    "Conversation ID:", default=conv_default if isinstance(conv_default, str) else None
                )
        else:
            logger.error(_missing_arg_message(args.command, missing))
            sys.exit(2)

    if (
        args.command == "analyze"
        and args.analyze_command in {"stats", "timeline"}
        and args.input is None
    ):
        if can_prompt:
            raw_input = prompt_text("Input parsed.jsonl or directory path:")
            args.input = Path(raw_input) if raw_input else None
        else:
            logger.error(
                f"Missing required options for 'analyze {args.analyze_command}':\n"
                "  - input: --input"
            )
            sys.exit(2)


def _dispatch(args, logger) -> None:
    if args.command == "parse":
        run_parse(args, logger)
    elif args.command == "export":
        run_export(args, logger)
    elif args.command == "extract":
        run_extract(args, logger)
    elif args.command == "analyze":
        if args.analyze_command == "stats":
            run_analyze_stats(args, logger)
        elif args.analyze_command == "timeline":
            run_analyze_timeline(args, logger)
    elif args.command == "chain":
        run_chain(args, logger)
    elif args.command == "viewer":
        logger.warning("[TODO] Viewer not implemented yet.")
    elif args.command == "config":
        logger.warning("[TODO] Config command not implemented yet.")


def main():
    set_locale()
    parser = build_parser()
    args = parser.parse_args()
    explicit_flags = parse_explicit_flags(sys.argv[1:])
    non_interactive = args.non_interactive or os.getenv("LLM_LOGPARSER_NON_INTERACTIVE") == "1"
    can_prompt = interactive_enabled(non_interactive=non_interactive)

    profile, _profiles, _config, config_path = _resolve_profile(args, can_prompt=can_prompt)
    _apply_profile_input_defaults(
        args,
        profile,
        explicit_flags=explicit_flags,
        config_path=config_path,
    )

    set_locale(args.locale)
    logger = setup_logger(args.log_level)
    _prompt_missing_required(args, profile, can_prompt=can_prompt, logger=logger)

    try:
        _dispatch(args, logger)
    except (FileNotFoundError, IsADirectoryError) as e:
        logger.error(f"パスエラー: {e}")
        sys.exit(2)
    except PermissionError as e:
        logger.error(f"アクセス権限エラー: {e}")
        sys.exit(3)
    except Exception as e:
        logger.exception(f"予期しないエラー: {e}")
        sys.exit(99)


__all__ = ["main", "setup_logger", "validate_path", "validate_split_option"]


if __name__ == "__main__":
    main()
