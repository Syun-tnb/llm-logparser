from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


REVIEW_QUEUE_SCHEMA_VERSION = "0.1"
REVIEW_QUEUE_RECORD_TYPE = "review_candidate"
REVIEW_QUEUE_ARTIFACT_TYPE = "review_queue"
LEXICAL_SOURCE = "l3/lexical-rules/candidates.jsonl"
CROSS_THREAD_SOURCE = "l3/cross-thread-candidates/candidates.jsonl"
REPORT_CANDIDATE_LIMIT = 20


class ReviewCandidateError(RuntimeError):
    pass


def review_queue_dir(input_root: Path) -> Path:
    return input_root / "l3" / "review-queue"


def review_queue_candidates_path(input_root: Path) -> Path:
    return review_queue_dir(input_root) / "candidates.jsonl"


def review_queue_report_path(input_root: Path) -> Path:
    return review_queue_dir(input_root) / "report.json"


def review_queue_markdown_path(input_root: Path) -> Path:
    return review_queue_dir(input_root) / "report.md"


def write_review_candidate_artifacts(
    input_root: Path,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    provider_root = input_root.expanduser()
    if not provider_root.exists() or not provider_root.is_dir():
        raise ReviewCandidateError(f"provider root not found: {provider_root}")

    output_paths = [
        review_queue_candidates_path(provider_root),
        review_queue_report_path(provider_root),
        review_queue_markdown_path(provider_root),
    ]
    existing_outputs = [path for path in output_paths if path.exists()]
    if existing_outputs and not overwrite:
        raise ReviewCandidateError(
            "review queue artifacts already exist; rerun with --overwrite: "
            + ", ".join(str(path) for path in existing_outputs)
        )

    rows, source_inputs, warnings = _build_review_candidate_rows(provider_root)
    report = _build_report(
        rows,
        source_inputs=source_inputs,
        warnings=warnings,
    )
    markdown = _render_report_markdown(report, rows)

    output_dir = review_queue_dir(provider_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(review_queue_candidates_path(provider_root), rows)
    _write_json(review_queue_report_path(provider_root), report)
    _write_text(review_queue_markdown_path(provider_root), markdown)

    return {
        "candidate_count": len(rows),
        "warnings": warnings,
        "candidates_path": review_queue_candidates_path(provider_root),
        "report_path": review_queue_report_path(provider_root),
        "markdown_path": review_queue_markdown_path(provider_root),
    }


def _build_review_candidate_rows(
    provider_root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    source_inputs: list[dict[str, Any]] = []
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []

    lexical_path = provider_root / LEXICAL_SOURCE
    lexical_rows, lexical_input = _load_source_rows(lexical_path, LEXICAL_SOURCE)
    source_inputs.append(lexical_input)
    if lexical_input["status"] == "missing":
        warnings.append(f"missing source artifact: {LEXICAL_SOURCE}")
    else:
        rows.extend(_normalize_lexical_row(row) for row in lexical_rows)

    cross_thread_path = provider_root / CROSS_THREAD_SOURCE
    cross_rows, cross_input = _load_source_rows(cross_thread_path, CROSS_THREAD_SOURCE)
    source_inputs.append(cross_input)
    if cross_input["status"] == "missing":
        warnings.append(f"missing source artifact: {CROSS_THREAD_SOURCE}")
    else:
        rows.extend(_normalize_cross_thread_row(row) for row in cross_rows)

    rows.sort(
        key=lambda row: (
            str(row.get("candidate_type") or ""),
            str(row.get("source_artifact") or ""),
            str(row.get("source_candidate_id") or ""),
            str(row.get("candidate_id") or ""),
        )
    )
    return rows, source_inputs, warnings


def _load_source_rows(path: Path, source_artifact: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not path.exists():
        return [], {
            "source_artifact": source_artifact,
            "path": str(path),
            "status": "missing",
            "candidate_count": 0,
        }
    rows = _read_jsonl(path)
    return rows, {
        "source_artifact": source_artifact,
        "path": str(path),
        "status": "loaded",
        "candidate_count": len(rows),
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ReviewCandidateError(
                    f"invalid JSON in {path}:{line_no}: {exc.msg}"
                ) from exc
            if not isinstance(payload, dict):
                raise ReviewCandidateError(f"invalid JSON object in {path}:{line_no}")
            rows.append(payload)
    return rows


def _normalize_lexical_row(row: dict[str, Any]) -> dict[str, Any]:
    source_candidate_id = _source_candidate_id(row, fallback_prefix="lexical")
    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    proposed_change = {
        "suggested_rule_path": row.get("suggested_rule_path"),
        "value": row.get("value"),
        "value_kind": row.get("value_kind"),
        "normalized_value": row.get("normalized_value"),
        "review_recommendation": _nested_get(row, "review", "recommendation"),
    }
    return _base_review_row(
        source_artifact=LEXICAL_SOURCE,
        source_candidate_id=source_candidate_id,
        candidate_type="lexical_rule",
        source_command="analyze lexical-rule-candidates",
        provider_id=row.get("provider_id"),
        scope=row.get("suggested_scope"),
        evidence_refs=row.get("sample_refs") if isinstance(row.get("sample_refs"), list) else [],
        diagnostics={
            "source_record_type": row.get("record_type"),
            "source_candidate_type": row.get("candidate_type"),
            "source_status": row.get("status"),
            "score": evidence.get("score"),
            "reason_codes": evidence.get("reason_codes", []),
            "source_payload": row,
        },
        proposed_change=proposed_change,
        risk_flags=_lexical_risk_flags(row),
    )


def _normalize_cross_thread_row(row: dict[str, Any]) -> dict[str, Any]:
    source_candidate_id = _source_candidate_id(row, fallback_prefix="cross_thread")
    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    source_ref = {
        "side": "source",
        "conversation_id": row.get("source_conversation_id"),
        "topic_id": row.get("source_topic_id"),
        "span_id": row.get("source_span_id"),
        "segment_id": row.get("source_segment_id"),
        "message_ids": row.get("source_message_ids", []),
    }
    target_ref = {
        "side": "target",
        "conversation_id": row.get("target_conversation_id"),
        "topic_id": row.get("target_topic_id"),
        "span_id": row.get("target_span_id"),
        "segment_id": row.get("target_segment_id"),
        "message_ids": row.get("target_message_ids", []),
    }
    return _base_review_row(
        source_artifact=CROSS_THREAD_SOURCE,
        source_candidate_id=source_candidate_id,
        candidate_type="cross_thread_link",
        source_command="analyze cross-thread-candidates",
        provider_id=row.get("provider_id"),
        scope="provider",
        evidence_refs=[source_ref, target_ref],
        diagnostics={
            "source_record_type": row.get("record_type"),
            "score": row.get("score"),
            "rank": row.get("rank"),
            "reason_codes": evidence.get("reason_codes", []),
            "excerpt_similarity": evidence.get("excerpt_similarity"),
            "topic_label_similarity": evidence.get("topic_label_similarity"),
            "source_payload": row,
        },
        proposed_change={
            "link_type": "cross_thread_candidate",
            "source": source_ref,
            "target": target_ref,
        },
        risk_flags=_cross_thread_risk_flags(row),
    )


def _base_review_row(
    *,
    source_artifact: str,
    source_candidate_id: str,
    candidate_type: str,
    source_command: str,
    provider_id: Any,
    scope: Any,
    evidence_refs: list[Any],
    diagnostics: dict[str, Any],
    proposed_change: dict[str, Any],
    risk_flags: list[str],
) -> dict[str, Any]:
    candidate_id = _review_candidate_id(
        source_artifact=source_artifact,
        source_candidate_id=source_candidate_id,
        candidate_type=candidate_type,
    )
    return {
        "record_type": REVIEW_QUEUE_RECORD_TYPE,
        "schema_version": REVIEW_QUEUE_SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "candidate_type": candidate_type,
        "status": "candidate",
        "activation_state": "requires_review",
        "source_artifact": source_artifact,
        "source_candidate_id": source_candidate_id,
        "source_command": source_command,
        "provider_id": provider_id,
        "scope": scope,
        "evidence_refs": evidence_refs,
        "diagnostics": diagnostics,
        "proposed_change": proposed_change,
        "risk_flags": risk_flags,
        "review_notes": None,
    }


def _source_candidate_id(row: dict[str, Any], *, fallback_prefix: str) -> str:
    explicit = row.get("candidate_id")
    if isinstance(explicit, str) and explicit:
        return explicit
    digest = hashlib.sha1(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()[:16]
    return f"{fallback_prefix}_{digest}"


def _review_candidate_id(
    *,
    source_artifact: str,
    source_candidate_id: str,
    candidate_type: str,
) -> str:
    digest = hashlib.sha1(
        "|".join((source_artifact, source_candidate_id, candidate_type)).encode("utf-8")
    ).hexdigest()[:16]
    return f"review_{digest}"


def _lexical_risk_flags(row: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    source_type = str(row.get("candidate_type") or "")
    if source_type == "generic_scoring_token":
        flags.append("review_for_persona_or_project_term")
    if row.get("already_active") is True:
        flags.append("already_active_policy")
    return flags


def _cross_thread_risk_flags(row: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    if row.get("continuity_mask") is True:
        flags.append("continuity_masked")
    score = row.get("score")
    if isinstance(score, (int, float)) and score < 0.45:
        flags.append("low_score")
    return flags


def _nested_get(value: dict[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _build_report(
    rows: list[dict[str, Any]],
    *,
    source_inputs: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    risk_flag_counts = _counter_dict(
        flag
        for row in rows
        for flag in _risk_flags(row)
    )
    risk_flagged_count = sum(1 for row in rows if _risk_flags(row))
    return {
        "artifact_type": REVIEW_QUEUE_ARTIFACT_TYPE,
        "schema_version": REVIEW_QUEUE_SCHEMA_VERSION,
        "candidate_count": len(rows),
        "candidate_counts_by_type": _counter_dict(row.get("candidate_type") for row in rows),
        "candidate_counts_by_source_artifact": _counter_dict(
            row.get("source_artifact") for row in rows
        ),
        "review_priority_summary": {
            "risk_flagged": risk_flagged_count,
            "unflagged": len(rows) - risk_flagged_count,
        },
        "risk_flag_counts": risk_flag_counts,
        "report_limits": {
            "candidates_per_major_type": REPORT_CANDIDATE_LIMIT,
        },
        "source_inputs": source_inputs,
        "warnings": warnings,
        "policy_effect": "inactive_review_queue_only",
    }


def _counter_dict(values: Iterable[Any]) -> dict[str, int]:
    counter: Counter[str] = Counter(str(value or "unknown") for value in values)
    return dict(sorted(counter.items()))


def _render_report_markdown(report: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Review Queue Report",
        "",
        "> This review queue is inactive. It does not accept, reject, promote,",
        "> suppress, or activate policy. Source candidate artifacts remain",
        "> authoritative for their own generation outputs.",
        "",
        f"- candidate_count: {report['candidate_count']}",
        "",
        "## Review Priority Summary",
        "",
    ]
    _append_count_lines(lines, report.get("review_priority_summary", {}))
    lines.extend(["", "## Risk Flags", ""])
    _append_count_lines(lines, report.get("risk_flag_counts", {}))
    lines.extend(["", "## Candidate Types", ""])
    _append_count_lines(lines, report.get("candidate_counts_by_type", {}))
    lines.extend(["", "## Source Artifacts", ""])
    _append_count_lines(lines, report.get("candidate_counts_by_source_artifact", {}))
    lines.extend(["", "## Source Inputs", ""])
    for source in report.get("source_inputs", []):
        lines.append(
            f"- {source['source_artifact']}: {source['status']} "
            f"({source['candidate_count']} candidate(s))"
        )
    warnings = report.get("warnings", [])
    lines.extend(["", "## Warnings", ""])
    if warnings:
        for warning in warnings:
            lines.append(f"- {warning}")
    else:
        lines.append("- none")
    cross_thread_rows = _prioritized_rows(rows, "cross_thread_link")
    lexical_rows = _prioritized_rows(rows, "lexical_rule")
    lines.extend(["", "## Top Cross-Thread Link Candidates", ""])
    _render_cross_thread_table(lines, cross_thread_rows)
    lines.extend(["", "## Top Lexical Rule Candidates", ""])
    _render_lexical_table(lines, lexical_rows)
    return "\n".join(lines).rstrip() + "\n"


def _append_count_lines(lines: list[str], counts: dict[str, Any]) -> None:
    if counts:
        for key, count in sorted(counts.items()):
            lines.append(f"- {key}: {count}")
    else:
        lines.append("- none")


def _risk_flags(row: dict[str, Any]) -> list[str]:
    flags = row.get("risk_flags")
    if not isinstance(flags, list):
        return []
    return sorted(str(flag) for flag in flags if str(flag))


def _reason_codes(row: dict[str, Any]) -> list[str]:
    diagnostics = row.get("diagnostics")
    if not isinstance(diagnostics, dict):
        return []
    values = diagnostics.get("reason_codes")
    if not isinstance(values, list):
        return []
    return [str(value) for value in values if str(value)]


def _diagnostic_score(row: dict[str, Any]) -> float:
    diagnostics = row.get("diagnostics")
    if not isinstance(diagnostics, dict):
        return 0.0
    score = diagnostics.get("score")
    if isinstance(score, (int, float)):
        return float(score)
    return 0.0


def _prioritized_rows(rows: list[dict[str, Any]], candidate_type: str) -> list[dict[str, Any]]:
    selected = [row for row in rows if row.get("candidate_type") == candidate_type]
    return sorted(
        selected,
        key=lambda row: (
            0 if _risk_flags(row) else 1,
            -_diagnostic_score(row),
            str(row.get("source_candidate_id") or ""),
            str(row.get("candidate_id") or ""),
        ),
    )


def _render_cross_thread_table(
    lines: list[str],
    rows: list[dict[str, Any]],
) -> None:
    if not rows:
        lines.append("_No cross-thread link candidates._")
        return
    lines.append("| source_candidate_id | score | reasons | risks | source | target |")
    lines.append("| --- | ---: | --- | --- | --- | --- |")
    for row in rows[:REPORT_CANDIDATE_LIMIT]:
        refs = row.get("evidence_refs") if isinstance(row.get("evidence_refs"), list) else []
        source_ref = refs[0] if len(refs) > 0 and isinstance(refs[0], dict) else {}
        target_ref = refs[1] if len(refs) > 1 and isinstance(refs[1], dict) else {}
        values = [
            row.get("source_candidate_id"),
            _format_score(row),
            _compact_list(_reason_codes(row)),
            _compact_list(_risk_flags(row)),
            _format_evidence_ref(source_ref),
            _format_evidence_ref(target_ref),
        ]
        lines.append("| " + " | ".join(_md_cell(value) for value in values) + " |")
    _append_cap_notice(lines, shown=min(len(rows), REPORT_CANDIDATE_LIMIT), total=len(rows))


def _render_lexical_table(lines: list[str], rows: list[dict[str, Any]]) -> None:
    if not rows:
        lines.append("_No lexical rule candidates._")
        return
    lines.append("| source_candidate_id | value | subtype | suggested_rule_path | score | reasons | risks |")
    lines.append("| --- | --- | --- | --- | ---: | --- | --- |")
    for row in rows[:REPORT_CANDIDATE_LIMIT]:
        proposed = row.get("proposed_change") if isinstance(row.get("proposed_change"), dict) else {}
        diagnostics = row.get("diagnostics") if isinstance(row.get("diagnostics"), dict) else {}
        values = [
            row.get("source_candidate_id"),
            proposed.get("value") or proposed.get("normalized_value"),
            diagnostics.get("source_candidate_type"),
            proposed.get("suggested_rule_path"),
            _format_score(row),
            _compact_list(_reason_codes(row)),
            _compact_list(_risk_flags(row)),
        ]
        lines.append("| " + " | ".join(_md_cell(value) for value in values) + " |")
    _append_cap_notice(lines, shown=min(len(rows), REPORT_CANDIDATE_LIMIT), total=len(rows))


def _append_cap_notice(lines: list[str], *, shown: int, total: int) -> None:
    if total > shown:
        lines.append(f"\n_Showing top {shown} of {total} candidates; list capped at {REPORT_CANDIDATE_LIMIT}._")


def _format_score(row: dict[str, Any]) -> str:
    diagnostics = row.get("diagnostics")
    if not isinstance(diagnostics, dict):
        return ""
    score = diagnostics.get("score")
    if not isinstance(score, (int, float)):
        return ""
    return f"{float(score):.4f}".rstrip("0").rstrip(".")


def _compact_list(values: list[str], *, limit: int = 4) -> str:
    if not values:
        return "none"
    rendered = ", ".join(values[:limit])
    if len(values) > limit:
        rendered += f" (+{len(values) - limit})"
    return rendered


def _format_evidence_ref(ref: dict[str, Any]) -> str:
    conversation_id = str(ref.get("conversation_id") or "")
    span_id = str(ref.get("span_id") or ref.get("segment_id") or "")
    if conversation_id and span_id:
        return f"{conversation_id}/{span_id}"
    return conversation_id or span_id


def _md_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    tmp_path = path.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp_path.replace(path)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)


def _write_text(path: Path, payload: str) -> None:
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(payload, encoding="utf-8")
    tmp_path.replace(path)
