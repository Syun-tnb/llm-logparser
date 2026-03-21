from __future__ import annotations

from pathlib import Path

import pytest


_CONTRACT_FILES = {
    "test_analyzer_artifact_contracts.py",
}

_INTEGRATION_FILES = {
    "test_e2e_openai_parse_export.py",
    "test_l2_sqlite_builder.py",
    "test_release_gate_v2_pipeline.py",
}

_CLI_FILES = {
    "test_entrypoint_aliases.py",
    "test_parser_builder_i18n.py",
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        filename = Path(str(item.fspath)).name

        if filename in _CONTRACT_FILES:
            item.add_marker(pytest.mark.contract)
            continue

        if filename in _INTEGRATION_FILES:
            item.add_marker(pytest.mark.integration)
            continue

        if filename in _CLI_FILES or filename.endswith("_cli.py"):
            item.add_marker(pytest.mark.cli)
            continue

        item.add_marker(pytest.mark.unit)
