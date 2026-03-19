from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .analyzer_tokens import (
    _detect_header_metadata,
    _normalize_role,
    _resolve_text,
    _string_or_none,
)
from .l1_derivation import discover_parsed_jsonl, iter_parsed_records


def _safe_ratio(numerator: int | float, denominator: int | float) -> float:
    if not denominator:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _safe_average(total: int | float, count: int | float) -> float:
    if not count:
        return 0.0
    return round(float(total) / float(count), 2)


def _load_token_stats(parsed_path: Path) -> dict[str, Any]:
    token_stats_path = parsed_path.with_name("token_stats.json")
    if not token_stats_path.exists():
        raise FileNotFoundError(
            f"required token_stats.json not found next to parsed.jsonl: {token_stats_path}"
        )

    try:
        payload = json.loads(token_stats_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {token_stats_path}: {exc.msg}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"invalid token_stats artifact in {token_stats_path}: expected object")
    if payload.get("artifact_type") != "token_stats":
        raise ValueError(f"invalid token_stats artifact in {token_stats_path}: wrong artifact_type")

    return payload


def _require_int(payload: dict[str, Any], path: str) -> int:
    current: Any = payload
    for key in path.split("."):
        if not isinstance(current, dict) or key not in current:
            raise ValueError(f"missing required field in token_stats.json: {path}")
        current = current[key]
    if not isinstance(current, int):
        raise ValueError(f"invalid required field in token_stats.json: {path}")
    return current


def _require_number(payload: dict[str, Any], path: str) -> int | float:
    current: Any = payload
    for key in path.split("."):
        if not isinstance(current, dict) or key not in current:
            raise ValueError(f"missing required field in token_stats.json: {path}")
        current = current[key]
    if not isinstance(current, (int, float)):
        raise ValueError(f"invalid required field in token_stats.json: {path}")
    return current


def _load_diversity_units(token_stats: dict[str, Any], texts: list[str]) -> tuple[int, int]:
    tokenizer_meta = token_stats.get("tokenizer")
    if isinstance(tokenizer_meta, dict):
        if tokenizer_meta.get("library") == "tiktoken":
            resolved_encoding = _string_or_none(tokenizer_meta.get("resolved_encoding"))
            if resolved_encoding:
                import tiktoken

                encoder = tiktoken.get_encoding(resolved_encoding)
                total_tokens = 0
                unique_tokens: set[int] = set()
                for text in texts:
                    encoded = encoder.encode_ordinary(text)
                    total_tokens += len(encoded)
                    unique_tokens.update(encoded)
                return len(unique_tokens), total_tokens

    total_tokens = 0
    unique_tokens: set[str] = set()
    for text in texts:
        pieces = text.split()
        total_tokens += len(pieces)
        unique_tokens.update(pieces)
    return len(unique_tokens), total_tokens


def build_metrics_artifact(parsed_path: Path) -> dict[str, Any]:
    token_stats = _load_token_stats(parsed_path)
    provider_id, conversation_id = _detect_header_metadata(parsed_path)

    char_count_total = 0
    char_count_user = 0
    char_count_assistant = 0
    texts: list[str] = []
    message_count_user = 0
    message_count_assistant = 0

    for row in iter_parsed_records(parsed_path):
        if row.get("record_type") != "message":
            continue

        if provider_id is None:
            provider_id = _string_or_none(row.get("provider_id"))
        if conversation_id is None:
            conversation_id = _string_or_none(row.get("conversation_id"))

        text, _text_source = _resolve_text(row)
        texts.append(text)
        char_count = len(text)
        char_count_total += char_count

        role = _normalize_role(row.get("role"))
        if role == "user":
            message_count_user += 1
            char_count_user += char_count
        elif role == "assistant":
            message_count_assistant += 1
            char_count_assistant += char_count

    if conversation_id is None:
        raise ValueError(f"parsed thread has no conversation_id: {parsed_path}")

    token_total = _require_int(token_stats, "summary.tokens_total")
    turn_count = _require_int(token_stats, "summary.turn_count")
    token_count_user = _require_int(token_stats, "summary.tokens_user")
    token_count_assistant = _require_int(token_stats, "summary.tokens_assistant")
    message_count_total = _require_int(token_stats, "summary.message_count")
    avg_tokens_per_message = _require_number(token_stats, "summary.avg_tokens_per_message")
    avg_tokens_per_turn = _require_number(token_stats, "summary.avg_tokens_per_turn")

    unique_token_count, diversity_token_total = _load_diversity_units(token_stats, texts)

    artifact = {
        "artifact_type": "metrics",
        "schema_version": "1.0",
        "provider_id": provider_id or _string_or_none(token_stats.get("provider_id")) or "unknown",
        "conversation_id": conversation_id,
        "ratios": {
            "prompt_response_ratio_tokens": _safe_ratio(
                token_count_user,
                token_count_assistant,
            ),
            "prompt_response_ratio_chars": _safe_ratio(
                char_count_user,
                char_count_assistant,
            ),
            "assistant_to_user_ratio": _safe_ratio(
                message_count_assistant,
                message_count_user,
            ),
        },
        "tokens": {
            "total": token_total,
            "avg_per_message": avg_tokens_per_message,
            "avg_per_turn": avg_tokens_per_turn,
        },
        "characters": {
            "total": char_count_total,
            "user": char_count_user,
            "assistant": char_count_assistant,
            "avg_per_message": _safe_average(char_count_total, message_count_total),
            "avg_per_turn": _safe_average(char_count_total, turn_count),
        },
        "distribution": {
            "message_total": message_count_total,
            "message_user": message_count_user,
            "message_assistant": message_count_assistant,
            "messages_per_turn": _safe_average(message_count_total, turn_count),
        },
        "diversity": {
            "type_token_ratio": _safe_ratio(unique_token_count, diversity_token_total),
            "unique_token_ratio": _safe_ratio(unique_token_count, diversity_token_total),
        },
    }
    return artifact


def render_metrics_json(artifact: dict[str, Any]) -> str:
    return json.dumps(artifact, ensure_ascii=False, indent=2)


def write_metrics_artifact(parsed_path: Path, artifact: dict[str, Any]) -> Path:
    artifact_path = parsed_path.with_name("metrics.json")
    tmp = artifact_path.with_suffix(".tmp")
    tmp.write_text(render_metrics_json(artifact), encoding="utf-8")
    tmp.replace(artifact_path)
    return artifact_path


def analyze_metrics(input_path: Path) -> dict[str, Any]:
    parsed_files = discover_parsed_jsonl(input_path)
    written_artifacts: list[Path] = []

    for parsed_path in parsed_files:
        artifact = build_metrics_artifact(parsed_path)
        written_artifacts.append(write_metrics_artifact(parsed_path, artifact))

    return {
        "threads": len(parsed_files),
        "artifacts": written_artifacts,
    }
