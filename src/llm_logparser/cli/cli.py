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
    run_analyze_cross_thread_candidates,
    run_analyze_datasheet,
    run_analyze_metrics,
    run_analyze_semantic_normalization,
    run_analyze_semantic_preview,
    run_analyze_semantic_span_proposals,
    run_analyze_semantic_prototype,
    run_analyze_semantic_topic,
    run_analyze_semantic_topic_explore,
    run_analyze_semantic_topics,
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
from llm_logparser.core.i18n import _, set_locale


def _bootstrap_cli_locale(raw_argv: list[str]) -> str | None:
    for index, token in enumerate(raw_argv):
        if token == "--":
            break
        if token.startswith("--locale="):
            return token.split("=", 1)[1] or None
        if token.startswith("--lang="):
            return token.split("=", 1)[1] or None
        if token in {"--locale", "--lang"}:
            if index + 1 < len(raw_argv):
                return raw_argv[index + 1]
            return None
    return None


def _missing_arg_message(command: str, missing: list[str]) -> str:
    key_hint = {
        "provider": _("error.missing_required_hint.provider"),
        "input": _("error.missing_required_hint.input"),
        "conversation_id": _("error.missing_required_hint.conversation_id"),
    }
    lines = [_("error.missing_required", command=command)]
    for name in missing:
        lines.append(
            f"  - {name}: {key_hint.get(name, _('error.missing_required_hint.generic'))}"
        )
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
            selected = prompt_choice(
                _("runtime.profile.select"),
                list(profiles.keys()),
                allow_skip=True,
            )
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
            selected_input = prompt_choice(
                _("runtime.profile.select_input"),
                [str(p) for p in input_candidates],
            )
            if selected_input:
                args.input = Path(selected_input)
        else:
            print(
                _("runtime.profile.multiple_input_paths"),
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
                args.provider = prompt_text(
                    _("runtime.prompt.provider"),
                    default=provider_default,
                )
            if "input" in missing:
                default_input = None
                if profile is not None:
                    default_input = profile.input.path or profile.input.parsed
                args.input = prompt_existing_file(
                    _("runtime.prompt.input_file_path"),
                    default=default_input,
                )
            if "conversation_id" in missing:
                conv_default = profile.extract.conversation_id if profile is not None else None
                args.conversation_id = prompt_text(
                    _("runtime.prompt.conversation_id"),
                    default=conv_default,
                )
        else:
            logger.error(_missing_arg_message(args.command, missing))
            sys.exit(2)

    if (
        args.command == "analyze"
        and args.analyze_command in {
            "stats",
            "datasheet",
            "timeline",
            "tokens",
            "metrics",
            "sqlite-build",
            "semantic-prototype",
            "semantic-preview",
            "semantic-span-proposals",
            "cross-thread-candidates",
            "semantic-normalization",
            "semantic-topic",
            "semantic-topics",
            "semantic-topic-explore",
        }
    ):
        if args.input is None:
            if can_prompt:
                prompt_label = (
                    _("runtime.prompt.analyze_provider_root")
                    if args.analyze_command
                    in {
                        "sqlite-build",
                        "semantic-normalization",
                        "semantic-span-proposals",
                        "cross-thread-candidates",
                        "semantic-topics",
                    }
                    else _("runtime.prompt.analyze_input")
                )
                raw_input = prompt_text(prompt_label)
                args.input = Path(raw_input) if raw_input else None
            else:
                if args.analyze_command == "semantic-topics":
                    logger.error(_("runtime.analyze.semantic_topics.missing_input_explicit"))
                else:
                    logger.error(_("runtime.analyze.missing_input", command=args.analyze_command))
                sys.exit(2)

        if args.analyze_command == "sqlite-build" and not args.provider:
            if can_prompt:
                args.provider = prompt_text(_("runtime.prompt.provider_id"))
            else:
                logger.error(_("runtime.analyze.missing_provider"))
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
        elif args.analyze_command == "datasheet":
            run_analyze_datasheet(args, logger)
        elif args.analyze_command == "metrics":
            run_analyze_metrics(args, logger)
        elif args.analyze_command == "tokens":
            run_analyze_tokens(args, logger)
        elif args.analyze_command == "timeline":
            run_analyze_timeline(args, logger)
        elif args.analyze_command == "sqlite-build":
            run_analyze_sqlite_build(args, logger)
        elif args.analyze_command == "semantic-prototype":
            run_analyze_semantic_prototype(args, logger)
        elif args.analyze_command == "semantic-preview":
            run_analyze_semantic_preview(args, logger)
        elif args.analyze_command == "semantic-span-proposals":
            run_analyze_semantic_span_proposals(args, logger)
        elif args.analyze_command == "cross-thread-candidates":
            run_analyze_cross_thread_candidates(args, logger)
        elif args.analyze_command == "semantic-normalization":
            run_analyze_semantic_normalization(args, logger)
        elif args.analyze_command == "semantic-topic":
            run_analyze_semantic_topic(args, logger)
        elif args.analyze_command == "semantic-topics":
            run_analyze_semantic_topics(args, logger)
        elif args.analyze_command == "semantic-topic-explore":
            run_analyze_semantic_topic_explore(args, logger)
    elif args.command == "chain":
        run_chain(args, logger)
    elif args.command == "viewer":
        logger.warning(_("runtime.viewer.todo"))
    elif args.command == "config":
        run_config_command(args, logger)


def main(argv: list[str] | None = None):
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    set_locale(_bootstrap_cli_locale(raw_argv))
    parser = build_parser()
    args = parser.parse_args(raw_argv)
    explicit_flags = parse_explicit_flags(raw_argv)
    non_interactive = args.non_interactive or os.getenv("LLM_LOGPARSER_NON_INTERACTIVE") == "1"
    can_prompt = interactive_enabled(non_interactive=non_interactive)

    if args.command == "config":
        config, _config_path = load_config_with_discovery(args.config)
        config_locale = None
        if config is not None:
            profile, _profiles = resolve_profile(config, args.profile)
            if profile is not None:
                config_locale = profile.locale
        set_locale(args.locale, config_locale=config_locale)
        logger = setup_logger(args.log_level)
        _dispatch(args, logger)
        return

    profile: ConfigProfile | None = None
    config_path: Path | None = None
    config_locale: str | None = None
    if args.command in {"parse", "export", "chain", "extract", "analyze"}:
        # Keep analyze on the same locale resolution and profile-default path as
        # the other runtime commands. Individual analyze subcommands still own
        # which options are safe to inherit from config.
        profile, _profiles, _config, config_path = _resolve_profile(
            args,
            can_prompt=can_prompt,
        )
        if profile is not None:
            config_locale = profile.locale
        _apply_profile_input_defaults(
            args,
            profile,
            explicit_flags=explicit_flags,
            config_path=config_path,
        )
        if args.command == "extract":
            args.sanitize_policy = resolve_sanitize_policy(profile)

    set_locale(args.locale, config_locale=config_locale)
    logger = setup_logger(args.log_level)
    _prompt_missing_required(args, profile, can_prompt=can_prompt, logger=logger)

    try:
        _dispatch(args, logger)
    except (FileNotFoundError, IsADirectoryError) as e:
        logger.error(_("error.path", detail=e))
        sys.exit(2)
    except PermissionError as e:
        logger.error(_("error.permission", detail=e))
        sys.exit(3)
    except Exception as e:
        logger.exception(_("error.unexpected", detail=e))
        sys.exit(99)


__all__ = ["main", "setup_logger", "validate_path", "validate_split_option"]


if __name__ == "__main__":
    main()
