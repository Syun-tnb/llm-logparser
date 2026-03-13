from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from typing import Any


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


def resolve_profile(config: dict[str, Any], profile_name: str | None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    profiles = config.get("profiles")
    if not isinstance(profiles, dict):
        if profile_name:
            raise SystemExit("Invalid config: 'profiles' must be a mapping")
        return None, {}

    if profile_name:
        profile = profiles.get(profile_name)
        if profile is None:
            raise SystemExit(f"Profile not found in config: {profile_name}")
        if not isinstance(profile, dict):
            raise SystemExit(f"Invalid profile '{profile_name}': profile must be a mapping")
        return profile, profiles

    active = config.get("active_profile")
    if isinstance(active, str) and active.strip():
        profile = profiles.get(active)
        if profile is None:
            raise SystemExit(f"Profile not found in config: {active}")
        if not isinstance(profile, dict):
            raise SystemExit(f"Invalid profile '{active}': profile must be a mapping")
        return profile, profiles

    if len(profiles) == 1:
        only = next(iter(profiles.values()))
        if not isinstance(only, dict):
            raise SystemExit("Invalid config: profile must be a mapping")
        return only, profiles

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


def _to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, int):
        return bool(value)
    return None


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


def _input_candidates(profile: dict[str, Any], command: str, base_dir: Path | None) -> list[str]:
    input_cfg = profile.get("input")
    if not isinstance(input_cfg, dict):
        return []

    if command == "export":
        parsed = input_cfg.get("parsed")
        if isinstance(parsed, str) and parsed.strip():
            resolved = _resolve_path(parsed, base_dir)
            return [str(resolved)] if resolved is not None else []

    path = input_cfg.get("path")
    if isinstance(path, str) and path.strip():
        resolved = _resolve_path(path, base_dir)
        return [str(resolved)] if resolved is not None else []

    paths = input_cfg.get("paths")
    if isinstance(paths, list):
        out: list[str] = []
        for item in paths:
            if isinstance(item, str) and item.strip():
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
    profile: dict[str, Any],
    explicit_flags: set[str],
    *,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    info: dict[str, Any] = {}

    _set_if_not_cli(args, explicit_flags, "locale", ("--locale", "--lang"), profile.get("locale"))

    logging_cfg = profile.get("logging")
    if isinstance(logging_cfg, dict):
        _set_if_not_cli(args, explicit_flags, "log_level", ("--log-level",), logging_cfg.get("level"), transform=lambda v: str(v).upper())

    if args.command in ("export", "chain"):
        _set_if_not_cli(args, explicit_flags, "timezone", ("--timezone", "--tz"), profile.get("timezone"))

    command_cfg_raw = profile.get(args.command)
    command_cfg = command_cfg_raw if isinstance(command_cfg_raw, dict) else {}
    output_cfg_raw = profile.get("output")
    output_cfg = output_cfg_raw if isinstance(output_cfg_raw, dict) else {}

    if args.command in ("parse", "chain", "extract"):
        _set_if_not_cli(args, explicit_flags, "provider", ("--provider",), profile.get("provider"))

    if args.command in ("parse", "export", "chain", "extract") and not cli_provided(explicit_flags, "--input"):
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
            command_cfg.get("outdir", profile.get("outdir")),
            transform=lambda v: _resolve_path(v, base_dir) or Path(v),
        )

        for attr, flag in (("dry_run", "--dry-run"), ("fail_fast", "--fail-fast"), ("validate_schema", "--validate-schema")):
            value = _to_bool(command_cfg.get(attr, profile.get(attr)))
            if value is not None:
                _set_if_not_cli(args, explicit_flags, attr, (flag,), value)

    elif args.command == "export":
        _set_if_not_cli(
            args,
            explicit_flags,
            "out",
            ("--out",),
            output_cfg.get("path"),
            transform=lambda v: _resolve_path(v, base_dir) or Path(v),
        )
        _set_if_not_cli(args, explicit_flags, "formatting", ("--formatting",), output_cfg.get("formatting"))
        _set_if_not_cli(args, explicit_flags, "split", ("--split",), output_cfg.get("split"))

        for attr, flag in (("split_soft_overflow", "--split-soft-overflow"), ("tiny_tail_threshold", "--tiny-tail-threshold")):
            value = output_cfg.get(attr)
            if value is not None:
                _set_if_not_cli(args, explicit_flags, attr, (flag,), value)

        for attr, flag in (("split_hard", "--split-hard"), ("split_preview", "--split-preview")):
            value = _to_bool(output_cfg.get(attr))
            if value is not None:
                _set_if_not_cli(args, explicit_flags, attr, (flag,), value)

    elif args.command == "chain":
        _set_if_not_cli(
            args,
            explicit_flags,
            "outdir",
            ("--outdir",),
            command_cfg.get("outdir", profile.get("outdir")),
            transform=lambda v: _resolve_path(v, base_dir) or Path(v),
        )
        _set_if_not_cli(
            args,
            explicit_flags,
            "export_outdir",
            ("--export-outdir",),
            command_cfg.get("export_outdir", profile.get("export_outdir")),
            transform=lambda v: _resolve_path(v, base_dir) or Path(v),
        )
        _set_if_not_cli(
            args,
            explicit_flags,
            "parsed_root",
            ("--parsed-root",),
            command_cfg.get("parsed_root", profile.get("parsed_root")),
            transform=lambda v: _resolve_path(v, base_dir) or Path(v),
        )

        _set_if_not_cli(args, explicit_flags, "formatting", ("--formatting",), output_cfg.get("formatting"))
        _set_if_not_cli(args, explicit_flags, "split", ("--split",), output_cfg.get("split"))

        for attr, flag in (("split_soft_overflow", "--split-soft-overflow"), ("tiny_tail_threshold", "--tiny-tail-threshold")):
            value = output_cfg.get(attr)
            if value is not None:
                _set_if_not_cli(args, explicit_flags, attr, (flag,), value)

        for attr, flag in (("split_hard", "--split-hard"), ("split_preview", "--split-preview"), ("dry_run", "--dry-run"), ("fail_fast", "--fail-fast"), ("validate_schema", "--validate-schema")):
            value = _to_bool(command_cfg.get(attr, profile.get(attr)) if attr in {"dry_run", "fail_fast", "validate_schema"} else output_cfg.get(attr))
            if value is not None:
                _set_if_not_cli(args, explicit_flags, attr, (flag,), value)

    elif args.command == "extract":
        _set_if_not_cli(
            args,
            explicit_flags,
            "outdir",
            ("--outdir",),
            command_cfg.get("outdir", profile.get("outdir")),
            transform=lambda v: _resolve_path(v, base_dir) or Path(v),
        )
        _set_if_not_cli(args, explicit_flags, "conversation_id", ("--conversation-id",), command_cfg.get("conversation_id", profile.get("conversation_id")))

        dry = _to_bool(command_cfg.get("dry_run", profile.get("dry_run")))
        if dry is not None:
            _set_if_not_cli(args, explicit_flags, "dry_run", ("--dry-run",), dry)

    return info
