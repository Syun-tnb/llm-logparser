from __future__ import annotations

import logging
from pathlib import Path

from llm_logparser.cli.config_apply import resolve_profile
from llm_logparser.cli.config_loader import (
    discover_config_path,
    load_config_file,
    resolve_explicit_config_path,
)


def _resolved_config_path(explicit_path: Path | None) -> Path | None:
    if explicit_path is not None:
        return resolve_explicit_config_path(explicit_path, missing_hint="passed via --config")
    return discover_config_path()


def _dump_yaml(data: dict[str, object]) -> str:
    import yaml

    return yaml.safe_dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).rstrip()


def run_config_command(args, logger: logging.Logger) -> None:
    command = args.config_command
    path = _resolved_config_path(args.config)

    if command == "path":
        if path is None:
            raise SystemExit("No config file found.")
        print(path)
        return

    if path is None:
        raise SystemExit("No config file found.")

    config = load_config_file(path)

    if command == "validate":
        if args.profile:
            resolve_profile(config, args.profile)
        selected = args.profile or config.active_profile or "<none>"
        logger.info(f"Config structure is valid: {path} (profile: {selected})")
        return

    if command == "show":
        profile, _profiles = resolve_profile(config, args.profile)
        if profile is None:
            payload: dict[str, object] = config.to_dict()
        else:
            payload = {
                "schema_version": config.schema_version,
                "active_profile": config.active_profile,
                "selected_profile": profile.name,
                "profile": profile.to_dict(),
            }
        print(_dump_yaml(payload))
        return

    raise SystemExit(f"Unknown config command: {command}")
