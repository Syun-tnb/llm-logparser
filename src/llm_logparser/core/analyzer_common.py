from __future__ import annotations

import json
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable
from typing import Any

from .l1_derivation import (
    ROLE_ORDER,
    UNKNOWN_ROLE,
    iter_parsed_records,
    resolve_message_text,
)

RATIO_DECIMAL_PLACES = 4
AVERAGE_DECIMAL_PLACES = 2
ZERO_DIVISION_FALLBACK = 0.0


def string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def resolve_canonical_text(row: dict[str, Any]) -> tuple[str, str]:
    return resolve_message_text(row)


def detect_header_metadata(parsed_path: Path) -> tuple[str | None, str | None]:
    provider_id: str | None = None
    conversation_id: str | None = None

    for row in iter_parsed_records(parsed_path):
        if row.get("record_type") == "thread":
            provider_id = provider_id or string_or_none(row.get("provider_id"))
            conversation_id = conversation_id or string_or_none(
                row.get("conversation_id")
            )
            if provider_id and conversation_id:
                break
            continue

        if row.get("record_type") != "message":
            continue

        provider_id = provider_id or string_or_none(row.get("provider_id"))
        conversation_id = conversation_id or string_or_none(row.get("conversation_id"))
        break

    return provider_id, conversation_id


def safe_ratio(numerator: int | float, denominator: int | float) -> float:
    """Return a deterministic ratio for artifact fields.

    Analyzer metrics treat division-by-zero as a non-fatal, deterministic edge
    case and emit `0.0` instead of raising or leaking NaN/Infinity into JSON.
    Ratios are rounded to a fixed artifact-friendly precision.
    """
    if not denominator:
        return ZERO_DIVISION_FALLBACK
    return round(float(numerator) / float(denominator), RATIO_DECIMAL_PLACES)


def safe_average(total: int | float, count: int | float) -> float:
    """Return a deterministic average for artifact fields.

    As with `safe_ratio`, empty populations intentionally collapse to `0.0` so
    analyzer artifacts stay machine-readable and deterministic.
    """
    if not count:
        return ZERO_DIVISION_FALLBACK
    return round(float(total) / float(count), AVERAGE_DECIMAL_PLACES)


def normalize_analysis_text(text: str) -> str:
    """Normalize free text for heuristic matching.

    Phrase-based analyzer heuristics use casefolding plus whitespace collapsing
    so YAML cue lists and message text can be compared with stable local rules.
    """
    return " ".join(text.casefold().split())


def has_min_normalized_length(text: str, minimum: int) -> bool:
    return len(normalize_analysis_text(text)) >= minimum


def normalized_similarity(left: str, right: str) -> float:
    left_normalized = normalize_analysis_text(left)
    right_normalized = normalize_analysis_text(right)
    if not left_normalized or not right_normalized:
        return 0.0
    return SequenceMatcher(None, left_normalized, right_normalized).ratio()


def render_artifact_json(artifact: dict[str, Any]) -> str:
    return json.dumps(artifact, ensure_ascii=False, indent=2)


def write_json_artifact(path: Path, artifact: dict[str, Any]) -> Path:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(render_artifact_json(artifact), encoding="utf-8")
    tmp.replace(path)
    return path


def plan_sidecar_actions(
    parsed_files: list[Path],
    artifact_path_resolver: Callable[[Path], Path],
    *,
    skip_existing: bool,
) -> dict[str, Any]:
    planned_actions: list[tuple[Path, Path]] = []
    existing_artifacts: list[Path] = []
    new_artifacts: list[Path] = []
    rebuild_artifacts: list[Path] = []
    skipped_artifacts: list[Path] = []

    for parsed_path in parsed_files:
        artifact_path = artifact_path_resolver(parsed_path)
        if artifact_path.exists():
            existing_artifacts.append(artifact_path)
            if skip_existing:
                skipped_artifacts.append(artifact_path)
                continue
            rebuild_artifacts.append(artifact_path)
        else:
            new_artifacts.append(artifact_path)

        planned_actions.append((parsed_path, artifact_path))

    return {
        "detected_threads": len(parsed_files),
        "planned_actions": planned_actions,
        "existing_artifacts": existing_artifacts,
        "existing_threads": len(existing_artifacts),
        "new_artifacts": new_artifacts,
        "new_threads": len(new_artifacts),
        "rebuild_artifacts": rebuild_artifacts,
        "rebuild_threads": len(rebuild_artifacts),
        "skipped_artifacts": skipped_artifacts,
        "skipped_threads": len(skipped_artifacts),
    }
