from __future__ import annotations

import logging
import os
import sys
from datetime import timezone as _dt_timezone
from pathlib import Path

from zoneinfo import ZoneInfo


def setup_logger(level: str | None = None) -> logging.Logger:
    """Configure the shared CLI logger once."""
    logger = logging.getLogger("llm_logparser")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        logger.addHandler(handler)
    env_level = os.getenv("LLM_LOGPARSER_LOGLEVEL")
    raw_level = level or env_level or "INFO"
    logger.setLevel(getattr(logging, raw_level.upper(), logging.INFO))
    return logger


def validate_path(
    path: Path,
    *,
    must_exist: bool = True,
    expect_file: bool = False,
    expect_dir: bool = False,
) -> Path:
    """Validate input and output paths."""
    target = path.expanduser()
    if must_exist and not target.exists():
        raise FileNotFoundError(f"指定されたパスが存在しません: {target}")
    if expect_file and target.is_dir():
        raise IsADirectoryError(f"ファイルパスを指定してください: {target}")
    if expect_dir and not target.is_dir():
        raise NotADirectoryError(f"ディレクトリパスを指定してください: {target}")
    return target


def validate_split_option(raw: str | None) -> str | None:
    if raw is None:
        return None
    normalized = raw.strip()
    if not normalized:
        return None
    lowered = normalized.lower()
    if lowered == "auto" or lowered.startswith("size=") or lowered.startswith("count="):
        return normalized
    raise SystemExit(f"invalid --split: {raw}")


def resolve_timezone(timezone_name: str, logger: logging.Logger):
    try:
        return ZoneInfo(timezone_name)
    except Exception:
        logger.warning(f"Unknown timezone '{timezone_name}', fallback to UTC")
        return _dt_timezone.utc


def write_or_print(rendered: str, out: Path | None) -> None:
    if out:
        if out.exists() and out.is_dir():
            raise IsADirectoryError(f"ファイルパスを指定してください: {out}")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f"{rendered}\n", encoding="utf-8")
    else:
        print(rendered)
