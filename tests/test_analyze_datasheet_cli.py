import json
import sys
from pathlib import Path

from llm_logparser.cli.cli import main
from llm_logparser.core.i18n import set_locale
from llm_logparser.core.l1_derivation import build_thread_stats_artifact, derive_thread_metrics


def _write_parsed_jsonl(
    path: Path,
    conversation_id: str,
    messages: list[dict],
    *,
    provider_id: str = "openai",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "record_type": "thread",
                    "provider_id": provider_id,
                    "conversation_id": conversation_id,
                    "message_count": len(messages),
                },
                ensure_ascii=True,
            )
            + "\n"
        )
        for idx, message in enumerate(messages, start=1):
            row = {
                "record_type": "message",
                "provider_id": provider_id,
                "conversation_id": conversation_id,
                "message_id": message.get("message_id", f"m{idx}"),
                "content": {"content_type": "text", "parts": []},
            }
            if "role" in message:
                row["role"] = message["role"]
            if "ts" in message:
                row["ts"] = message["ts"]
            if "content" in message:
                row["content"] = message["content"]
            if "text" in message:
                row["text"] = message["text"]
                if "content" not in message and isinstance(message["text"], str):
                    row["content"]["parts"] = [message["text"]]
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def _run_cli(monkeypatch, capsys, argv: list[str]) -> str:
    monkeypatch.setattr(sys, "argv", argv)
    main()
    return capsys.readouterr().out


def _write_metrics_sidecars(root: Path, monkeypatch, capsys, *, locale: str) -> None:
    _run_cli(
        monkeypatch,
        capsys,
        [
            "llm-logparser",
            "--locale",
            locale,
            "analyze",
            "tokens",
            "--input",
            str(root),
            "--encoding",
            "o200k_base",
        ],
    )
    _run_cli(
        monkeypatch,
        capsys,
        [
            "llm-logparser",
            "--locale",
            locale,
            "analyze",
            "metrics",
            "--input",
            str(root),
        ],
    )


def _write_thread_stats_sidecar(parsed: Path) -> None:
    artifact = build_thread_stats_artifact(
        derive_thread_metrics(parsed),
        provider_id="openai",
    )
    parsed.with_name("thread_stats.json").write_text(
        json.dumps(artifact, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def _write_metrics_sidecar(parsed: Path) -> None:
    artifact = {
        "safety": {
            "refusal_count": 1,
            "intervention_count": 1,
        }
    }
    parsed.with_name("metrics.json").write_text(
        json.dumps(artifact, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def test_analyze_datasheet_markdown_contains_required_sections_and_rows(
    tmp_path, monkeypatch, capsys
):
    set_locale("en-US")
    root = tmp_path / "parsed"
    _write_parsed_jsonl(
        root / "a" / "thread-conv-a" / "parsed.jsonl",
        "conv-a",
        [
            {"message_id": "m1", "role": "user", "ts": 0, "text": "hello"},
            {
                "message_id": "m2",
                "role": "assistant",
                "ts": 30_000,
                "text": "```python\nprint('hi')\n```",
                "content": {"content_type": "text", "parts": ["```python", "print('hi')\n```"]},
            },
        ],
    )

    out = _run_cli(
        monkeypatch,
        capsys,
        ["llm-logparser", "--locale", "en-US", "analyze", "datasheet", "--input", str(root)],
    )

    assert "# Dataset Summary" in out
    assert "## Corpus Overview" in out
    assert "## Temporal Characteristics" in out
    assert "## Turn-Taking Characteristics" in out
    assert "## Safety Characteristics" in out
    assert "## Structural Characteristics" in out
    assert "## Notes" in out
    assert "| Total threads | 1 |" in out
    assert "| Total messages | 2 |" in out
    assert "| User messages | 1 |" in out
    assert "| Assistant messages | 1 |" in out
    assert "| Average thread duration (seconds) | 30 |" in out
    assert "| Mean user/assistant character ratio | 0.2 |" in out
    assert "| Threads with code blocks | 1 |" in out
    assert "| Code-block message ratio | 0.5 |" in out


def test_analyze_datasheet_json_contains_required_top_level_sections(
    tmp_path, monkeypatch, capsys
):
    set_locale("en-US")
    parsed = tmp_path / "thread-conv-1" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-1",
        [
            {"message_id": "m1", "role": "user", "text": "hello"},
            {"message_id": "m2", "role": "assistant", "text": "world"},
        ],
    )

    out = _run_cli(
        monkeypatch,
        capsys,
        ["llm-logparser", "--locale", "en-US", "analyze", "datasheet", "--input", str(parsed), "--json"],
    )
    payload = json.loads(out)

    assert payload["artifact_type"] == "datasheet"
    assert payload["schema_version"] == "1.0"
    assert set(payload) == {
        "artifact_type",
        "schema_version",
        "corpus_overview",
        "temporal",
        "turn_taking",
        "safety",
        "structure",
        "notes",
    }


def test_analyze_datasheet_works_when_sidecars_are_present(
    tmp_path, monkeypatch, capsys
):
    set_locale("en-US")
    parsed = tmp_path / "thread-conv-sidecars" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-sidecars",
        [
            {"message_id": "m1", "role": "user", "ts": 0, "text": "hello"},
            {"message_id": "m2", "role": "assistant", "ts": 60_000, "text": "I can't help with that request."},
        ],
    )
    _write_thread_stats_sidecar(parsed)
    _write_metrics_sidecar(parsed)

    out = _run_cli(
        monkeypatch,
        capsys,
        ["llm-logparser", "--locale", "en-US", "analyze", "datasheet", "--input", str(parsed), "--json"],
    )
    payload = json.loads(out)

    assert payload["corpus_overview"]["total_threads"] == 1
    assert payload["safety"]["threads_with_refusal"] == 1
    assert payload["safety"]["threads_with_intervention"] == 1


def test_analyze_datasheet_works_when_sidecars_are_absent(
    tmp_path, monkeypatch, capsys
):
    set_locale("en-US")
    root = tmp_path / "parsed"
    _write_parsed_jsonl(
        root / "a" / "thread-conv-a" / "parsed.jsonl",
        "conv-a",
        [
            {"message_id": "m1", "role": "user", "text": "hello"},
            {"message_id": "m2", "role": "assistant", "text": "It's important to note that you should verify the source."},
        ],
    )

    out = _run_cli(
        monkeypatch,
        capsys,
        ["llm-logparser", "--locale", "en-US", "analyze", "datasheet", "--input", str(root), "--json"],
    )
    payload = json.loads(out)

    assert payload["corpus_overview"]["total_threads"] == 1
    assert payload["safety"]["threads_with_refusal"] == 0
    assert payload["safety"]["threads_with_intervention"] == 1


def test_analyze_datasheet_output_is_deterministic(tmp_path, monkeypatch, capsys):
    set_locale("en-US")
    parsed = tmp_path / "thread-conv-deterministic" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-deterministic",
        [
            {"message_id": "m1", "role": "user", "ts": 100_000, "text": "hello"},
            {"message_id": "m2", "role": "assistant", "ts": 130_000, "text": "Be careful when sharing secrets."},
        ],
    )

    argv = [
        "llm-logparser",
        "--locale",
        "en-US",
        "analyze",
        "datasheet",
        "--input",
        str(parsed),
    ]
    first = _run_cli(monkeypatch, capsys, argv)
    second = _run_cli(monkeypatch, capsys, argv)

    assert first == second
    assert "generated at" not in first.lower()


def test_analyze_datasheet_json_is_identical_across_locales_without_sidecars(
    tmp_path, monkeypatch, capsys
):
    root = tmp_path / "parsed"
    _write_parsed_jsonl(
        root / "a" / "thread-conv-a" / "parsed.jsonl",
        "conv-a",
        [
            {"message_id": "m1", "role": "user", "text": "hello"},
            {"message_id": "m2", "role": "assistant", "text": "I can't help with that request."},
        ],
    )
    _write_parsed_jsonl(
        root / "b" / "thread-conv-b" / "parsed.jsonl",
        "conv-b",
        [
            {"message_id": "m1", "role": "user", "text": "hello"},
            {"message_id": "m2", "role": "assistant", "text": "Be careful when handling personal data."},
        ],
    )

    en_output = _run_cli(
        monkeypatch,
        capsys,
        ["llm-logparser", "--locale", "en-US", "analyze", "datasheet", "--input", str(root), "--json"],
    )
    ja_output = _run_cli(
        monkeypatch,
        capsys,
        ["llm-logparser", "--locale", "ja-JP", "analyze", "datasheet", "--input", str(root), "--json"],
    )

    assert en_output == ja_output


def test_analyze_datasheet_json_is_identical_across_locales_with_metrics_sidecars(
    tmp_path, monkeypatch, capsys
):
    root = tmp_path / "parsed"
    _write_parsed_jsonl(
        root / "a" / "thread-conv-a" / "parsed.jsonl",
        "conv-a",
        [
            {"message_id": "m1", "role": "user", "text": "hello"},
            {"message_id": "m2", "role": "assistant", "text": "I can't help with that request."},
        ],
    )
    _write_parsed_jsonl(
        root / "b" / "thread-conv-b" / "parsed.jsonl",
        "conv-b",
        [
            {"message_id": "m1", "role": "user", "text": "hello"},
            {"message_id": "m2", "role": "assistant", "text": "Be careful when handling personal data."},
        ],
    )
    _write_metrics_sidecars(root, monkeypatch, capsys, locale="en-US")

    en_output = _run_cli(
        monkeypatch,
        capsys,
        ["llm-logparser", "--locale", "en-US", "analyze", "datasheet", "--input", str(root), "--json"],
    )
    ja_output = _run_cli(
        monkeypatch,
        capsys,
        ["llm-logparser", "--locale", "ja-JP", "analyze", "datasheet", "--input", str(root), "--json"],
    )

    assert en_output == ja_output


def test_analyze_datasheet_sidecar_provenance_is_locale_independent(
    tmp_path, monkeypatch, capsys
):
    root = tmp_path / "parsed"
    _write_parsed_jsonl(
        root / "a" / "thread-conv-a" / "parsed.jsonl",
        "conv-a",
        [
            {"message_id": "m1", "role": "user", "text": "hello"},
            {"message_id": "m2", "role": "assistant", "text": "I can't help with that request."},
        ],
    )
    _write_metrics_sidecars(root, monkeypatch, capsys, locale="en-US")

    output = _run_cli(
        monkeypatch,
        capsys,
        ["llm-logparser", "--locale", "ja-JP", "analyze", "datasheet", "--input", str(root), "--json"],
    )
    payload = json.loads(output)

    assert payload["safety"] == {
        "threads_with_refusal": 1,
        "threads_with_intervention": 1,
    }


def test_analyze_datasheet_safety_counts_align_with_existing_safety_logic(
    tmp_path, monkeypatch, capsys
):
    set_locale("en-US")
    root = tmp_path / "parsed"
    _write_parsed_jsonl(
        root / "a" / "thread-conv-a" / "parsed.jsonl",
        "conv-a",
        [
            {"message_id": "m1", "role": "user", "text": "hello"},
            {"message_id": "m2", "role": "assistant", "text": "I can't help with that request."},
        ],
    )
    _write_parsed_jsonl(
        root / "b" / "thread-conv-b" / "parsed.jsonl",
        "conv-b",
        [
            {"message_id": "m1", "role": "user", "text": "hello"},
            {"message_id": "m2", "role": "assistant", "text": "Be careful when handling personal data."},
        ],
    )
    _write_parsed_jsonl(
        root / "c" / "thread-conv-c" / "parsed.jsonl",
        "conv-c",
        [
            {"message_id": "m1", "role": "user", "text": "hello"},
            {"message_id": "m2", "role": "assistant", "text": "Here is the answer."},
        ],
    )

    out = _run_cli(
        monkeypatch,
        capsys,
        ["llm-logparser", "--locale", "en-US", "analyze", "datasheet", "--input", str(root), "--json"],
    )
    payload = json.loads(out)

    assert payload["safety"] == {
        "threads_with_refusal": 1,
        "threads_with_intervention": 2,
    }


def test_analyze_datasheet_structural_metrics_are_included(
    tmp_path, monkeypatch, capsys
):
    set_locale("en-US")
    parsed = tmp_path / "thread-conv-structure" / "parsed.jsonl"
    _write_parsed_jsonl(
        parsed,
        "conv-structure",
        [
            {
                "message_id": "m1",
                "role": "assistant",
                "text": "```js\nconsole.log('hi')\n```",
                "content": {"content_type": "text", "parts": ["```js", "console.log('hi')\n```"]},
            },
            {"message_id": "m2", "role": "user", "text": "thanks"},
        ],
    )

    out = _run_cli(
        monkeypatch,
        capsys,
        ["llm-logparser", "--locale", "en-US", "analyze", "datasheet", "--input", str(parsed), "--json"],
    )
    payload = json.loads(out)

    assert payload["structure"] == {
        "avg_blocks_per_message": 1.0,
        "threads_with_code_blocks": 1,
        "code_block_message_ratio": 0.5,
    }
