from __future__ import annotations

import json
from pathlib import Path
from typing import Literal
from unittest.mock import call, patch

import pytest

from llm_logparser.cli.cli import main
from llm_logparser.core.analyzer_semantic_topic import (
    SemanticTopicError,
    render_semantic_topic,
)
from llm_logparser.core.semantic_normalization import (
    SemanticNormalizationMethod,
    SemanticNormalizationResult,
)


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
    roles: list[str],
    text: str,
) -> dict:
    message_ids = [f"{window_id}-m{index}" for index, _ in enumerate(roles, start=1)]
    message_texts = _message_texts_for_window(roles=roles, text=text)
    return {
        "record_type": "message_window",
        "schema_version": "3.0",
        "provider_id": "openai",
        "conversation_id": conversation_id,
        "window_id": window_id,
        "message_ids": message_ids,
        "char_count": sum(len(part) for part in message_texts),
        "ts_start": 1,
        "ts_end": 2,
        "window_size": len(message_ids),
        "window_stride": len(message_ids),
        "__parsed_messages": [
            {
                "record_type": "message",
                "provider_id": "openai",
                "conversation_id": conversation_id,
                "message_id": message_id,
                "role": role,
                "ts": 1 + index,
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
                    "Draft the production migration checklist\n\n"
                    "Include schema audit and rollback steps"
                ),
            ),
            _message_window_row(
                "conv-a",
                "window-0002",
                roles=["user"],
                text="Capture monitoring gates for rollout readiness",
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
                text="Review launch risk controls and deployment rollback checks",
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
                text="Plan lunch options for next week",
            ),
            _message_window_row(
                "conv-c",
                "window-0002",
                roles=["assistant"],
                text="Compare ramen shops and cafe seating",
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


def _write_japanese_topic_fixture(root: Path) -> None:
    thread_a = root / "thread-conv-ja"
    _write_jsonl(
        thread_a / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-ja",
                "window-0001",
                roles=["user"],
                text="ありがとうございます、それで大丈夫です。",
            )
        ],
    )
    _write_jsonl(
        thread_a / "window_clusters.jsonl",
        [
            _window_cluster_row(
                "conv-ja",
                "window-0001",
                cluster_id="cluster_ja_000001",
                cluster_size=1,
            )
        ],
    )


def _normalization_result(
    *,
    conversation_id: str,
    span_id: str,
    window_id: str,
    message_ids: list[str],
    raw_label: str = "implementation_decision",
    normalized_label: str | None = "decision",
    mapping_status: Literal["mapped", "needs_review", "taxonomy_gap", "unmapped"] = "mapped",
    confidence: float | None = 0.9,
) -> SemanticNormalizationResult:
    return SemanticNormalizationResult(
        conversation_id=conversation_id,
        span_id=span_id,
        window_id=window_id,
        message_ids=message_ids,
        unit_kind="representative_span",
        raw_label=raw_label,
        normalized_label=normalized_label,
        mapping_status=mapping_status,
        confidence=confidence,
        method=SemanticNormalizationMethod(
            kind="hybrid",
            model="llama3.1:latest",
        ),
    )


def test_render_semantic_topic_text_output(tmp_path, monkeypatch):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_topic_fixture(root)
    del monkeypatch

    with (
        patch("llm_logparser.core.analyzer_semantic_topic.OllamaClient") as client_cls,
        patch(
            "llm_logparser.core.analyzer_semantic_topic.normalize_representative_span",
            side_effect=[
                _normalization_result(
                    conversation_id="conv-a",
                    span_id="span-a",
                    window_id="window-0001",
                    message_ids=["window-0001-m1", "window-0001-m2"],
                ),
                _normalization_result(
                    conversation_id="conv-a",
                    span_id="span-b",
                    window_id="window-0002",
                    message_ids=["window-0002-m1"],
                    raw_label="review_request",
                    normalized_label="request",
                ),
                _normalization_result(
                    conversation_id="conv-b",
                    span_id="span-c",
                    window_id="window-0001",
                    message_ids=["window-0001-m1"],
                    raw_label="status_update",
                    normalized_label="status_update",
                ),
                _normalization_result(
                    conversation_id="conv-c",
                    span_id="span-d",
                    window_id="window-0001",
                    message_ids=["window-0001-m1"],
                    raw_label="proposal",
                    normalized_label="proposal",
                ),
                _normalization_result(
                    conversation_id="conv-c",
                    span_id="span-e",
                    window_id="window-0002",
                    message_ids=["window-0002-m1"],
                    raw_label="reflection",
                    normalized_label="reflection",
                ),
            ],
        ),
    ):
        client = client_cls.return_value
        client.generate_text.side_effect = [
            "{invalid",
            json.dumps(
                {
                    "topic_label": "Launch Readiness",
                    "summary": "The windows focus on deployment checklists, rollback safety, and rollout gates.",
                    "keywords": ["launch", "rollback", "monitoring"],
                }
            ),
            json.dumps(
                {
                    "topic_label": "Lunch Planning",
                    "summary": "The windows compare lunch choices and seating options for a future outing.",
                    "keywords": ["lunch", "ramen", "cafe"],
                }
            ),
        ]

        rendered = render_semantic_topic(
            input_root=root,
            model="llama3.1:latest",
            top_clusters=2,
        )

    assert "Cluster cluster_000001" in rendered
    assert "Cluster cluster_000002" in rendered
    assert "State: unresolved (0.50)" in rendered
    assert "Label: Launch Readiness" in rendered
    assert "Keywords: launch, rollback, monitoring" in rendered
    assert "Representative:" in rendered
    assert "[conv-a / window-0001]" in rendered
    assert "normalization: decision raw=implementation_decision 0.90" in rendered
    assert client_cls.call_args_list == [
        call(base_url="http://localhost:11434", timeout=120.0),
        call(base_url="http://localhost:11434", timeout=120.0),
        call(base_url="http://localhost:11434", timeout=120.0),
    ]
    first_call = client.generate_text.call_args_list[0]
    assert first_call.kwargs["model"] == "llama3.1:latest"
    assert first_call.kwargs["response_format"] == "json"
    assert first_call.kwargs["options"]["temperature"] == 0.0
    assert first_call.kwargs["options"]["num_predict"] == 220
    assert "Cluster size: 3" in first_call.kwargs["prompt"]


def test_render_semantic_topic_json_filters_cross_thread_clusters(tmp_path, monkeypatch):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_topic_fixture(root)

    del monkeypatch

    with patch(
        "llm_logparser.core.analyzer_semantic_topic.OllamaClient.generate_text",
        side_effect=[
            json.dumps({"raw_label": "implementation_decision", "confidence": 0.91}),
            json.dumps({"raw_label": "review_request", "confidence": 0.89}),
            json.dumps({"raw_label": "status_update", "confidence": 0.92}),
            json.dumps(
                {
                    "topic_label": "Launch Readiness",
                    "summary": "Deployment readiness and rollback planning dominate the cluster.",
                    "keywords": ["launch", "rollback", "risk"],
                }
            ),
        ],
    ):
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
    assert payload["topics"][0]["state"] == "unresolved"
    assert payload["topics"][0]["state_confidence"] == 0.5
    assert payload["topics"][0]["topic_label"] == "Launch Readiness"
    assert payload["topics"][0]["representative_spans"]
    assert payload["topics"][0]["representative_spans"][0]["span_id"]
    assert payload["topics"][0]["representative_spans"][0]["message_ids"]
    assert payload["topics"][0]["representative_spans"][0]["state"] == "done"
    assert (
        payload["topics"][0]["representative_spans"][0]["semantic_normalization"][
            "normalized_label"
        ]
        == "decision"
    )
    assert (
        payload["topics"][0]["representative_spans"][1]["semantic_normalization"][
            "normalized_label"
        ]
        == "request"
    )
    assert payload["topics"][0]["representative_windows"][0]["window_id"] == "window-0001"
    assert payload["topics"][0]["quality_signals"] == {
        "cluster_size": 3,
        "conversation_count": 2,
        "avg_intra_cluster_score": None,
        "max_intra_cluster_score": None,
        "single_window": False,
    }


def test_render_semantic_topic_requires_cluster_artifacts(tmp_path):
    root = tmp_path / "artifacts" / "output" / "openai" / "thread-conv-a"
    _write_jsonl(
        root / "message_windows.jsonl",
        [
            _message_window_row(
                "conv-a",
                "window-0001",
                roles=["user"],
                text="Standalone window without cluster artifacts",
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

    del monkeypatch

    with patch(
        "llm_logparser.core.analyzer_semantic_topic.OllamaClient.generate_text",
        return_value=json.dumps(
            {
                "topic_label": "Launch Readiness",
                "summary": "Deployment readiness and rollback planning dominate the cluster.",
                "keywords": ["launch", "rollback", "risk"],
            }
        ),
    ):
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
    assert "State: unresolved (0.50)" in output
    assert "Label: Launch Readiness" in output
    assert "Summary: Deployment readiness and rollback planning dominate the cluster." in output


def test_render_semantic_topic_json_uses_state_locale_for_span_state(tmp_path, monkeypatch):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_japanese_topic_fixture(root)

    del monkeypatch

    with patch(
        "llm_logparser.core.analyzer_semantic_topic.OllamaClient.generate_text",
        return_value=json.dumps(
            {
                "topic_label": "Japanese Closure",
                "summary": "A resolved Japanese-language confirmation span.",
                "keywords": ["japanese", "closure"],
            }
        ),
    ):
        default_payload = json.loads(
            render_semantic_topic(
                input_root=root,
                model="llama3.1:latest",
                cluster_id="cluster_ja_000001",
                json_output=True,
            )
        )
        japanese_payload = json.loads(
            render_semantic_topic(
                input_root=root,
                model="llama3.1:latest",
                cluster_id="cluster_ja_000001",
                state_locale="ja-JP",
                json_output=True,
            )
        )

    assert default_payload["topics"][0]["state"] == "unresolved"
    assert japanese_payload["topics"][0]["state"] == "done"
    assert japanese_payload["topics"][0]["representative_spans"][0]["state"] == "done"


def test_analyze_semantic_topic_cli_accepts_state_locale(tmp_path, monkeypatch, capsys):
    root = tmp_path / "artifacts" / "output" / "openai"
    _write_japanese_topic_fixture(root)

    del monkeypatch

    with patch(
        "llm_logparser.core.analyzer_semantic_topic.OllamaClient.generate_text",
        return_value=json.dumps(
            {
                "topic_label": "Japanese Closure",
                "summary": "A resolved Japanese-language confirmation span.",
                "keywords": ["japanese", "closure"],
            }
        ),
    ):
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
                "cluster_ja_000001",
                "--state-locale",
                "ja-JP",
            ]
        )

    output = capsys.readouterr().out
    assert "State: done" in output
