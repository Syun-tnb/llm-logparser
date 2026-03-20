from __future__ import annotations

import sys
from pathlib import Path

from llm_logparser.core.i18n import _


def interactive_enabled(*, non_interactive: bool) -> bool:
    if non_interactive:
        return False
    return sys.stdin.isatty() and sys.stdout.isatty()


def prompt_text(label: str, *, default: str | None = None) -> str:
    while True:
        suffix = f" [{default}]" if default else ""
        value = input(f"{label}{suffix} ").strip()
        if value:
            return value
        if default is not None and default != "":
            return default
        print(_("runtime.prompt.enter_value"))


def prompt_existing_file(label: str, *, default: str | None = None) -> Path:
    while True:
        raw = prompt_text(label, default=default)
        path = Path(raw).expanduser()
        if path.exists() and path.is_file():
            return path
        print(_("runtime.prompt.file_not_found", path=path))


def prompt_choice(label: str, options: list[str], *, allow_skip: bool = False) -> str | None:
    if not options:
        return None

    print(label)
    for idx, item in enumerate(options, start=1):
        print(f"  {idx}) {item}")

    while True:
        skip_hint = _("runtime.prompt.skip_hint") if allow_skip else ""
        raw = input(
            _("runtime.prompt.select_option", count=len(options), skip_hint=skip_hint)
        ).strip()
        if raw == "" and allow_skip:
            return None
        if raw.isdigit():
            pos = int(raw)
            if 1 <= pos <= len(options):
                return options[pos - 1]
        print(_("runtime.prompt.invalid_selection"))
