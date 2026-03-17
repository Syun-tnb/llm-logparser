from __future__ import annotations

import os
from pathlib import Path

from llm_logparser.cli.config_model import AppConfig

CONFIG_ENV_VAR = "LLM_LOGPARSER_CONFIG"


def _load_yaml_mapping(path: Path) -> dict[str, object]:
    try:
        import yaml
    except ImportError as e:
        raise SystemExit(
            "PyYAML is required for config support. Install dependency 'PyYAML'."
        ) from e

    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise SystemExit(f"Failed to load config YAML '{path}': {e}") from e

    if not isinstance(loaded, dict):
        raise SystemExit(f"Config YAML must be a mapping at top level: {path}")
    return loaded


def resolve_explicit_config_path(path: Path, *, missing_hint: str = "") -> Path:
    target = path.expanduser()
    if not target.exists():
        hint = f" ({missing_hint})" if missing_hint else ""
        raise SystemExit(f"Config file not found: {target}{hint}")
    if target.is_dir():
        raise SystemExit(f"Config path must be a file: {target}")
    return target


def load_config_file(path: Path, *, missing_hint: str = "") -> AppConfig:
    target = resolve_explicit_config_path(path, missing_hint=missing_hint)
    return AppConfig.from_mapping(_load_yaml_mapping(target))


def discover_config_path(cwd: Path | None = None) -> Path | None:
    start = (cwd or Path.cwd()).resolve()

    from_env = os.getenv(CONFIG_ENV_VAR)
    if from_env:
        env_path = Path(from_env).expanduser()
        if not env_path.exists():
            raise SystemExit(
                f"{CONFIG_ENV_VAR} points to a missing file: {env_path}. "
                "Fix the path or unset the environment variable."
            )
        if env_path.is_dir():
            raise SystemExit(f"{CONFIG_ENV_VAR} must point to a file: {env_path}")
        return env_path

    local_cfg = start / "config.yaml"
    if local_cfg.exists() and local_cfg.is_file():
        return local_cfg

    cur = start.parent
    while True:
        candidate = cur / "config.yaml"
        if candidate.exists() and candidate.is_file():
            return candidate
        if cur == cur.parent:
            break
        cur = cur.parent

    home_cfg = Path.home() / ".config" / "llm-logparser" / "config.yaml"
    if home_cfg.exists() and home_cfg.is_file():
        return home_cfg

    return None


def load_config_with_discovery(
    explicit_path: Path | None,
    *,
    cwd: Path | None = None,
) -> tuple[AppConfig | None, Path | None]:
    if explicit_path is not None:
        path = resolve_explicit_config_path(explicit_path, missing_hint="passed via --config")
        return load_config_file(path), path

    discovered = discover_config_path(cwd=cwd)
    if discovered is None:
        return None, None

    return load_config_file(discovered), discovered
