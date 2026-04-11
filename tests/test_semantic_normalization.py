from __future__ import annotations

import hashlib
import json
from pathlib import Path

TEST_LOCAL_MODEL = "test-local-model"

from llm_logparser.core.semantic_normalization import (
    normalize_representative_span,
    semantic_normalization_prompt_hashes,
    semantic_normalization_prompt_provenance,
)


class _FakeClient:
    def __init__(self, payloads: list[dict]) -> None:
        self._payloads = list(payloads)
        self.prompts: list[str] = []

    def generate_text(
        self,
        model: str,
        prompt: str,
        *,
        response_format: str | None = None,
        options: dict[str, object] | None = None,
    ) -> str:
        del model, response_format, options
        if not self._payloads:
            raise AssertionError("unexpected generate_text call")
        self.prompts.append(prompt)
        return json.dumps(self._payloads.pop(0))


def _normalize_with_payloads(payloads: list[dict]):
    client = _FakeClient(payloads)
    return normalize_representative_span(
        client=client,
        model=TEST_LOCAL_MODEL,
        conversation_id="conv-a",
        span_id="span-123",
        window_id="window-0001",
        message_ids=["m1", "m2"],
        text="Please update the rollout checklist and let me know what changed.",
    )


def test_semantic_normalization_result_shape_for_mapped_request():
    result = _normalize_with_payloads(
        [{"raw_label": "update_request", "confidence": 0.91}]
    )

    assert result.conversation_id == "conv-a"
    assert result.span_id == "span-123"
    assert result.window_id == "window-0001"
    assert result.message_ids == ["m1", "m2"]
    assert result.unit_kind == "representative_span"
    assert result.raw_label == "update_request"
    assert result.normalized_label == "request"
    assert result.mapping_status == "mapped"
    assert result.confidence == 0.91
    assert result.method.kind == "hybrid"
    assert result.method.model == TEST_LOCAL_MODEL
    assert result.method.mapping_version == "seed_taxonomy_v0"


def test_semantic_normalization_distinguishes_request_from_question():
    request_result = _normalize_with_payloads(
        [{"raw_label": "review_request", "confidence": 0.88}]
    )
    question_result = _normalize_with_payloads(
        [{"raw_label": "open_question", "confidence": 0.87}]
    )

    assert request_result.normalized_label == "request"
    assert question_result.normalized_label == "question"
    assert request_result.normalized_label != question_result.normalized_label


def test_semantic_normalization_never_exposes_other_as_stable_label():
    result = _normalize_with_payloads(
        [{"raw_label": "other", "confidence": 0.93}]
    )

    assert result.normalized_label is None
    assert result.mapping_status == "unmapped"
    assert result.raw_label == "other"


def test_semantic_normalization_uses_needs_review_for_low_confidence_alias_mapping():
    result = _normalize_with_payloads(
        [{"raw_label": "implementation_decision", "confidence": 0.4}]
    )

    assert result.normalized_label is None
    assert result.mapping_status == "needs_review"
    assert result.method.kind == "hybrid"


def test_semantic_normalization_uses_taxonomy_gap_for_meaningful_unmapped_label():
    result = _normalize_with_payloads(
        [
            {"raw_label": "emotional_reassurance", "confidence": 0.82},
            {
                "normalized_label": None,
                "mapping_status": "taxonomy_gap",
                "confidence": 0.73,
            },
        ]
    )

    assert result.normalized_label is None
    assert result.mapping_status == "taxonomy_gap"
    assert result.confidence == 0.73
    assert result.method.kind == "llm"


def test_semantic_normalization_prompt_hashes_are_deterministic_and_file_backed():
    provenance = semantic_normalization_prompt_provenance()
    repo_root = Path(__file__).resolve().parents[1]
    raw_prompt_path = repo_root / provenance["raw_label_prompt_path"]
    mapping_prompt_path = repo_root / provenance["mapping_prompt_path"]

    raw_bytes = raw_prompt_path.read_bytes()
    mapping_bytes = mapping_prompt_path.read_bytes()

    assert semantic_normalization_prompt_hashes() == {
        "raw_label_prompt_sha1": hashlib.sha1(raw_bytes).hexdigest(),
        "mapping_prompt_sha1": hashlib.sha1(mapping_bytes).hexdigest(),
    }
    assert provenance["prompt_set"] == "semantic_normalization_v0"


def test_semantic_normalization_uses_external_prompt_files_for_runtime_prompts():
    repo_root = Path(__file__).resolve().parents[1]
    provenance = semantic_normalization_prompt_provenance()
    raw_template = (repo_root / provenance["raw_label_prompt_path"]).read_text(
        encoding="utf-8"
    )
    mapping_template = (repo_root / provenance["mapping_prompt_path"]).read_text(
        encoding="utf-8"
    )
    text = "Please update the rollout checklist and tell me what changed."
    client = _FakeClient(
        [
            {"raw_label": "emotional_reassurance", "confidence": 0.82},
            {
                "normalized_label": None,
                "mapping_status": "taxonomy_gap",
                "confidence": 0.73,
            },
        ]
    )

    normalize_representative_span(
        client=client,
        model=TEST_LOCAL_MODEL,
        conversation_id="conv-a",
        span_id="span-123",
        window_id="window-0001",
        message_ids=["m1", "m2"],
        text=text,
    )

    assert client.prompts == [
        raw_template.format(span_text=text),
        mapping_template.format(raw_label="emotional_reassurance", span_text=text),
    ]
