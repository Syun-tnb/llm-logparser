from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .analyzer_common import write_json_artifact
from .llm_client_protocol import LLMClient
from .ollama_client import OllamaClient
from .schema_validation import (
    load_cross_thread_candidate_validator,
    load_cross_thread_intent_evaluation_validator,
)

CROSS_THREAD_INTENT_EVAL_SCHEMA_VERSION = "0.1"
CROSS_THREAD_INTENT_EVAL_RECORD_TYPE = "cross_thread_intent_evaluation"
CROSS_THREAD_INTENT_EVAL_SUMMARY_ARTIFACT_TYPE = (
    "cross_thread_intent_evaluations_summary"
)
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_TIMEOUT_SECONDS = 120.0
DEFAULT_OLLAMA_NUM_PREDICT = 280
PROMPT_VARIANT = "same_intent_v0_1"

_PROMPT_TEMPLATE = """You are evaluating whether cross-thread candidate spans express the same underlying intent, event, or task continuation as one source span.

Definitions:
- "same intent" means the target refers to the same action, event, or state change as the source, or a clear continuation of the same task or project state.
- Greeting-only text is almost always "no".
- Near-duplicate summaries are usually "yes".
- Rephrased completion or status messages are usually "yes".
- Be conservative: prefer "no" over weak "yes".
- Do not rely on wording overlap alone; focus on meaning.
- If unsure, answer "no" with "low" confidence.

Return JSON only.
Return one array of objects.
Use each target_index exactly once.
Each object must have exactly these keys:
- target_index
- same_intent
- confidence
- reason

Allowed values:
- same_intent: "yes" or "no"
- confidence: "high", "medium", or "low"

Source:
{source_block}

Targets:
{targets_block}
"""


class CrossThreadIntentEvalError(RuntimeError):
    pass


def cross_thread_intent_evaluations_path(input_root: Path) -> Path:
    return input_root / "l4" / "cross-thread-intent-eval" / "evaluations.jsonl"


def _candidate_rows_path(input_root: Path) -> Path:
    return input_root / "l3" / "cross-thread-candidates" / "candidates.jsonl"


def _load_candidate_rows(input_root: Path) -> list[dict[str, Any]]:
    path = _candidate_rows_path(input_root)
    if not path.exists():
        raise CrossThreadIntentEvalError(
            f"cross-thread candidate artifact not found: {path}"
        )

    rows: list[dict[str, Any]] = []
    validator = load_cross_thread_candidate_validator()
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise CrossThreadIntentEvalError(
                    f"invalid JSON in {path} line {line_number}: {exc.msg}"
                ) from exc
            errors = list(validator.iter_errors(row))
            if errors:
                raise CrossThreadIntentEvalError(
                    "cross-thread candidate schema validation failed for "
                    f"{path} line {line_number}: {errors[0].message}"
                )
            rows.append(row)

    if not rows:
        raise CrossThreadIntentEvalError(
            f"cross-thread candidate artifact is empty: {path}"
        )
    return rows


def _source_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row["source_conversation_id"]),
        str(row["source_topic_id"]),
        str(row["source_span_id"]),
    )


def _build_prompt(source_row: dict[str, Any], target_rows: list[dict[str, Any]]) -> str:
    source_lines = [f'excerpt: "{source_row["source_excerpt"]}"']
    source_topic_label = source_row.get("source_topic_label")
    if isinstance(source_topic_label, str) and source_topic_label:
        source_lines.append(f'topic_label: "{source_topic_label}"')

    target_blocks: list[str] = []
    for index, row in enumerate(target_rows, start=1):
        lines = [f"{index}. excerpt: \"{row['target_excerpt']}\""]
        target_topic_label = row.get("target_topic_label")
        if isinstance(target_topic_label, str) and target_topic_label:
            lines.append(f'   topic_label: "{target_topic_label}"')
        target_blocks.append("\n".join(lines))

    return _PROMPT_TEMPLATE.format(
        source_block="\n".join(source_lines),
        targets_block="\n".join(target_blocks),
    )


def _parse_response(
    *,
    response_text: str,
    target_count: int,
) -> list[dict[str, Any]]:
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise CrossThreadIntentEvalError(
            "cross-thread intent evaluation response was not valid JSON"
        ) from exc

    if not isinstance(payload, list):
        raise CrossThreadIntentEvalError(
            "cross-thread intent evaluation response must be a JSON array"
        )
    if len(payload) != target_count:
        raise CrossThreadIntentEvalError(
            "cross-thread intent evaluation response did not return one item "
            f"per target ({len(payload)} != {target_count})"
        )

    seen_indexes: set[int] = set()
    normalized: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise CrossThreadIntentEvalError(
                "cross-thread intent evaluation items must be JSON objects"
            )
        target_index = item.get("target_index")
        same_intent = item.get("same_intent")
        confidence = item.get("confidence")
        reason = item.get("reason")

        if not isinstance(target_index, int) or not (1 <= target_index <= target_count):
            raise CrossThreadIntentEvalError(
                "cross-thread intent evaluation target_index must be an integer "
                f"between 1 and {target_count}"
            )
        if target_index in seen_indexes:
            raise CrossThreadIntentEvalError(
                "cross-thread intent evaluation returned duplicate target_index values"
            )
        seen_indexes.add(target_index)

        if not isinstance(same_intent, str) or same_intent not in {"yes", "no"}:
            raise CrossThreadIntentEvalError(
                'cross-thread intent evaluation same_intent must be "yes" or "no"'
            )
        if not isinstance(confidence, str) or confidence not in {
            "high",
            "medium",
            "low",
        }:
            raise CrossThreadIntentEvalError(
                "cross-thread intent evaluation confidence must be high, medium, or low"
            )
        if not isinstance(reason, str) or not reason.strip():
            raise CrossThreadIntentEvalError(
                "cross-thread intent evaluation reason must be a non-empty string"
            )

        normalized.append(
            {
                "target_index": target_index,
                "same_intent": same_intent,
                "confidence": confidence,
                "reason": " ".join(reason.split()),
            }
        )

    normalized.sort(key=lambda item: item["target_index"])
    return normalized


def _evaluate_source_group(
    *,
    client: LLMClient,
    model: str,
    source_row: dict[str, Any],
    target_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    prompt = _build_prompt(source_row, target_rows)
    response_text = client.generate_text(
        model=model,
        prompt=prompt,
        response_format="json",
        options={
            "temperature": 0.0,
            "num_predict": DEFAULT_OLLAMA_NUM_PREDICT,
        },
    )
    return _parse_response(response_text=response_text, target_count=len(target_rows))


def _evaluation_row(
    *,
    candidate_row: dict[str, Any],
    evaluation: dict[str, Any],
) -> dict[str, Any]:
    row = {
        "record_type": CROSS_THREAD_INTENT_EVAL_RECORD_TYPE,
        "schema_version": CROSS_THREAD_INTENT_EVAL_SCHEMA_VERSION,
        "provider_id": candidate_row["provider_id"],
        "source_conversation_id": candidate_row["source_conversation_id"],
        "target_conversation_id": candidate_row["target_conversation_id"],
        "source_topic_id": candidate_row["source_topic_id"],
        "target_topic_id": candidate_row["target_topic_id"],
        "source_span_id": candidate_row["source_span_id"],
        "target_span_id": candidate_row["target_span_id"],
        "source_message_ids": list(candidate_row["source_message_ids"]),
        "target_message_ids": list(candidate_row["target_message_ids"]),
        "source_excerpt": candidate_row["source_excerpt"],
        "target_excerpt": candidate_row["target_excerpt"],
        "source_topic_label": candidate_row.get("source_topic_label"),
        "target_topic_label": candidate_row.get("target_topic_label"),
        "candidate_score": candidate_row["score"],
        "candidate_rank": candidate_row["rank"],
        "candidate_reason_codes": list(candidate_row["evidence"]["reason_codes"]),
        "same_intent": evaluation["same_intent"],
        "confidence": evaluation["confidence"],
        "reason": evaluation["reason"],
    }
    if "embedding_similarity" in candidate_row:
        row["candidate_embedding_similarity"] = candidate_row["embedding_similarity"]

    errors = list(load_cross_thread_intent_evaluation_validator().iter_errors(row))
    if errors:
        raise CrossThreadIntentEvalError(
            "cross-thread intent evaluation schema validation failed: "
            f"{errors[0].message}"
        )
    return row


def build_cross_thread_intent_evaluation_rows(
    input_root: Path,
    *,
    model: str,
    base_url: str = DEFAULT_OLLAMA_BASE_URL,
    timeout_seconds: float = DEFAULT_OLLAMA_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    if not isinstance(model, str) or not model.strip():
        raise CrossThreadIntentEvalError(
            "--model is required for cross-thread-intent-eval"
        )
    if timeout_seconds <= 0:
        raise CrossThreadIntentEvalError("--timeout-seconds must be > 0")

    rows = _load_candidate_rows(input_root)
    rows.sort(
        key=lambda row: (
            row["source_conversation_id"],
            row["source_topic_id"],
            row["source_span_id"],
            row["rank"],
            row["target_conversation_id"],
            row["target_topic_id"],
            row["target_span_id"],
        )
    )
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(_source_key(row), []).append(row)

    client: LLMClient = OllamaClient(
        base_url=base_url,
        timeout=timeout_seconds,
    )
    evaluated_rows: list[dict[str, Any]] = []
    for source_key in sorted(grouped):
        target_rows = grouped[source_key]
        evaluations = _evaluate_source_group(
            client=client,
            model=model.strip(),
            source_row=target_rows[0],
            target_rows=target_rows,
        )
        for evaluation, candidate_row in zip(evaluations, target_rows, strict=True):
            if evaluation["target_index"] != candidate_row["rank"]:
                raise CrossThreadIntentEvalError(
                    "cross-thread intent evaluation target_index did not match "
                    "candidate rank ordering"
                )
            evaluated_rows.append(
                _evaluation_row(
                    candidate_row=candidate_row,
                    evaluation=evaluation,
                )
            )

    evaluated_rows.sort(
        key=lambda row: (
            row["source_conversation_id"],
            row["source_topic_id"],
            row["source_span_id"],
            row["candidate_rank"],
            row["target_conversation_id"],
            row["target_topic_id"],
            row["target_span_id"],
        )
    )
    return evaluated_rows


def _summary(
    *,
    input_root: Path,
    rows: list[dict[str, Any]],
    model: str,
) -> dict[str, Any]:
    confidence_counts: Counter[str] = Counter(row["confidence"] for row in rows)
    same_intent_counts: Counter[str] = Counter(row["same_intent"] for row in rows)
    return {
        "artifact_type": CROSS_THREAD_INTENT_EVAL_SUMMARY_ARTIFACT_TYPE,
        "schema_version": CROSS_THREAD_INTENT_EVAL_SCHEMA_VERSION,
        "generated_from": str(_candidate_rows_path(input_root).resolve()),
        "model": model,
        "prompt_variant": PROMPT_VARIANT,
        "source_group_count": len(
            {
                (
                    row["source_conversation_id"],
                    row["source_topic_id"],
                    row["source_span_id"],
                )
                for row in rows
            }
        ),
        "evaluation_count": len(rows),
        "same_intent_counts": {
            "yes": same_intent_counts.get("yes", 0),
            "no": same_intent_counts.get("no", 0),
        },
        "confidence_counts": {
            "high": confidence_counts.get("high", 0),
            "medium": confidence_counts.get("medium", 0),
            "low": confidence_counts.get("low", 0),
        },
    }


def write_cross_thread_intent_evaluation_artifact(
    input_root: Path,
    *,
    model: str,
    base_url: str = DEFAULT_OLLAMA_BASE_URL,
    timeout_seconds: float = DEFAULT_OLLAMA_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    rows = build_cross_thread_intent_evaluation_rows(
        input_root,
        model=model,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
    )
    if not rows:
        raise CrossThreadIntentEvalError(
            "no cross-thread candidates were available to evaluate"
        )

    output_dir = input_root / "l4" / "cross-thread-intent-eval"
    output_dir.mkdir(parents=True, exist_ok=True)
    evaluations_path = cross_thread_intent_evaluations_path(input_root)
    with evaluations_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary_path = output_dir / "summary.json"
    summary = _summary(input_root=input_root, rows=rows, model=model.strip())
    write_json_artifact(summary_path, summary)
    return {
        "evaluation_count": len(rows),
        "evaluations_path": evaluations_path,
        "summary_path": summary_path,
    }
