from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_logparser.cli.cli import main
from llm_logparser.core.analyzer_semantic_span_proposals import (
    build_semantic_span_proposal_rows,
    write_semantic_span_proposals_artifact,
)
from llm_logparser.core.analyzer_semantic_topics import build_semantic_topics_artifact
from llm_logparser.core.schema_validation import load_semantic_span_proposal_validator

GOLD_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "semantic_span_proposals_gold.json"
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.name == "message_windows.jsonl":
        parsed_rows: list[dict] = []
        seen_message_ids: set[str] = set()
        clean_rows: list[dict] = []
        for row in rows:
            clean_rows.append(
                {key: value for key, value in row.items() if not key.startswith("__")}
            )
            for message in row.get("__parsed_messages", []):
                message_id = message["message_id"]
                if message_id in seen_message_ids:
                    continue
                seen_message_ids.add(message_id)
                parsed_rows.append(message)
        with path.open("w", encoding="utf-8") as handle:
            for row in clean_rows:
                handle.write(json.dumps(row, ensure_ascii=True) + "\n")
        if clean_rows:
            parsed_path = path.with_name("parsed.jsonl")
            thread_row = {
                "record_type": "thread",
                "provider_id": clean_rows[0]["provider_id"],
                "conversation_id": clean_rows[0]["conversation_id"],
                "message_count": len(parsed_rows),
            }
            with parsed_path.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps(thread_row, ensure_ascii=True) + "\n")
                for row in parsed_rows:
                    handle.write(json.dumps(row, ensure_ascii=True) + "\n")
        return
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped:
                rows.append(json.loads(stripped))
    return rows


def _message_texts_for_window(*, roles: list[str], text: str) -> list[str]:
    parts = text.split("\n\n") if text else [""]
    if len(parts) == len(roles):
        return parts
    if len(roles) == 1:
        return [text]
    raise ValueError("window test fixture text must align with roles")


def _message_window_row(
    conversation_id: str,
    window_id: str,
    *,
    message_ids: list[str],
    roles: list[str],
    text: str,
    ts_start: int,
    ts_end: int,
) -> dict:
    message_texts = _message_texts_for_window(roles=roles, text=text)
    return {
        "record_type": "message_window",
        "schema_version": "3.0",
        "provider_id": "openai",
        "conversation_id": conversation_id,
        "window_id": window_id,
        "message_ids": message_ids,
        "char_count": sum(len(part) for part in message_texts),
        "ts_start": ts_start,
        "ts_end": ts_end,
        "window_size": len(message_ids),
        "window_stride": len(message_ids),
        "__parsed_messages": [
            {
                "record_type": "message",
                "provider_id": "openai",
                "conversation_id": conversation_id,
                "message_id": message_id,
                "role": role,
                "ts": ts_start + index,
                "text": body,
                "content": {"content_type": "text", "parts": [body]},
            }
            for index, (message_id, role, body) in enumerate(
                zip(message_ids, roles, message_texts, strict=True)
            )
        ],
    }


def _message_window_row_from_fixture(window: dict) -> dict:
    messages = window["messages"]
    return _message_window_row(
        str(window["conversation_id"]),
        str(window["window_id"]),
        message_ids=[str(message["message_id"]) for message in messages],
        roles=[str(message["role"]) for message in messages],
        text="\n\n".join(str(message["text"]) for message in messages),
        ts_start=int(window["ts_start"]),
        ts_end=int(window["ts_start"]) + len(messages) - 1,
    )


def _window_cluster_row(
    conversation_id: str,
    window_id: str,
    *,
    cluster_id: str,
    cluster_size: int,
) -> dict:
    return {
        "record_type": "window_cluster_member",
        "schema_version": "0.1",
        "provider_id": "openai",
        "conversation_id": conversation_id,
        "window_id": window_id,
        "cluster_id": cluster_id,
        "cluster_size": cluster_size,
        "edge_policy": "mutual-only",
    }


def _load_gold_fixture() -> dict:
    return json.loads(GOLD_FIXTURE_PATH.read_text(encoding="utf-8"))


def _write_gold_scenario_fixture(root: Path, scenario: dict) -> None:
    thread = root / f"thread-{scenario['id']}"
    _write_jsonl(
        thread / "message_windows.jsonl",
        [_message_window_row_from_fixture(window) for window in scenario["windows"]],
    )
    clusters = scenario.get("clusters") or []
    if clusters:
        _write_jsonl(
            thread / "window_clusters.jsonl",
            [
                _window_cluster_row(
                    str(cluster["conversation_id"]),
                    str(cluster["window_id"]),
                    cluster_id=str(cluster["cluster_id"]),
                    cluster_size=int(cluster["cluster_size"]),
                )
                for cluster in clusters
            ],
        )


def _write_proposal_fixture(root: Path) -> None:
    split_thread = root / "thread-split"
    merge_thread = root / "thread-merge"
    keep_thread = root / "thread-keep"

    _write_jsonl(
        split_thread / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-split",
                "window-0001",
                message_ids=["s-1", "s-2", "s-3", "s-4"],
                roles=["user", "assistant", "user", "assistant"],
                text=(
                    "Draft the migration checklist\n\n"
                    "Include rollback steps\n\n"
                    "Add rollout monitoring gates\n\n"
                    "Confirm alert thresholds"
                ),
                ts_start=100,
                ts_end=104,
            )
        ],
    )

    _write_jsonl(
        merge_thread / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-merge",
                "window-0001",
                message_ids=["m-1", "m-2"],
                roles=["user", "assistant"],
                text=(
                    "Draft rollout checklist\n\n"
                    "Include rollback guardrails"
                ),
                ts_start=200,
                ts_end=202,
            ),
            _message_window_row(
                "conv-merge",
                "window-0002",
                message_ids=["m-3"],
                roles=["user"],
                text="Add rollout monitoring gates",
                ts_start=203,
                ts_end=203,
            ),
        ],
    )
    _write_jsonl(
        merge_thread / "window_clusters.jsonl",
        [
            _window_cluster_row(
                "conv-merge",
                "window-0001",
                cluster_id="cluster-merge",
                cluster_size=2,
            ),
            _window_cluster_row(
                "conv-merge",
                "window-0002",
                cluster_id="cluster-merge",
                cluster_size=2,
            ),
        ],
    )

    _write_jsonl(
        keep_thread / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-keep",
                "window-0001",
                message_ids=["k-1"],
                roles=["user"],
                text="Plan lunch options for next week",
                ts_start=300,
                ts_end=300,
            )
        ],
    )


def _write_topics_compat_fixture(root: Path) -> None:
    thread = root / "thread-topics"
    _write_jsonl(
        thread / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-topics",
                "window-0001",
                message_ids=["t-1", "t-2"],
                roles=["user", "assistant"],
                text="Draft migration checklist\n\nInclude rollback steps",
                ts_start=100,
                ts_end=101,
            ),
            _message_window_row(
                "conv-topics",
                "window-0002",
                message_ids=["t-3"],
                roles=["user"],
                text="Add monitoring gates",
                ts_start=102,
                ts_end=102,
            ),
        ],
    )
    _write_jsonl(
        thread / "window_clusters.jsonl",
        [
            _window_cluster_row(
                "conv-topics",
                "window-0001",
                cluster_id="cluster-topics",
                cluster_size=2,
            ),
            _window_cluster_row(
                "conv-topics",
                "window-0002",
                cluster_id="cluster-topics",
                cluster_size=2,
            ),
        ],
    )


def test_write_semantic_span_proposals_artifact_emits_schema_valid_rows(tmp_path):
    root = tmp_path / "artifacts" / "openai"
    _write_proposal_fixture(root)

    result = write_semantic_span_proposals_artifact(root)

    proposals_path = root / "l3" / "semantic-span-proposals" / "span_proposals.jsonl"
    summary_path = root / "l3" / "semantic-span-proposals" / "summary.json"
    assert result["proposal_count"] == 4
    assert proposals_path.exists()
    assert summary_path.exists()

    rows = _read_jsonl(proposals_path)
    validator = load_semantic_span_proposal_validator()
    assert all(not list(validator.iter_errors(row)) for row in rows)


def test_build_semantic_span_proposal_rows_emits_split_merge_and_keep(tmp_path):
    root = tmp_path / "artifacts" / "openai"
    _write_proposal_fixture(root)

    rows = build_semantic_span_proposal_rows(root)

    split_rows = [row for row in rows if row["conversation_id"] == "conv-split"]
    assert [row["proposal_kind"] for row in split_rows] == ["split", "split"]
    assert split_rows[0]["message_ids"] == ["s-1", "s-2"]
    assert split_rows[1]["message_ids"] == ["s-3", "s-4"]

    merge_rows = [row for row in rows if row["conversation_id"] == "conv-merge"]
    assert len(merge_rows) == 1
    assert merge_rows[0]["proposal_kind"] == "merge"
    assert merge_rows[0]["source_window_ids"] == ["window-0001", "window-0002"]
    assert merge_rows[0]["message_ids"] == ["m-1", "m-2", "m-3"]

    keep_rows = [row for row in rows if row["conversation_id"] == "conv-keep"]
    assert len(keep_rows) == 1
    assert keep_rows[0]["proposal_kind"] == "keep"
    assert keep_rows[0]["message_ids"] == ["k-1"]


def test_cli_analyze_semantic_span_proposals_writes_artifact(tmp_path):
    root = tmp_path / "artifacts" / "openai"
    _write_proposal_fixture(root)

    main(["analyze", "semantic-span-proposals", "--input", str(root)])

    assert (root / "l3" / "semantic-span-proposals" / "span_proposals.jsonl").exists()


def test_semantic_span_proposals_do_not_change_semantic_topics_output(tmp_path):
    root = tmp_path / "artifacts" / "openai"
    _write_topics_compat_fixture(root)

    baseline_artifact, baseline_rows = build_semantic_topics_artifact(root)
    write_semantic_span_proposals_artifact(root)
    artifact_after, rows_after = build_semantic_topics_artifact(root)

    assert artifact_after == baseline_artifact
    assert rows_after == baseline_rows


@pytest.mark.parametrize(
    ("scenario",),
    [
        (scenario,)
        for scenario in _load_gold_fixture()["scenarios"]
    ],
    ids=lambda item: str(item["id"]),
)
def test_semantic_span_proposal_gold_fixtures_lock_expected_groupings(
    tmp_path: Path,
    scenario: dict,
):
    root = tmp_path / "artifacts" / "openai"
    _write_gold_scenario_fixture(root, scenario)

    rows = build_semantic_span_proposal_rows(root)

    actual = [
        {
            "proposal_kind": row["proposal_kind"],
            "message_ids": row["message_ids"],
        }
        for row in rows
    ]

    assert actual == scenario["expected_proposals"], scenario["description"]


def test_semantic_span_proposal_gold_fixture_rows_remain_schema_valid(tmp_path):
    root = tmp_path / "artifacts" / "openai"
    fixture = _load_gold_fixture()
    for scenario in fixture["scenarios"]:
        _write_gold_scenario_fixture(root, scenario)

    rows = build_semantic_span_proposal_rows(root)

    validator = load_semantic_span_proposal_validator()
    for row in rows:
        assert not list(validator.iter_errors(row))
