from __future__ import annotations

import os
from pathlib import Path

from llm_logparser.cli.config_model import AppConfig
from llm_logparser.core.i18n import _

CONFIG_ENV_VAR = "LLM_LOGPARSER_CONFIG"


def _load_yaml_mapping(path: Path) -> dict[str, object]:
    try:
        import yaml
    except ImportError as e:
        raise SystemExit(_("runtime.config.yaml_required")) from e

    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise SystemExit(_("runtime.config.load_failed", path=path, detail=e)) from e

    if not isinstance(loaded, dict):
        raise SystemExit(_("runtime.config.mapping_required", path=path))
    return loaded


def resolve_explicit_config_path(path: Path, *, missing_hint: str = "") -> Path:
    target = path.expanduser()
    if not target.exists():
        hint = f" ({missing_hint})" if missing_hint else ""
        raise SystemExit(_("runtime.config.file_not_found", path=target, hint=hint))
    if target.is_dir():
        raise SystemExit(_("runtime.config.path_must_be_file", path=target))
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
                _("runtime.config.env_missing", env_var=CONFIG_ENV_VAR, path=env_path)
            )
        if env_path.is_dir():
            raise SystemExit(
                _("runtime.config.env_must_be_file", env_var=CONFIG_ENV_VAR, path=env_path)
            )
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
        path = resolve_explicit_config_path(
            explicit_path,
            missing_hint=_("runtime.config.hint_passed_via_config"),
        )
        return load_config_file(path), path

    discovered = discover_config_path(cwd=cwd)
    if discovered is None:
        return None, None

    return load_config_file(discovered), discovered
