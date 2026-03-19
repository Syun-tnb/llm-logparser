# src/llm_logparser/cli.py
from __future__ import annotations

import os
import sys
from pathlib import Path

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
    resolve_sanitize_policy,
)
from llm_logparser.cli.config_loader import load_config_with_discovery
from llm_logparser.cli.handlers import (
    run_analyze_sqlite_build,
    run_analyze_stats,
    run_analyze_tokens,
    run_analyze_timeline,
    run_chain,
    run_config_command,
    run_export,
    run_extract,
    run_parse,
)
from llm_logparser.cli.config_model import AppConfig, ConfigProfile
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
        "conversation_id": "--conversation-id / config: extract.conversation_id",
    }
    lines = [f"Missing required options for '{command}':"]
    for name in missing:
        lines.append(f"  - {name}: {key_hint.get(name, 'CLI option or config value')}")
    return "\n".join(lines)


def _resolve_profile(
    args,
    *,
    can_prompt: bool,
) -> tuple[ConfigProfile | None, dict[str, ConfigProfile], AppConfig | None, Path | None]:
    config, config_path = load_config_with_discovery(args.config)
    profile: ConfigProfile | None = None
    profiles: dict[str, ConfigProfile] = {}
    if config is not None:
        profile, profiles = resolve_profile(config, args.profile)
        if profile is None and can_prompt and len(profiles) > 1:
            selected = prompt_choice("Select profile:", list(profiles.keys()), allow_skip=True)
            if selected is not None:
                candidate = profiles.get(selected)
                if candidate is not None:
                    profile = candidate
    return profile, profiles, config, config_path


def _apply_profile_input_defaults(
    args,
    profile: ConfigProfile | None,
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


def _prompt_missing_required(
    args,
    profile: ConfigProfile | None,
    *,
    can_prompt: bool,
    logger,
) -> None:
    missing = missing_required_fields(args)
    if missing:
        if can_prompt:
            if "provider" in missing:
                provider_default = profile.provider if profile is not None else None
                args.provider = prompt_text("Provider (e.g., openai):", default=provider_default)
            if "input" in missing:
                default_input = None
                if profile is not None:
                    default_input = profile.input.path or profile.input.parsed
                args.input = prompt_existing_file("Input file path:", default=default_input)
            if "conversation_id" in missing:
                conv_default = profile.extract.conversation_id if profile is not None else None
                args.conversation_id = prompt_text(
                    "Conversation ID:",
                    default=conv_default,
                )
        else:
            logger.error(_missing_arg_message(args.command, missing))
            sys.exit(2)

    if (
        args.command == "analyze"
        and args.analyze_command in {"stats", "timeline", "tokens", "sqlite-build"}
    ):
        if args.input is None:
            if can_prompt:
                prompt_label = (
                    "Input provider-root directory path:"
                    if args.analyze_command == "sqlite-build"
                    else "Input parsed.jsonl or directory path:"
                )
                raw_input = prompt_text(prompt_label)
                args.input = Path(raw_input) if raw_input else None
            else:
                logger.error(
                    f"Missing required options for 'analyze {args.analyze_command}':\n"
                    "  - input: --input"
                )
                sys.exit(2)

        if args.analyze_command == "sqlite-build" and not args.provider:
            if can_prompt:
                args.provider = prompt_text("Provider ID (for example: openai):")
            else:
                logger.error(
                    "Missing required options for 'analyze sqlite-build':\n"
                    "  - provider: --provider"
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
        elif args.analyze_command == "tokens":
            run_analyze_tokens(args, logger)
        elif args.analyze_command == "timeline":
            run_analyze_timeline(args, logger)
        elif args.analyze_command == "sqlite-build":
            run_analyze_sqlite_build(args, logger)
    elif args.command == "chain":
        run_chain(args, logger)
    elif args.command == "viewer":
        logger.warning("[TODO] Viewer not implemented yet.")
    elif args.command == "config":
        run_config_command(args, logger)


def main(argv: list[str] | None = None):
    set_locale()
    parser = build_parser()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(raw_argv)
    explicit_flags = parse_explicit_flags(raw_argv)
    non_interactive = args.non_interactive or os.getenv("LLM_LOGPARSER_NON_INTERACTIVE") == "1"
    can_prompt = interactive_enabled(non_interactive=non_interactive)

    if args.command == "config":
        set_locale(args.locale)
        logger = setup_logger(args.log_level)
        _dispatch(args, logger)
        return

    profile: ConfigProfile | None = None
    config_path: Path | None = None
    if args.command in {"parse", "export", "chain", "extract"}:
        profile, _profiles, _config, config_path = _resolve_profile(
            args,
            can_prompt=can_prompt,
        )
        _apply_profile_input_defaults(
            args,
            profile,
            explicit_flags=explicit_flags,
            config_path=config_path,
        )
        if args.command == "extract":
            args.sanitize_policy = resolve_sanitize_policy(profile)

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
