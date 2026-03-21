import tomllib
from pathlib import Path


def test_project_scripts_expose_llp_alias():
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

    scripts = payload["project"]["scripts"]

    assert scripts["llm-logparser"] == "llm_logparser.cli.cli:main"
    assert scripts["llp"] == "llm_logparser.cli.cli:main"
