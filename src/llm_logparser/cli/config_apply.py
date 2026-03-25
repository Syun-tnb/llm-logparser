from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from typing import Any

from llm_logparser.cli.config_model import AppConfig, ConfigProfile
from llm_logparser.core.i18n import _
from llm_logparser.core.sanitize import SanitizePolicy

REQUIRED_FIELDS_BY_COMMAND: dict[str, list[str]] = {
    "parse": ["provider", "input"],
    "export": ["input"],
    "chain": ["provider", "input"],
    "extract": ["provider", "input", "conversation_id"],
}


def parse_explicit_flags(argv: list[str]) -> set[str]:
    flags: set[str] = set()
    for token in argv:
        if token == "--":
            break
        if not token.startswith("-"):
            continue
        if token.startswith("--"):
            flags.add(token.split("=", 1)[0])
        else:
            flags.add(token)
    return flags


def cli_provided(explicit_flags: set[str], *flags: str) -> bool:
    return any(flag in explicit_flags for flag in flags)


def resolve_profile(
    config: AppConfig,
    profile_name: str | None,
) -> tuple[ConfigProfile | None, dict[str, ConfigProfile]]:
    profiles = config.profiles
    if profile_name:
        profile = profiles.get(profile_name)
        if profile is None:
            raise SystemExit(_("runtime.config.profile_not_found", name=profile_name))
        return profile, profiles

    active = config.active_profile
    if active:
        profile = profiles.get(active)
        if profile is None:
            raise SystemExit(_("runtime.config.profile_not_found", name=active))
        return profile, profiles

    if len(profiles) == 1:
        return next(iter(profiles.values())), profiles

    return None, profiles


def missing_required_fields(args: Namespace) -> list[str]:
    required = REQUIRED_FIELDS_BY_COMMAND.get(args.command, [])
    missing: list[str] = []
    for field in required:
        value = getattr(args, field, None)
        if value is None:
            missing.append(field)
        elif isinstance(value, str) and not value.strip():
            missing.append(field)
    return missing


def _resolve_path(value: Any, base_dir: Path | None) -> Path | None:
    if isinstance(value, Path):
        p = value
    elif isinstance(value, str) and value.strip():
        p = Path(value)
    else:
        return None
    if p.is_absolute() or base_dir is None:
        return p
    return (base_dir / p).resolve()


def _input_candidates(profile: ConfigProfile, command: str, base_dir: Path | None) -> list[str]:
    if command == "export":
        if profile.input.parsed:
            resolved = _resolve_path(profile.input.parsed, base_dir)
            return [str(resolved)] if resolved is not None else []

    if profile.input.path:
        resolved = _resolve_path(profile.input.path, base_dir)
        return [str(resolved)] if resolved is not None else []

    if profile.input.paths:
        out: list[str] = []
        for item in profile.input.paths:
            resolved = _resolve_path(item, base_dir)
            if resolved is not None:
                out.append(str(resolved))
        return out

    return []


def _set_if_not_cli(
    args: Namespace,
    explicit_flags: set[str],
    attr: str,
    flags: tuple[str, ...],
    value: Any,
    *,
    transform=lambda x: x,
) -> None:
    if value is None:
        return
    if cli_provided(explicit_flags, *flags):
        return
    setattr(args, attr, transform(value))


def apply_profile_defaults(
    args: Namespace,
    profile: ConfigProfile,
    explicit_flags: set[str],
    *,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    info: dict[str, Any] = {}
    _set_if_not_cli(
        args,
        explicit_flags,
        "log_level",
        ("--log-level",),
        profile.logging.level,
        transform=lambda v: str(v).upper(),
    )

    if args.command in ("export", "chain"):
        _set_if_not_cli(
            args,
            explicit_flags,
            "timezone",
            ("--timezone", "--tz"),
            profile.timezone,
        )

    if args.command in ("parse", "chain", "extract"):
        _set_if_not_cli(
            args,
            explicit_flags,
            "provider",
            ("--provider",),
            profile.provider,
        )

    if (
        args.command in ("parse", "export", "chain", "extract")
        or (
            args.command == "analyze"
            and getattr(args, "analyze_command", None)
            in {"semantic-prototype", "semantic-preview"}
        )
    ) and not cli_provided(explicit_flags, "--input"):
        candidates = _input_candidates(profile, args.command, base_dir)
        if len(candidates) == 1:
            args.input = Path(candidates[0])
        elif len(candidates) > 1:
            info["input_candidates"] = candidates

    if args.command == "parse":
        _set_if_not_cli(
            args,
            explicit_flags,
            "outdir",
            ("--outdir",),
            profile.parse.outdir,
            transform=lambda v: _resolve_path(v, base_dir) or Path(v),
        )

        for attr, flag in (("dry_run", "--dry-run"), ("fail_fast", "--fail-fast"), ("validate_schema", "--validate-schema")):
            value = getattr(profile.parse, attr)
            if value is not None:
                _set_if_not_cli(args, explicit_flags, attr, (flag,), value)

    elif args.command == "export":
        _set_if_not_cli(
            args,
            explicit_flags,
            "out",
            ("--out",),
            profile.output.path,
            transform=lambda v: _resolve_path(v, base_dir) or Path(v),
        )
        _set_if_not_cli(
            args,
            explicit_flags,
            "formatting",
            ("--formatting",),
            profile.output.formatting,
        )
        _set_if_not_cli(args, explicit_flags, "split", ("--split",), profile.output.split)

        for attr, flag in (("split_soft_overflow", "--split-soft-overflow"), ("tiny_tail_threshold", "--tiny-tail-threshold")):
            value = getattr(profile.output, attr)
            if value is not None:
                _set_if_not_cli(args, explicit_flags, attr, (flag,), value)

        for attr, flag in (("split_hard", "--split-hard"), ("split_preview", "--split-preview")):
            value = getattr(profile.output, attr)
            if value is not None:
                _set_if_not_cli(args, explicit_flags, attr, (flag,), value)

    elif args.command == "chain":
        _set_if_not_cli(
            args,
            explicit_flags,
            "outdir",
            ("--outdir",),
            profile.chain.outdir,
            transform=lambda v: _resolve_path(v, base_dir) or Path(v),
        )
        _set_if_not_cli(
            args,
            explicit_flags,
            "export_outdir",
            ("--export-outdir",),
            profile.chain.export_outdir,
            transform=lambda v: _resolve_path(v, base_dir) or Path(v),
        )
        _set_if_not_cli(
            args,
            explicit_flags,
            "parsed_root",
            ("--parsed-root",),
            profile.chain.parsed_root,
            transform=lambda v: _resolve_path(v, base_dir) or Path(v),
        )

        _set_if_not_cli(
            args,
            explicit_flags,
            "formatting",
            ("--formatting",),
            profile.output.formatting,
        )
        _set_if_not_cli(args, explicit_flags, "split", ("--split",), profile.output.split)

        for attr, flag in (("split_soft_overflow", "--split-soft-overflow"), ("tiny_tail_threshold", "--tiny-tail-threshold")):
            value = getattr(profile.output, attr)
            if value is not None:
                _set_if_not_cli(args, explicit_flags, attr, (flag,), value)

        for attr, flag in (("split_hard", "--split-hard"), ("split_preview", "--split-preview"), ("dry_run", "--dry-run"), ("fail_fast", "--fail-fast"), ("validate_schema", "--validate-schema")):
            value = getattr(profile.chain, attr) if attr in {"dry_run", "fail_fast", "validate_schema"} else getattr(profile.output, attr)
            if value is not None:
                _set_if_not_cli(args, explicit_flags, attr, (flag,), value)

    elif args.command == "extract":
        _set_if_not_cli(
            args,
            explicit_flags,
            "outdir",
            ("--outdir",),
            profile.extract.outdir,
            transform=lambda v: _resolve_path(v, base_dir) or Path(v),
        )
        _set_if_not_cli(
            args,
            explicit_flags,
            "conversation_id",
            ("--conversation-id",),
            profile.extract.conversation_id,
        )

        dry = profile.extract.dry_run
        if dry is not None:
            _set_if_not_cli(args, explicit_flags, "dry_run", ("--dry-run",), dry)

    elif args.command == "analyze" and args.analyze_command == "semantic-prototype":
        semantic = profile.analyze.semantic_prototype
        _set_if_not_cli(
            args,
            explicit_flags,
            "backend",
            ("--backend",),
            semantic.backend,
        )
        _set_if_not_cli(
            args,
            explicit_flags,
            "model",
            ("--model",),
            semantic.model,
        )
        _set_if_not_cli(
            args,
            explicit_flags,
            "top_k",
            ("--top-k",),
            semantic.top_k,
        )
        _set_if_not_cli(
            args,
            explicit_flags,
            "max_input_bytes",
            ("--max-input-bytes",),
            semantic.embedding.max_input_bytes,
        )
        _set_if_not_cli(
            args,
            explicit_flags,
            "chunk_overlap_bytes",
            ("--chunk-overlap-bytes",),
            semantic.embedding.chunk_overlap_bytes,
        )
        _set_if_not_cli(
            args,
            explicit_flags,
            "aggregate",
            ("--aggregate",),
            semantic.embedding.aggregate,
        )

    return info


def resolve_sanitize_policy(profile: ConfigProfile | None) -> SanitizePolicy:
    sanitize = profile.sanitize if profile is not None else None
    if sanitize is None:
        return SanitizePolicy.defaults()
    return SanitizePolicy.from_settings(
        enabled=sanitize.enabled,
        replacement=sanitize.replacement,
        scope=sanitize.scope,
        extra_keywords=sanitize.extra_keywords,
        mask_patterns=sanitize.mask_patterns,
    )
