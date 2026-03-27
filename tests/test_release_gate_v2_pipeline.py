from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from llm_logparser.cli.cli import main
from llm_logparser.core.schema_validation import (
    load_metrics_validator,
    load_token_stats_validator,
)


@pytest.fixture(autouse=True)
def _isolate_from_repo_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def _run_cli(monkeypatch, capsys, argv: list[str]) -> str:
    monkeypatch.setattr(sys, "argv", argv)
    main()
    return capsys.readouterr().out


def test_release_gate_v2_pipeline(tmp_path, monkeypatch, capsys):
    fixture = Path(__file__).parent / "fixtures" / "openai_sample.json"
    artifacts_root = tmp_path / "artifacts"

    _run_cli(
        monkeypatch,
        capsys,
        [
            "llm-logparser",
            "--locale",
            "en-US",
            "parse",
            "--provider",
            "openai",
            "--input",
            str(fixture),
            "--outdir",
            str(artifacts_root),
        ],
    )

    provider_root = artifacts_root / "openai"
    parsed_files = sorted(provider_root.rglob("parsed.jsonl"))

    assert len(parsed_files) == 1
    parsed_path = parsed_files[0]
    thread_dir = parsed_path.parent
    assert (thread_dir / "thread_stats.json").exists()
    assert (thread_dir / "message_windows.jsonl").exists()

    export_path = tmp_path / "thread.md"
    _run_cli(
        monkeypatch,
        capsys,
        [
            "llm-logparser",
            "--locale",
            "en-US",
            "export",
            "--input",
            str(parsed_path),
            "--out",
            str(export_path),
            "--timezone",
            "Asia/Tokyo",
            "--formatting",
            "light",
        ],
    )

    assert export_path.exists()
    assert "おはよう" in export_path.read_text(encoding="utf-8")

    stats_out = _run_cli(
        monkeypatch,
        capsys,
        [
            "llm-logparser",
            "--locale",
            "en-US",
            "analyze",
            "stats",
            "--input",
            str(provider_root),
            "--json",
        ],
    )
    stats_payload = json.loads(stats_out)
    assert stats_payload["threads"] == 1
    assert stats_payload["messages"] > 0

    timeline_out = _run_cli(
        monkeypatch,
        capsys,
        [
            "llm-logparser",
            "--locale",
            "en-US",
            "analyze",
            "timeline",
            "--input",
            str(provider_root),
            "--json",
        ],
    )
    timeline_payload = json.loads(timeline_out)
    assert timeline_payload["bucket"] == "day"
    assert timeline_payload["timeline"]

    _run_cli(
        monkeypatch,
        capsys,
        [
            "llm-logparser",
            "--locale",
            "en-US",
            "analyze",
            "tokens",
            "--input",
            str(provider_root),
            "--encoding",
            "o200k_base",
        ],
    )
    token_stats_path = thread_dir / "token_stats.json"
    token_stats = json.loads(token_stats_path.read_text(encoding="utf-8"))
    assert token_stats_path.exists()
    assert list(load_token_stats_validator().iter_errors(token_stats)) == []

    _run_cli(
        monkeypatch,
        capsys,
        [
            "llm-logparser",
            "--locale",
            "en-US",
            "analyze",
            "metrics",
            "--input",
            str(provider_root),
        ],
    )
    metrics_path = thread_dir / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert metrics_path.exists()
    assert list(load_metrics_validator().iter_errors(metrics)) == []

    _run_cli(
        monkeypatch,
        capsys,
        [
            "llm-logparser",
            "--locale",
            "en-US",
            "analyze",
            "sqlite-build",
            "--input",
            str(artifacts_root),
            "--provider",
            "openai",
        ],
    )

    db_path = provider_root / "analysis.db"
    assert db_path.exists()
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] > 0
        assert conn.execute("SELECT COUNT(*) FROM message_windows").fetchone()[0] > 0
    finally:
        conn.close()
