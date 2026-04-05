from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_logparser.cli.cli import main
from llm_logparser.core.analyzer_semantic_topic_explore import (
    SemanticTopicExploreError,
    build_semantic_topic_explore_payload,
    render_semantic_topic_explore,
)
from llm_logparser.core.analyzer_semantic_topics import write_semantic_topics_artifacts


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.name == "message_windows.jsonl":
        parsed_rows: list[dict] = []
        seen_message_ids: set[str] = set()
        clean_rows: list[dict] = []
        for row in rows:
            clean_rows.append({key: value for key, value in row.items() if not key.startswith("__")})
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


def _span_ref(
    conversation_id: str,
    span_id: str,
    message_ids: list[str],
    window_id: str,
    *,
    state: str,
    state_confidence: float,
    state_signals: list[str],
) -> dict:
    return {
        "conversation_id": conversation_id,
        "span_id": span_id,
        "message_ids": message_ids,
        "window_id": window_id,
        "state": state,
        "state_confidence": state_confidence,
        "state_signals": state_signals,
    }


def _representative_span(
    conversation_id: str,
    span_id: str,
    message_ids: list[str],
    window_id: str,
    excerpt: str,
    *,
    state: str,
    state_confidence: float,
    state_signals: list[str],
) -> dict:
    return {
        **_span_ref(
            conversation_id,
            span_id,
            message_ids,
            window_id,
            state=state,
            state_confidence=state_confidence,
            state_signals=state_signals,
        ),
        "excerpt": excerpt,
    }


def _write_explore_fixture(root: Path) -> None:
    thread_a = root / "thread-conv-a"
    thread_b = root / "thread-conv-b"
    thread_c = root / "thread-conv-c"

    _write_jsonl(
        thread_a / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-a",
                "window-0001",
                message_ids=["a-1", "a-2"],
                roles=["user", "assistant"],
                text=(
                    "Draft the production migration checklist\n\n"
                    "Include schema audit and rollback steps"
                ),
                ts_start=100,
                ts_end=120,
            ),
            _message_window_row(
                "conv-a",
                "window-0002",
                message_ids=["a-3"],
                roles=["user"],
                text="Capture monitoring gates for rollout readiness",
                ts_start=130,
                ts_end=140,
            ),
        ],
    )
    _write_jsonl(
        thread_b / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-b",
                "window-0001",
                message_ids=["b-1", "b-2"],
                roles=["assistant", "user"],
                text=(
                    "Review launch risk controls\n\n"
                    "Add deployment rollback checks"
                ),
                ts_start=150,
                ts_end=170,
            )
        ],
    )
    _write_jsonl(
        thread_c / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-c",
                "window-0001",
                message_ids=["c-1"],
                roles=["user"],
                text="Plan lunch options for next week",
                ts_start=200,
                ts_end=205,
            ),
            _message_window_row(
                "conv-c",
                "window-0002",
                message_ids=["c-2"],
                roles=["assistant"],
                text="Compare ramen shops and cafe seating",
                ts_start=206,
                ts_end=210,
            ),
        ],
    )

    _write_jsonl(
        thread_a / "window_clusters.jsonl",
        [
            _window_cluster_row(
                "conv-a",
                "window-0001",
                cluster_id="cluster_000001",
                cluster_size=3,
            ),
            _window_cluster_row(
                "conv-a",
                "window-0002",
                cluster_id="cluster_000001",
                cluster_size=3,
            ),
        ],
    )
    _write_jsonl(
        thread_b / "window_clusters.jsonl",
        [
            _window_cluster_row(
                "conv-b",
                "window-0001",
                cluster_id="cluster_000001",
                cluster_size=3,
            )
        ],
    )
    _write_jsonl(
        thread_c / "window_clusters.jsonl",
        [
            _window_cluster_row(
                "conv-c",
                "window-0001",
                cluster_id="cluster_000002",
                cluster_size=2,
            ),
            _window_cluster_row(
                "conv-c",
                "window-0002",
                cluster_id="cluster_000002",
                cluster_size=2,
            ),
        ],
    )


def _write_manual_topic_artifacts(root: Path) -> None:
    _write_jsonl(
        root / "thread-conv-a" / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-a",
                "window-0001",
                message_ids=["a-1"],
                roles=["user"],
                text="alpha rollout checklist",
                ts_start=100,
                ts_end=101,
            )
        ],
    )
    _write_jsonl(
        root / "thread-conv-b" / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-b",
                "window-0001",
                message_ids=["b-1"],
                roles=["assistant"],
                text="beta launch notes",
                ts_start=110,
                ts_end=111,
            )
        ],
    )
    _write_jsonl(
        root / "thread-conv-c" / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-c",
                "window-0001",
                message_ids=["c-1"],
                roles=["user"],
                text="gamma lunch planning",
                ts_start=120,
                ts_end=121,
            )
        ],
    )

    topics_dir = root / "l3" / "semantic-topics"
    topics_dir.mkdir(parents=True, exist_ok=True)
    (topics_dir / "topics.json").write_text(
        json.dumps(
            {
                "artifact_type": "semantic_topics",
                "schema_version": "2.1",
                "provider_id": "openai",
                "topic_count": 3,
                "generated_at": "2026-03-28T00:00:00Z",
                "source_inputs": [
                    "message_windows.jsonl",
                    "window_clusters.jsonl",
                ],
                "provenance": {
                    "pipeline_version": "test",
                    "membership_mode": "span-and-message-v2",
                    "label_mode": "structural-only",
                    "embedding_model": None,
                    "labeling_model": None,
                    "prompt_hash": None,
                    "prompt_variant": None,
                    "window_cap": 8,
                    "max_window_chars": 300,
                    "clustering": {
                        "method": "connected-components",
                        "edge_policy": "mutual-only",
                        "neighbor_k": None,
                        "score_threshold_policy": "test",
                    },
                    "filters": {
                        "cluster_id": None,
                        "min_cluster_size": 1,
                        "cross_thread_only": False,
                    },
                },
                "topics": [
                    {
                        "topic_id": "topic-zeta",
                        "provider_id": "openai",
                        "label": "Zeta",
                        "summary": "Large but low-score topic.",
                        "keywords": [],
                        "confidence": None,
                        "state": "unresolved",
                        "state_confidence": 0.61,
                        "cluster_ids": ["cluster-zeta"],
                        "conversation_ids": ["conv-a"],
                        "span_refs": [
                            _span_ref(
                                "conv-a",
                                "span-a1",
                                ["a-1"],
                                "window-0001",
                                state="unresolved",
                                state_confidence=0.61,
                                state_signals=["C1:ends_with_user", "D2:stale"],
                            )
                        ],
                        "window_refs": [{"conversation_id": "conv-a", "window_id": "window-0001"}],
                        "message_refs": [{"conversation_id": "conv-a", "message_id": "a-1"}],
                        "cluster_count": 1,
                        "span_count": 3,
                        "window_count": 3,
                        "message_count": 1,
                        "quality_signals": {
                            "cluster_size": 3,
                            "conversation_count": 1,
                            "avg_intra_cluster_score": 0.40,
                            "max_intra_cluster_score": 0.60,
                            "single_window": False,
                        },
                        "first_seen": 100,
                        "last_seen": 101,
                        "representative_spans": [
                            _representative_span(
                                "conv-a",
                                "span-a1",
                                ["a-1"],
                                "window-0001",
                                "alpha rollout checklist",
                                state="unresolved",
                                state_confidence=0.61,
                                state_signals=["C1:ends_with_user", "D2:stale"],
                            )
                        ],
                        "representative_windows": [
                            {
                                "conversation_id": "conv-a",
                                "window_id": "window-0001",
                                "excerpt": "alpha rollout checklist",
                            }
                        ],
                    },
                    {
                        "topic_id": "topic-alpha",
                        "provider_id": "openai",
                        "label": "Alpha",
                        "summary": "Higher-score tied topic.",
                        "keywords": [],
                        "confidence": None,
                        "state": "in_progress",
                        "state_confidence": 0.82,
                        "cluster_ids": ["cluster-alpha"],
                        "conversation_ids": ["conv-a", "conv-b"],
                        "span_refs": [
                            _span_ref(
                                "conv-a",
                                "span-a1",
                                ["a-1"],
                                "window-0001",
                                state="unresolved",
                                state_confidence=0.61,
                                state_signals=["C1:ends_with_user", "D2:stale"],
                            ),
                            _span_ref(
                                "conv-b",
                                "span-b1",
                                ["b-1"],
                                "window-0001",
                                state="in_progress",
                                state_confidence=0.82,
                                state_signals=["B4:explicit_next_step", "D1:recent_activity"],
                            ),
                        ],
                        "window_refs": [
                            {"conversation_id": "conv-a", "window_id": "window-0001"},
                            {"conversation_id": "conv-b", "window_id": "window-0001"},
                        ],
                        "message_refs": [
                            {"conversation_id": "conv-a", "message_id": "a-1"},
                            {"conversation_id": "conv-b", "message_id": "b-1"},
                        ],
                        "cluster_count": 1,
                        "span_count": 2,
                        "window_count": 2,
                        "message_count": 2,
                        "quality_signals": {
                            "cluster_size": 2,
                            "conversation_count": 2,
                            "avg_intra_cluster_score": 0.90,
                            "max_intra_cluster_score": 0.92,
                            "single_window": False,
                        },
                        "first_seen": 100,
                        "last_seen": 111,
                        "representative_spans": [
                            _representative_span(
                                "conv-b",
                                "span-b1",
                                ["b-1"],
                                "window-0001",
                                "beta launch notes",
                                state="in_progress",
                                state_confidence=0.82,
                                state_signals=["B4:explicit_next_step", "D1:recent_activity"],
                            )
                        ],
                        "representative_windows": [
                            {
                                "conversation_id": "conv-b",
                                "window_id": "window-0001",
                                "excerpt": "beta launch notes",
                            }
                        ],
                    },
                    {
                        "topic_id": "topic-beta",
                        "provider_id": "openai",
                        "label": "Beta",
                        "summary": "Null-score tied topic.",
                        "keywords": [],
                        "confidence": None,
                        "state": "done",
                        "state_confidence": 0.9,
                        "cluster_ids": ["cluster-beta"],
                        "conversation_ids": ["conv-b", "conv-c"],
                        "span_refs": [
                            _span_ref(
                                "conv-b",
                                "span-b1",
                                ["b-1"],
                                "window-0001",
                                state="done",
                                state_confidence=0.9,
                                state_signals=["A2:task_completion_statement"],
                            ),
                            _span_ref(
                                "conv-c",
                                "span-c1",
                                ["c-1"],
                                "window-0001",
                                state="done",
                                state_confidence=0.9,
                                state_signals=["A2:task_completion_statement"],
                            ),
                        ],
                        "window_refs": [
                            {"conversation_id": "conv-b", "window_id": "window-0001"},
                            {"conversation_id": "conv-c", "window_id": "window-0001"},
                        ],
                        "message_refs": [
                            {"conversation_id": "conv-b", "message_id": "b-1"},
                            {"conversation_id": "conv-c", "message_id": "c-1"},
                        ],
                        "cluster_count": 1,
                        "span_count": 2,
                        "window_count": 2,
                        "message_count": 2,
                        "quality_signals": {
                            "cluster_size": 2,
                            "conversation_count": 2,
                            "avg_intra_cluster_score": None,
                            "max_intra_cluster_score": None,
                            "single_window": False,
                        },
                        "first_seen": 110,
                        "last_seen": 121,
                        "representative_spans": [
                            _representative_span(
                                "conv-c",
                                "span-c1",
                                ["c-1"],
                                "window-0001",
                                "gamma lunch planning",
                                state="done",
                                state_confidence=0.9,
                                state_signals=["A2:task_completion_statement"],
                            )
                        ],
                        "representative_windows": [
                            {
                                "conversation_id": "conv-c",
                                "window_id": "window-0001",
                                "excerpt": "gamma lunch planning",
                            }
                        ],
                    },
                    {
                        "topic_id": "topic-delta",
                        "provider_id": "openai",
                        "label": "Delta",
                        "summary": "Singleton topic.",
                        "keywords": [],
                        "confidence": None,
                        "state": "unresolved",
                        "state_confidence": 0.5,
                        "cluster_ids": ["cluster-delta"],
                        "conversation_ids": ["conv-c"],
                        "span_refs": [
                            _span_ref(
                                "conv-c",
                                "span-c1",
                                ["c-1"],
                                "window-0001",
                                state="unresolved",
                                state_confidence=0.5,
                                state_signals=["C1:ends_with_user"],
                            )
                        ],
                        "window_refs": [
                            {"conversation_id": "conv-c", "window_id": "window-0001"},
                        ],
                        "message_refs": [
                            {"conversation_id": "conv-c", "message_id": "c-1"},
                        ],
                        "cluster_count": 1,
                        "span_count": 1,
                        "window_count": 1,
                        "message_count": 1,
                        "quality_signals": {
                            "cluster_size": 1,
                            "conversation_count": 1,
                            "avg_intra_cluster_score": None,
                            "max_intra_cluster_score": None,
                            "single_window": True,
                        },
                        "first_seen": 120,
                        "last_seen": 121,
                        "representative_spans": [
                            _representative_span(
                                "conv-c",
                                "span-c1",
                                ["c-1"],
                                "window-0001",
                                "gamma lunch planning",
                                state="unresolved",
                                state_confidence=0.5,
                                state_signals=["C1:ends_with_user"],
                            )
                        ],
                        "representative_windows": [
                            {
                                "conversation_id": "conv-c",
                                "window_id": "window-0001",
                                "excerpt": "gamma lunch planning",
                            }
                        ],
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_jsonl(
        topics_dir / "topic_membership.jsonl",
        [
            {
                "record_type": "topic_membership",
                "schema_version": "1.0",
                "provider_id": "openai",
                "topic_id": "topic-zeta",
                "membership_type": "message",
                "conversation_id": "conv-a",
                "cluster_id": "cluster-zeta",
                "span_id": "span-a1",
                "window_id": "window-0001",
                "message_id": "a-1",
            },
            {
                "record_type": "topic_membership",
                "schema_version": "1.0",
                "provider_id": "openai",
                "topic_id": "topic-alpha",
                "membership_type": "message",
                "conversation_id": "conv-a",
                "cluster_id": "cluster-alpha",
                "span_id": "span-a1",
                "window_id": "window-0001",
                "message_id": "a-1",
            },
            {
                "record_type": "topic_membership",
                "schema_version": "1.0",
                "provider_id": "openai",
                "topic_id": "topic-alpha",
                "membership_type": "message",
                "conversation_id": "conv-b",
                "cluster_id": "cluster-alpha",
                "span_id": "span-b1",
                "window_id": "window-0001",
                "message_id": "b-1",
            },
            {
                "record_type": "topic_membership",
                "schema_version": "1.0",
                "provider_id": "openai",
                "topic_id": "topic-beta",
                "membership_type": "message",
                "conversation_id": "conv-b",
                "cluster_id": "cluster-beta",
                "span_id": "span-b1",
                "window_id": "window-0001",
                "message_id": "b-1",
            },
            {
                "record_type": "topic_membership",
                "schema_version": "1.0",
                "provider_id": "openai",
                "topic_id": "topic-beta",
                "membership_type": "message",
                "conversation_id": "conv-c",
                "cluster_id": "cluster-beta",
                "span_id": "span-c1",
                "window_id": "window-0001",
                "message_id": "c-1",
            },
            {
                "record_type": "topic_membership",
                "schema_version": "1.0",
                "provider_id": "openai",
                "topic_id": "topic-delta",
                "membership_type": "message",
                "conversation_id": "conv-c",
                "cluster_id": "cluster-delta",
                "span_id": "span-c1",
                "window_id": "window-0001",
                "message_id": "c-1",
            },
        ],
    )


def _build_topic_artifacts(root: Path) -> dict:
    result = write_semantic_topics_artifacts(root)
    return json.loads(Path(result["topics_path"]).read_text(encoding="utf-8"))


def _rewrite_parsed_message_text(
    root: Path,
    *,
    conversation_id: str,
    message_id: str,
    text: str | None,
) -> None:
    for parsed_path in sorted(root.rglob("parsed.jsonl")):
        rows = [
            json.loads(line)
            for line in parsed_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        updated = False
        for row in rows:
            if (
                row.get("record_type") == "message"
                and row.get("conversation_id") == conversation_id
                and row.get("message_id") == message_id
            ):
                row["text"] = text
                updated = True
        if not updated:
            continue
        parsed_path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=True) for row in rows) + "\n",
            encoding="utf-8",
        )
        message_text_by_id = {
            row["message_id"]: (
                row.get("text") if isinstance(row.get("text"), str) else ""
            )
            for row in rows
            if row.get("record_type") == "message"
            and row.get("conversation_id") == conversation_id
            and isinstance(row.get("message_id"), str)
        }
        windows_path = parsed_path.with_name("message_windows.jsonl")
        if not windows_path.exists():
            continue
        window_rows = [
            json.loads(line)
            for line in windows_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        for row in window_rows:
            if row.get("conversation_id") != conversation_id:
                continue
            message_ids = row.get("message_ids", [])
            if not isinstance(message_ids, list):
                continue
            if message_id not in message_ids:
                continue
            row["char_count"] = sum(
                len(message_text_by_id.get(item, ""))
                for item in message_ids
                if isinstance(item, str)
            )
        windows_path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=True) for row in window_rows) + "\n",
            encoding="utf-8",
        )


def _write_incompatible_topics_artifact(root: Path, *, schema_version: str = "1.0") -> None:
    topics_dir = root / "l3" / "semantic-topics"
    topics_dir.mkdir(parents=True, exist_ok=True)
    (topics_dir / "topics.json").write_text(
        json.dumps(
            {
                "artifact_type": "semantic_topics",
                "schema_version": schema_version,
                "provider_id": "openai",
                "topic_count": 1,
                "generated_at": "2026-03-28T00:00:00Z",
                "source_inputs": ["message_windows.jsonl", "window_clusters.jsonl"],
                "provenance": {
                    "pipeline_version": "test",
                    "membership_mode": "cluster-is-topic-v1",
                    "label_mode": "structural-only",
                    "embedding_model": None,
                    "labeling_model": None,
                    "prompt_hash": None,
                    "prompt_variant": None,
                    "window_cap": 8,
                    "max_window_chars": 300,
                    "clustering": {
                        "method": "connected-components",
                        "edge_policy": "mutual-only",
                        "neighbor_k": None,
                        "score_threshold_policy": "test",
                    },
                    "filters": {
                        "cluster_id": None,
                        "min_cluster_size": 1,
                        "cross_thread_only": False,
                    },
                },
                "topics": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_incompatible_topic_membership_artifact(root: Path, *, schema_version: str = "0.1") -> None:
    topics_dir = root / "l3" / "semantic-topics"
    topics_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(
        topics_dir / "topic_membership.jsonl",
        [
            {
                "record_type": "topic_membership",
                "schema_version": schema_version,
                "provider_id": "openai",
                "topic_id": "topic-old",
                "membership_type": "window",
                "conversation_id": "conv-a",
                "cluster_id": "cluster-old",
                "window_id": "window-0001",
                "message_id": None,
            }
        ],
    )


def test_semantic_topic_explore_topic_list_output(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_explore_fixture(root)
    topics_payload = _build_topic_artifacts(root)

    rendered = render_semantic_topic_explore(input_root=root)

    first_topic_id = topics_payload["topics"][0]["topic_id"]
    first_span_id = topics_payload["topics"][0]["representative_spans"][0]["span_id"]
    assert first_topic_id in rendered
    assert "state: unresolved (0.50)" in rendered
    assert "stats: clusters=1 windows=3 messages=5 conversations=2" in rendered
    assert "avg_intra_cluster_score=?" in rendered
    assert "range=100 -> 170" in rendered
    assert "(unlabeled)" in rendered
    assert f"preview: [conv-a / {first_span_id} (window-0001)]" in rendered


def test_semantic_topic_explore_topic_list_empty_preview_uses_placeholder(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_manual_topic_artifacts(root)

    topics_path = root / "l3" / "semantic-topics" / "topics.json"
    payload = json.loads(topics_path.read_text(encoding="utf-8"))
    payload["topics"][0]["representative_spans"][0]["excerpt"] = "   "
    topics_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    rendered = render_semantic_topic_explore(input_root=root)

    assert (
        "preview: [conv-a / span-a1 (window-0001)] (no displayable preview)"
        in rendered
    )
    assert 'preview: [conv-a / span-a1 (window-0001)] ""' not in rendered


def test_semantic_topic_explore_topic_list_non_empty_preview_is_unchanged(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_manual_topic_artifacts(root)

    rendered = render_semantic_topic_explore(input_root=root)

    assert 'preview: [conv-a / span-a1 (window-0001)] "alpha rollout checklist"' in rendered


def test_semantic_topic_explore_topic_detail_view(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_explore_fixture(root)
    topics_payload = _build_topic_artifacts(root)
    topic_id = next(
        topic["topic_id"]
        for topic in topics_payload["topics"]
        if topic["cluster_ids"] == ["cluster_000001"]
    )

    rendered = render_semantic_topic_explore(
        input_root=root,
        topic_id=topic_id,
    )

    representative_span_id = topics_payload["topics"][0]["representative_spans"][0]["span_id"]
    timeline_span_ids = [
        row["span_id"]
        for row in topics_payload["topics"][0]["representative_spans"]
    ]
    assert f"Topic {topic_id}" in rendered
    assert "Summary: (none)" in rendered
    assert "State: unresolved (0.50)" in rendered
    assert "Stats: clusters=1 windows=3 messages=5" in rendered
    assert "Quality: windows=3 conversations=2 avg_intra_cluster_score=?" in rendered
    assert "Conversations: conv-a, conv-b" in rendered
    assert "Representative:" in rendered
    assert (
        f'- [conv-a / {representative_span_id} (window-0001)] '
        '"Draft the production migration checklist Include schema audit and rollback steps"'
    ) in rendered
    assert "Timeline:" in rendered
    assert f"- 100 | conv-a / {timeline_span_ids[0]} (window-0001)" in rendered
    assert (
        f'- 150 | conv-b / {timeline_span_ids[1]} (window-0001) '
        '| "Review launch risk controls Add deployment rollback checks"'
    ) in rendered


def test_semantic_topic_explore_view_reconstructs_representative_messages_from_parsed_jsonl(
    tmp_path,
):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_explore_fixture(root)
    topics_payload = _build_topic_artifacts(root)
    topic_id = next(
        topic["topic_id"]
        for topic in topics_payload["topics"]
        if topic["cluster_ids"] == ["cluster_000001"]
    )

    topics_path = root / "l3" / "semantic-topics" / "topics.json"
    payload = json.loads(topics_path.read_text(encoding="utf-8"))
    for topic in payload["topics"]:
        if topic["topic_id"] != topic_id:
            continue
        topic["representative_spans"][0]["excerpt"] = "WRONG EXCERPT"
    topics_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    rendered = render_semantic_topic_explore(
        input_root=root,
        topic_id=topic_id,
        view=True,
    )

    assert f"topic_id: {topic_id}" in rendered
    assert "[USER] Draft the production migration checklist" in rendered
    assert "[ASSISTANT] Include schema audit and rollback steps" in rendered
    assert "WRONG EXCERPT" not in rendered


def test_semantic_topic_explore_view_full_messages_shows_full_ordered_sequence(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_explore_fixture(root)
    topics_payload = _build_topic_artifacts(root)
    topic_id = next(
        topic["topic_id"]
        for topic in topics_payload["topics"]
        if topic["cluster_ids"] == ["cluster_000001"]
    )

    rendered = render_semantic_topic_explore(
        input_root=root,
        topic_id=topic_id,
        view=True,
        full_messages=True,
    )

    user_a = rendered.index("[USER] Draft the production migration checklist")
    assistant_a = rendered.index("[ASSISTANT] Include schema audit and rollback steps")
    user_b = rendered.index("[USER] Capture monitoring gates for rollout readiness")
    assistant_b = rendered.index("[ASSISTANT] Review launch risk controls")
    user_c = rendered.index("[USER] Add deployment rollback checks")

    assert "== full messages ==" in rendered
    assert user_a < assistant_a < user_b < assistant_b < user_c


def test_semantic_topic_explore_view_requires_topic_id(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_explore_fixture(root)
    _build_topic_artifacts(root)

    with pytest.raises(
        SemanticTopicExploreError,
        match="--view currently requires --topic-id",
    ):
        render_semantic_topic_explore(
            input_root=root,
            view=True,
        )


def test_semantic_topic_explore_view_skips_empty_canonical_messages(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_explore_fixture(root)
    topics_payload = _build_topic_artifacts(root)
    topic_id = next(
        topic["topic_id"]
        for topic in topics_payload["topics"]
        if topic["cluster_ids"] == ["cluster_000001"]
    )

    _rewrite_parsed_message_text(
        root,
        conversation_id="conv-a",
        message_id="a-1",
        text="   ",
    )

    rendered = render_semantic_topic_explore(
        input_root=root,
        topic_id=topic_id,
        view=True,
    )

    assert "[USER] Draft the production migration checklist" not in rendered
    assert "[ASSISTANT] Include schema audit and rollback steps" in rendered
    assert "[USER] Add deployment rollback checks" in rendered


def test_semantic_topic_explore_view_all_empty_span_shows_placeholder(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_explore_fixture(root)
    topics_payload = _build_topic_artifacts(root)
    topic_id = next(
        topic["topic_id"]
        for topic in topics_payload["topics"]
        if topic["cluster_ids"] == ["cluster_000001"]
    )

    _rewrite_parsed_message_text(
        root,
        conversation_id="conv-a",
        message_id="a-1",
        text="",
    )
    _rewrite_parsed_message_text(
        root,
        conversation_id="conv-a",
        message_id="a-2",
        text=None,
    )

    rendered = render_semantic_topic_explore(
        input_root=root,
        topic_id=topic_id,
        view=True,
    )

    assert "(no displayable messages)" in rendered
    assert "[USER] Draft the production migration checklist" not in rendered
    assert "[ASSISTANT] Include schema audit and rollback steps" not in rendered
    assert "[ASSISTANT] Review launch risk controls" in rendered


def test_semantic_topic_explore_full_messages_all_empty_shows_placeholder(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_explore_fixture(root)
    topics_payload = _build_topic_artifacts(root)
    topic_id = next(
        topic["topic_id"]
        for topic in topics_payload["topics"]
        if topic["cluster_ids"] == ["cluster_000001"]
    )

    for conversation_id, message_id in [
        ("conv-a", "a-1"),
        ("conv-a", "a-2"),
        ("conv-a", "a-3"),
        ("conv-b", "b-1"),
        ("conv-b", "b-2"),
    ]:
        _rewrite_parsed_message_text(
            root,
            conversation_id=conversation_id,
            message_id=message_id,
            text=" " if message_id.endswith("1") else "",
        )

    rendered = render_semantic_topic_explore(
        input_root=root,
        topic_id=topic_id,
        view=True,
        full_messages=True,
    )

    assert "== full messages ==" in rendered
    assert "(no displayable messages)" in rendered
    assert "[USER]" not in rendered
    assert "[ASSISTANT]" not in rendered


def test_semantic_topic_explore_message_reverse_lookup(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_explore_fixture(root)
    topics_payload = _build_topic_artifacts(root)
    topic_id = next(
        topic["topic_id"]
        for topic in topics_payload["topics"]
        if topic["cluster_ids"] == ["cluster_000001"]
    )

    payload = build_semantic_topic_explore_payload(
        input_root=root,
        message_id="a-1",
    )

    assert payload["view"] == "message-lookup"
    assert payload["message_id"] == "a-1"
    assert payload["topics"] == [
        {
            "topic_id": topic_id,
            "label": None,
            "summary": None,
            "state": "unresolved",
            "state_confidence": 0.5,
        }
    ]


def test_semantic_topic_explore_conversation_grouping(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_explore_fixture(root)
    _build_topic_artifacts(root)

    payload = build_semantic_topic_explore_payload(
        input_root=root,
        conversation_id="conv-a",
    )

    assert payload["view"] == "conversation"
    assert payload["conversation_id"] == "conv-a"
    assert len(payload["topics"]) == 1
    assert payload["topics"][0]["state"] == "unresolved"
    assert payload["topics"][0]["state_confidence"] == 0.5
    assert payload["topics"][0]["message_count"] == 3
    assert payload["topics"][0]["quality_signals"] == {
        "cluster_size": 3,
        "conversation_count": 2,
        "avg_intra_cluster_score": None,
        "max_intra_cluster_score": None,
        "single_window": False,
    }
    assert payload["topics"][0]["first_seen"] == 100
    assert payload["topics"][0]["last_seen"] == 140


def test_semantic_topic_explore_json_output_correctness(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_explore_fixture(root)
    topics_payload = _build_topic_artifacts(root)
    topic_id = next(
        topic["topic_id"]
        for topic in topics_payload["topics"]
        if topic["cluster_ids"] == ["cluster_000002"]
    )

    rendered = render_semantic_topic_explore(
        input_root=root,
        topic_id=topic_id,
        json_output=True,
    )
    payload = json.loads(rendered)

    assert payload["view"] == "topic-detail"
    assert payload["topic"]["topic_id"] == topic_id
    assert payload["topic"]["state"] == "unresolved"
    assert payload["topic"]["state_confidence"] == 0.5
    assert payload["topic"]["span_refs"]
    assert payload["topic"]["span_count"] == 2
    assert payload["topic"]["representative_spans"] == [
        {
            "conversation_id": "conv-c",
            "span_id": payload["topic"]["representative_spans"][0]["span_id"],
            "message_ids": ["c-2"],
            "window_id": "window-0002",
            "excerpt": "Compare ramen shops and cafe seating",
            "state": "done",
            "state_confidence": 0.5,
            "state_signals": ["C2:ends_with_assistant", "C3:single_turn"],
        },
        {
            "conversation_id": "conv-c",
            "span_id": payload["topic"]["representative_spans"][1]["span_id"],
            "message_ids": ["c-1"],
            "window_id": "window-0001",
            "excerpt": "Plan lunch options for next week",
            "state": "unresolved",
            "state_confidence": 0.5,
            "state_signals": ["C1:ends_with_user", "C3:single_turn"],
        },
    ]
    assert payload["topic"]["message_count"] == 2
    assert payload["topic"]["representative_windows"] == [
        {
            "conversation_id": "conv-c",
            "window_id": "window-0002",
            "excerpt": "Compare ramen shops and cafe seating",
        },
        {
            "conversation_id": "conv-c",
            "window_id": "window-0001",
            "excerpt": "Plan lunch options for next week",
        },
    ]
    assert payload["topic"]["quality_signals"] == {
        "cluster_size": 2,
        "conversation_count": 1,
        "avg_intra_cluster_score": None,
        "max_intra_cluster_score": None,
        "single_window": False,
    }
    assert len(payload["topic"]["timeline"]) == 2
    assert all("span_id" in row for row in payload["topic"]["timeline"])


def test_semantic_topic_explore_topic_list_refined_ordering_and_null_score_handling(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_manual_topic_artifacts(root)

    payload = build_semantic_topic_explore_payload(input_root=root)

    assert [row["topic_id"] for row in payload["topics"]] == [
        "topic-zeta",
        "topic-alpha",
        "topic-beta",
        "topic-delta",
    ]
    assert payload["topics"][0]["state"] == "unresolved"
    assert payload["topics"][1]["state"] == "in_progress"
    assert payload["topics"][2]["state"] == "done"
    assert payload["topics"][1]["quality_signals"]["avg_intra_cluster_score"] == 0.9
    assert payload["topics"][2]["quality_signals"]["avg_intra_cluster_score"] is None


def test_semantic_topic_explore_hide_single_window_filters_browse_lists(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_manual_topic_artifacts(root)

    payload = build_semantic_topic_explore_payload(
        input_root=root,
        hide_single_window=True,
    )

    assert [row["topic_id"] for row in payload["topics"]] == [
        "topic-zeta",
        "topic-alpha",
        "topic-beta",
    ]


def test_semantic_topic_explore_min_window_count_filters_small_topics(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_manual_topic_artifacts(root)

    payload = build_semantic_topic_explore_payload(
        input_root=root,
        min_window_count=3,
    )

    assert [row["topic_id"] for row in payload["topics"]] == ["topic-zeta"]


def test_semantic_topic_explore_combined_filters_are_deterministic(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_manual_topic_artifacts(root)

    payload = build_semantic_topic_explore_payload(
        input_root=root,
        hide_single_window=True,
        min_conversation_count=2,
    )

    assert [row["topic_id"] for row in payload["topics"]] == [
        "topic-alpha",
        "topic-beta",
    ]


def test_semantic_topic_explore_json_topic_list_honors_filters(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_manual_topic_artifacts(root)

    rendered = render_semantic_topic_explore(
        input_root=root,
        hide_single_window=True,
        min_window_count=2,
        json_output=True,
    )
    payload = json.loads(rendered)

    assert payload["view"] == "topic-list"
    assert [row["topic_id"] for row in payload["topics"]] == [
        "topic-zeta",
        "topic-alpha",
        "topic-beta",
    ]
    assert all("quality_signals" in row for row in payload["topics"])


def test_semantic_topic_explore_conversation_view_honors_filters(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_manual_topic_artifacts(root)

    payload = build_semantic_topic_explore_payload(
        input_root=root,
        conversation_id="conv-c",
        hide_single_window=True,
    )

    assert payload["view"] == "conversation"
    assert [row["topic_id"] for row in payload["topics"]] == ["topic-beta"]


def test_semantic_topic_explore_direct_lookup_bypasses_browse_filters(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_manual_topic_artifacts(root)

    topic_payload = build_semantic_topic_explore_payload(
        input_root=root,
        topic_id="topic-delta",
        hide_single_window=True,
        min_window_count=3,
        min_conversation_count=2,
    )
    message_payload = build_semantic_topic_explore_payload(
        input_root=root,
        message_id="c-1",
        hide_single_window=True,
        min_window_count=3,
        min_conversation_count=2,
    )

    assert topic_payload["view"] == "topic-detail"
    assert topic_payload["topic"]["topic_id"] == "topic-delta"
    assert any(row["topic_id"] == "topic-delta" for row in message_payload["topics"])


def test_semantic_topic_explore_topic_list_rendering_surfaces_preview_and_quality(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_manual_topic_artifacts(root)

    rendered = render_semantic_topic_explore(input_root=root)

    assert "topic-zeta | Zeta" in rendered
    assert "summary: Large but low-score topic." in rendered
    assert "state: unresolved (0.61)" in rendered
    assert "avg_intra_cluster_score=0.40" in rendered
    assert 'preview: [conv-a / span-a1 (window-0001)] "alpha rollout checklist"' in rendered


def test_semantic_topic_explore_rejects_legacy_topics_contract_with_regeneration_guidance(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_jsonl(
        root / "thread-conv-a" / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-a",
                "window-0001",
                message_ids=["a-1"],
                roles=["user"],
                text="alpha rollout checklist",
                ts_start=100,
                ts_end=101,
            )
        ],
    )
    _write_incompatible_topics_artifact(root)
    _write_incompatible_topic_membership_artifact(root, schema_version="1.0")

    with pytest.raises(SemanticTopicExploreError) as exc_info:
        build_semantic_topic_explore_payload(input_root=root)

    message = str(exc_info.value)
    assert "incompatible topics.json schema_version" in message
    assert "Regenerate semantic artifacts with the current pipeline" in message


def test_semantic_topic_explore_rejects_legacy_topic_membership_contract_with_regeneration_guidance(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_jsonl(
        root / "thread-conv-a" / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-a",
                "window-0001",
                message_ids=["a-1"],
                roles=["user"],
                text="alpha rollout checklist",
                ts_start=100,
                ts_end=101,
            )
        ],
    )
    _write_manual_topic_artifacts(root)
    _write_incompatible_topic_membership_artifact(root)

    with pytest.raises(SemanticTopicExploreError) as exc_info:
        build_semantic_topic_explore_payload(input_root=root)

    message = str(exc_info.value)
    assert "incompatible topic_membership.jsonl schema_version" in message
    assert "Regenerate semantic artifacts with the current pipeline" in message


def test_analyze_semantic_topic_explore_cli_happy_path(tmp_path, capsys):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_explore_fixture(root)
    _build_topic_artifacts(root)

    main(
        [
            "--locale",
            "en-US",
            "analyze",
            "semantic-topic-explore",
            "--input",
            str(root),
            "--conversation-id",
            "conv-a",
        ]
    )

    output = capsys.readouterr().out
    assert "Conversation conv-a" in output
    assert "state=unresolved (0.50)" in output
    assert "messages=3" in output
