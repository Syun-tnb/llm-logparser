from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from .llm_client_protocol import LLMClient
from .structured_llm import generate_structured_json

SEED_TAXONOMY_VERSION = "seed_taxonomy_v0"
RAW_LABEL_CONFIDENCE_THRESHOLD = 0.65
DEFAULT_RAW_LABEL_NUM_PREDICT = 180
DEFAULT_MAPPING_NUM_PREDICT = 160
DEFAULT_NORMALIZATION_TEMPERATURE = 0.0
SEMANTIC_NORMALIZATION_PROMPT_SET = "semantic_normalization_v0"
_PROMPT_RESOURCE_DIR = (
    Path(__file__).resolve().parent.parent
    / "resources"
    / "semantic_normalization_prompts"
)
_RAW_LABEL_PROMPT_REPO_PATH = (
    "src/llm_logparser/resources/semantic_normalization_prompts/raw_label.prompt.txt"
)
_MAPPING_PROMPT_REPO_PATH = (
    "src/llm_logparser/resources/semantic_normalization_prompts/raw_label_mapping.prompt.txt"
)
MethodKind = Literal["llm", "rule", "hybrid"]
MappingStatus = Literal["mapped", "needs_review", "taxonomy_gap", "unmapped"]
STABLE_NORMALIZED_LABELS = (
    "proposal",
    "request",
    "agreement",
    "disagreement",
    "decision",
    "clarification",
    "question",
    "step_transition",
    "status_update",
    "reflection",
)
MAPPING_STATUSES: tuple[MappingStatus, ...] = (
    "mapped",
    "needs_review",
    "taxonomy_gap",
    "unmapped",
)
NO_DOMINANT_RAW_LABELS = frozenset(
    {
        "no_dominant_act",
        "unclear_act",
        "mixed_act",
        "other",
        "unknown",
        "none",
    }
)
GENERIC_UNMAPPED_RAW_LABELS = frozenset(
    {
        "commentary",
        "reaction",
        "banter",
        "chatting",
        "small_talk",
        "venting",
        "emotion",
        "exclamation",
        "mixed_humor",
    }
)
RAW_LABEL_ALIASES = {
    "proposal": "proposal",
    "option_proposal": "proposal",
    "suggestion": "proposal",
    "plan_proposal": "proposal",
    "invitation": "proposal",
    "request": "request",
    "action_request": "request",
    "implementation_request": "request",
    "update_request": "request",
    "review_request": "request",
    "change_request": "request",
    "help_request": "request",
    "agreement": "agreement",
    "acceptance": "agreement",
    "approval": "agreement",
    "confirmation": "agreement",
    "scope_alignment": "agreement",
    "alignment": "agreement",
    "disagreement": "disagreement",
    "objection": "disagreement",
    "pushback": "disagreement",
    "reluctance": "disagreement",
    "rejection": "disagreement",
    "decision": "decision",
    "implementation_decision": "decision",
    "naming_decision": "decision",
    "scope_decision": "decision",
    "choice_confirmation": "decision",
    "clarification": "clarification",
    "clarification_request": "clarification",
    "reframing": "clarification",
    "disambiguation": "clarification",
    "question": "question",
    "open_question": "question",
    "information_request": "question",
    "fact_question": "question",
    "step_transition": "step_transition",
    "next_step": "step_transition",
    "handoff": "step_transition",
    "phase_transition": "step_transition",
    "sequencing": "step_transition",
    "status_update": "status_update",
    "progress_update": "status_update",
    "completion_update": "status_update",
    "completion_report": "status_update",
    "implementation_update": "status_update",
    "reflection": "reflection",
    "self_assessment": "reflection",
    "retrospective": "reflection",
    "evaluation": "reflection",
    "analysis_reflection": "reflection",
}

def _load_prompt_text(path: Path) -> tuple[str, str]:
    payload = path.read_bytes()
    return payload.decode("utf-8"), hashlib.sha1(payload).hexdigest()


RAW_LABEL_PROMPT, _RAW_LABEL_PROMPT_SHA1 = _load_prompt_text(
    _PROMPT_RESOURCE_DIR / "raw_label.prompt.txt"
)
RAW_LABEL_MAPPING_PROMPT, _RAW_LABEL_MAPPING_PROMPT_SHA1 = _load_prompt_text(
    _PROMPT_RESOURCE_DIR / "raw_label_mapping.prompt.txt"
)


@dataclass(frozen=True)
class SemanticNormalizationMethod:
    kind: MethodKind
    model: str | None
    mapping_version: str = SEED_TAXONOMY_VERSION


@dataclass(frozen=True)
class SemanticNormalizationResult:
    conversation_id: str
    span_id: str
    window_id: str | None
    message_ids: list[str]
    unit_kind: Literal["representative_span"]
    raw_label: str
    normalized_label: str | None
    mapping_status: MappingStatus
    confidence: float | None
    method: SemanticNormalizationMethod


@dataclass(frozen=True)
class SemanticNormalizationRuntimeOptions:
    temperature: float = DEFAULT_NORMALIZATION_TEMPERATURE
    raw_num_predict: int = DEFAULT_RAW_LABEL_NUM_PREDICT
    mapping_num_predict: int = DEFAULT_MAPPING_NUM_PREDICT


def semantic_normalization_to_dict(
    result: SemanticNormalizationResult,
) -> dict[str, Any]:
    return asdict(result)


def semantic_normalization_runtime_options_to_dict(
    runtime_options: SemanticNormalizationRuntimeOptions,
) -> dict[str, Any]:
    return asdict(runtime_options)


def semantic_normalization_prompt_hashes() -> dict[str, str]:
    return {
        "raw_label_prompt_sha1": _RAW_LABEL_PROMPT_SHA1,
        "mapping_prompt_sha1": _RAW_LABEL_MAPPING_PROMPT_SHA1,
    }


def semantic_normalization_prompt_provenance() -> dict[str, str]:
    """Return inspectable prompt provenance for semantic normalization.

    Hashes are computed over the exact UTF-8 file bytes stored in the repository
    prompt files. No newline or whitespace normalization is applied before
    hashing.
    """
    return {
        "prompt_set": SEMANTIC_NORMALIZATION_PROMPT_SET,
        "raw_label_prompt_path": _RAW_LABEL_PROMPT_REPO_PATH,
        "mapping_prompt_path": _MAPPING_PROMPT_REPO_PATH,
        **semantic_normalization_prompt_hashes(),
    }


def _coerce_confidence(value: Any) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    clamped = max(0.0, min(1.0, float(value)))
    return round(clamped, 4)


def _sanitize_raw_label(value: Any) -> str:
    if not isinstance(value, str):
        return "no_dominant_act"
    lowered = value.strip().casefold()
    lowered = re.sub(r"[^a-z0-9]+", "_", lowered)
    lowered = re.sub(r"_+", "_", lowered).strip("_")
    if not lowered:
        return "no_dominant_act"
    parts = [part for part in lowered.split("_") if part]
    if not parts:
        return "no_dominant_act"
    return "_".join(parts[:3])


def _stable_label(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().casefold()
    return normalized if normalized in STABLE_NORMALIZED_LABELS else None


def _mapping_status(value: Any) -> MappingStatus | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().casefold()
    return normalized if normalized in MAPPING_STATUSES else None


def _result(
    *,
    conversation_id: str,
    span_id: str,
    window_id: str | None,
    message_ids: list[str],
    raw_label: str,
    normalized_label: str | None,
    mapping_status: MappingStatus,
    confidence: float | None,
    method_kind: MethodKind,
    model: str | None,
) -> SemanticNormalizationResult:
    return SemanticNormalizationResult(
        conversation_id=conversation_id,
        span_id=span_id,
        window_id=window_id,
        message_ids=list(message_ids),
        unit_kind="representative_span",
        raw_label=raw_label,
        normalized_label=normalized_label,
        mapping_status=mapping_status,
        confidence=confidence,
        method=SemanticNormalizationMethod(
            kind=method_kind,
            model=model,
        ),
    )


def _generate_raw_label(
    *,
    client: LLMClient,
    model: str,
    text: str,
    runtime_options: SemanticNormalizationRuntimeOptions,
) -> tuple[str, float | None]:
    payload = generate_structured_json(
        client,
        model=model,
        prompt=RAW_LABEL_PROMPT.format(span_text=text),
        options={
            "temperature": runtime_options.temperature,
            "num_predict": runtime_options.raw_num_predict,
        },
    )
    return _sanitize_raw_label(payload.get("raw_label")), _coerce_confidence(
        payload.get("confidence")
    )


def _llm_map_raw_label(
    *,
    client: LLMClient,
    model: str,
    raw_label: str,
    text: str,
    runtime_options: SemanticNormalizationRuntimeOptions,
) -> tuple[str | None, str | None, float | None]:
    payload = generate_structured_json(
        client,
        model=model,
        prompt=RAW_LABEL_MAPPING_PROMPT.format(
            raw_label=raw_label,
            span_text=text,
        ),
        options={
            "temperature": runtime_options.temperature,
            "num_predict": runtime_options.mapping_num_predict,
        },
    )
    return (
        _stable_label(payload.get("normalized_label")),
        _mapping_status(payload.get("mapping_status")),
        _coerce_confidence(payload.get("confidence")),
    )


def normalize_representative_span(
    *,
    client: LLMClient,
    model: str,
    conversation_id: str,
    span_id: str,
    window_id: str | None,
    message_ids: list[str],
    text: str,
    runtime_options: SemanticNormalizationRuntimeOptions | None = None,
) -> SemanticNormalizationResult:
    """Return an ephemeral L3 helper signal for one representative span.

    Model choice is supplied by the caller at runtime. This module does not
    prescribe or hardcode any specific local model.
    """
    compact_text = " ".join(text.split()).strip()
    effective_runtime_options = (
        runtime_options
        if runtime_options is not None
        else SemanticNormalizationRuntimeOptions()
    )
    if not compact_text:
        return _result(
            conversation_id=conversation_id,
            span_id=span_id,
            window_id=window_id,
            message_ids=message_ids,
            raw_label="no_dominant_act",
            normalized_label=None,
            mapping_status="unmapped",
            confidence=None,
            method_kind="rule",
            model=None,
        )

    raw_label, raw_confidence = _generate_raw_label(
        client=client,
        model=model,
        text=compact_text,
        runtime_options=effective_runtime_options,
    )
    if raw_label in NO_DOMINANT_RAW_LABELS:
        return _result(
            conversation_id=conversation_id,
            span_id=span_id,
            window_id=window_id,
            message_ids=message_ids,
            raw_label=raw_label,
            normalized_label=None,
            mapping_status="unmapped",
            confidence=raw_confidence,
            method_kind="llm",
            model=model,
        )

    normalized_label = RAW_LABEL_ALIASES.get(raw_label)
    if normalized_label is not None:
        if raw_confidence is not None and raw_confidence < RAW_LABEL_CONFIDENCE_THRESHOLD:
            return _result(
                conversation_id=conversation_id,
                span_id=span_id,
                window_id=window_id,
                message_ids=message_ids,
                raw_label=raw_label,
                normalized_label=None,
                mapping_status="needs_review",
                confidence=raw_confidence,
                method_kind="hybrid",
                model=model,
            )
        return _result(
            conversation_id=conversation_id,
            span_id=span_id,
            window_id=window_id,
            message_ids=message_ids,
            raw_label=raw_label,
            normalized_label=normalized_label,
            mapping_status="mapped",
            confidence=raw_confidence,
            method_kind="hybrid",
            model=model,
        )

    mapped_label, mapped_status, mapped_confidence = _llm_map_raw_label(
        client=client,
        model=model,
        raw_label=raw_label,
        text=compact_text,
        runtime_options=effective_runtime_options,
    )
    final_confidence = mapped_confidence if mapped_confidence is not None else raw_confidence
    if (
        mapped_label is not None
        and mapped_status == "mapped"
        and (final_confidence is None or final_confidence >= RAW_LABEL_CONFIDENCE_THRESHOLD)
    ):
        return _result(
            conversation_id=conversation_id,
            span_id=span_id,
            window_id=window_id,
            message_ids=message_ids,
            raw_label=raw_label,
            normalized_label=mapped_label,
            mapping_status="mapped",
            confidence=final_confidence,
            method_kind="llm",
            model=model,
        )

    if mapped_status == "needs_review":
        return _result(
            conversation_id=conversation_id,
            span_id=span_id,
            window_id=window_id,
            message_ids=message_ids,
            raw_label=raw_label,
            normalized_label=None,
            mapping_status="needs_review",
            confidence=final_confidence,
            method_kind="llm",
            model=model,
        )

    if mapped_status == "taxonomy_gap":
        return _result(
            conversation_id=conversation_id,
            span_id=span_id,
            window_id=window_id,
            message_ids=message_ids,
            raw_label=raw_label,
            normalized_label=None,
            mapping_status="taxonomy_gap",
            confidence=final_confidence,
            method_kind="llm",
            model=model,
        )

    if mapped_status == "unmapped":
        return _result(
            conversation_id=conversation_id,
            span_id=span_id,
            window_id=window_id,
            message_ids=message_ids,
            raw_label=raw_label,
            normalized_label=None,
            mapping_status="unmapped",
            confidence=final_confidence,
            method_kind="llm",
            model=model,
        )

    fallback_status = (
        "taxonomy_gap"
        if raw_label not in GENERIC_UNMAPPED_RAW_LABELS
        else "unmapped"
    )
    return _result(
        conversation_id=conversation_id,
        span_id=span_id,
        window_id=window_id,
        message_ids=message_ids,
        raw_label=raw_label,
        normalized_label=None,
        mapping_status=fallback_status,
        confidence=final_confidence,
        method_kind="llm",
        model=model,
    )
