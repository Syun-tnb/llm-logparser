from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_logparser.cli.cli import main
from llm_logparser.core.analyzer_semantic_topic import (
    SemanticTopicError,
    render_semantic_topic,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def _message_window_row(
    conversation_id: str,
    window_id: str,
    *,
    roles: list[str],
    text: str,
) -> dict:
    return {
        "record_type": "message_window",
        "schema_version": "1.0",
        "provider_id": "openai",
        "conversation_id": conversation_id,
        "window_id": window_id,
        "message_ids": [f"{window_id}-m{index}" for index, _ in enumerate(roles, start=1)],
        "roles": roles,
        "message_count": len(roles),
        "char_count": len(text),
        "ts_start": 1,
        "ts_end": 2,
        "text": text,
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


class _FakeHTTPResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def _write_topic_fixture(root: Path) -> None:
    thread_a = root / "thread-conv-a"
    thread_b = root / "thread-conv-b"
    thread_c = root / "thread-conv-c"

    _write_jsonl(
        thread_a / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-a",
                "window-0001",
                roles=["user", "assistant"],
                text=(
                    "user: Draft the production migration checklist\n\n"
                    "assistant: Include schema audit and rollback steps"
                ),
            ),
            _message_window_row(
                "conv-a",
                "window-0002",
                roles=["user"],
                text="user: Capture monitoring gates for rollout readiness",
            ),
        ],
    )
    _write_jsonl(
        thread_b / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-b",
                "window-0001",
                roles=["assistant"],
                text="assistant: Review launch risk controls and deployment rollback checks",
            )
        ],
    )
    _write_jsonl(
        thread_c / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-c",
                "window-0001",
                roles=["user"],
                text="user: Plan lunch options for next week",
            ),
            _message_window_row(
                "conv-c",
                "window-0002",
                roles=["assistant"],
                text="assistant: Compare ramen shops and cafe seating",
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


def test_render_semantic_topic_text_output(tmp_path, monkeypatch):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_topic_fixture(root)
    captured_requests: list[dict] = []
    queued_responses = [
        {
            "response": (
                "JSON follows:\n"
                '{"topic_label":"Launch Readiness","summary":"The windows focus on deployment '
                'checklists, rollback safety, and rollout gates.","keywords":["launch","rollback","monitoring"]}'
            )
        },
        {
            "response": json.dumps(
                {
                    "topic_label": "Lunch Planning",
                    "summary": "The windows compare lunch choices and seating options for a future outing.",
                    "keywords": ["lunch", "ramen", "cafe"],
                }
            )
        },
    ]

    def _fake_urlopen(request, timeout):
        captured_requests.append(
            {
                "url": request.full_url,
                "timeout": timeout,
                "body": json.loads(request.data.decode("utf-8")),
            }
        )
        return _FakeHTTPResponse(queued_responses.pop(0))

    monkeypatch.setattr(
        "llm_logparser.core.analyzer_semantic_topic.urllib_request.urlopen",
        _fake_urlopen,
    )

    rendered = render_semantic_topic(
        input_root=root,
        model="llama3.1:latest",
        top_clusters=2,
    )

    assert "Cluster cluster_000001" in rendered
    assert "Cluster cluster_000002" in rendered
    assert "Label: Launch Readiness" in rendered
    assert "Keywords: launch, rollback, monitoring" in rendered
    assert "Representative:" in rendered
    assert "[conv-a / window-0001]" in rendered
    assert captured_requests[0]["url"] == "http://localhost:11434/api/generate"
    assert captured_requests[0]["timeout"] == 120.0
    assert captured_requests[0]["body"]["model"] == "llama3.1:latest"
    assert captured_requests[0]["body"]["options"]["temperature"] == 0.0
    assert captured_requests[0]["body"]["options"]["num_predict"] == 220
    assert "Cluster size: 3" in captured_requests[0]["body"]["prompt"]


def test_render_semantic_topic_json_filters_cross_thread_clusters(tmp_path, monkeypatch):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_topic_fixture(root)

    monkeypatch.setattr(
        "llm_logparser.core.analyzer_semantic_topic.urllib_request.urlopen",
        lambda request, timeout: _FakeHTTPResponse(
            {
                "response": json.dumps(
                    {
                        "topic_label": "Launch Readiness",
                        "summary": "Deployment readiness and rollback planning dominate the cluster.",
                        "keywords": ["launch", "rollback", "risk"],
                    }
                )
            }
        ),
    )

    rendered = render_semantic_topic(
        input_root=root,
        model="llama3.1:latest",
        cross_thread_only=True,
        json_output=True,
    )
    payload = json.loads(rendered)

    assert payload["prompt_variant"] == "prompt_b"
    assert payload["window_cap"] == 8
    assert payload["max_window_chars"] == 300
    assert payload["cluster_count"] == 1
    assert payload["topics"][0]["cluster_id"] == "cluster_000001"
    assert payload["topics"][0]["topic_label"] == "Launch Readiness"


def test_render_semantic_topic_requires_cluster_artifacts(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai" / "thread-conv-a"
    _write_jsonl(
        root / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-a",
                "window-0001",
                roles=["user"],
                text="user: Standalone window without cluster artifacts",
            )
        ],
    )

    with pytest.raises(SemanticTopicError, match="no window_clusters.jsonl found"):
        render_semantic_topic(
            input_root=root.parent,
            model="llama3.1:latest",
        )


def test_analyze_semantic_topic_cli_happy_path(tmp_path, monkeypatch, capsys):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_topic_fixture(root)

    monkeypatch.setattr(
        "llm_logparser.core.analyzer_semantic_topic.urllib_request.urlopen",
        lambda request, timeout: _FakeHTTPResponse(
            {
                "response": json.dumps(
                    {
                        "topic_label": "Launch Readiness",
                        "summary": "Deployment readiness and rollback planning dominate the cluster.",
                        "keywords": ["launch", "rollback", "risk"],
                    }
                )
            }
        ),
    )

    main(
        [
            "--locale",
            "en-US",
            "analyze",
            "semantic-topic",
            "--input",
            str(root),
            "--model",
            "llama3.1:latest",
            "--cluster-id",
            "cluster_000001",
        ]
    )

    output = capsys.readouterr().out
    assert "Cluster cluster_000001" in output
    assert "size: 3" in output
    assert "threads: 2" in output
    assert "Label: Launch Readiness" in output
    assert "Summary: Deployment readiness and rollback planning dominate the cluster." in output
