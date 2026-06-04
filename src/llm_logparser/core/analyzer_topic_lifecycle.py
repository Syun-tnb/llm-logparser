from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


TOPIC_LIFECYCLE_SCHEMA_VERSION = "0.1"
TOPIC_LIFECYCLE_ARTIFACT_TYPE = "topic_lifecycle_diagnostics"
LOW_SCORE_THRESHOLD = 0.45
CROSS_THREAD_SOURCE = "l3/cross-thread-candidates/candidates.jsonl"
REVIEW_QUEUE_SOURCE = "l3/review-queue/candidates.jsonl"
LEXICAL_SOURCE = "l3/lexical-rules/candidates.jsonl"
TOPIC_SUMMARIES_SOURCE = "thread-*/l3/intra-thread-topics/topic-summaries.jsonl"


class TopicLifecycleError(RuntimeError):
    pass


def topic_lifecycle_dir(input_root: Path) -> Path:
    return input_root / "l3" / "diagnostics"


def topic_lifecycle_json_path(input_root: Path) -> Path:
    return topic_lifecycle_dir(input_root) / "topic_lifecycle.json"


def topic_lifecycle_markdown_path(input_root: Path) -> Path:
    return topic_lifecycle_dir(input_root) / "topic_lifecycle.md"


def write_topic_lifecycle_artifacts(
    input_root: Path,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    provider_root = input_root.expanduser()
    if not provider_root.exists() or not provider_root.is_dir():
        raise TopicLifecycleError(f"provider root not found: {provider_root}")

    output_paths = [
        topic_lifecycle_json_path(provider_root),
        topic_lifecycle_markdown_path(provider_root),
    ]
    existing_outputs = [path for path in output_paths if path.exists()]
    if existing_outputs and not overwrite:
        raise TopicLifecycleError(
            "topic lifecycle diagnostics already exist; rerun with --overwrite: "
            + ", ".join(str(path) for path in existing_outputs)
        )

    report = build_topic_lifecycle_report(provider_root)
    markdown = render_topic_lifecycle_markdown(report)

    output_dir = topic_lifecycle_dir(provider_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(topic_lifecycle_json_path(provider_root), report)
    _write_text(topic_lifecycle_markdown_path(provider_root), markdown)

    return {
        "candidate_count": report["candidate_count"],
        "warnings": report["warnings"],
        "json_path": topic_lifecycle_json_path(provider_root),
        "markdown_path": topic_lifecycle_markdown_path(provider_root),
    }


def build_topic_lifecycle_report(provider_root: Path) -> dict[str, Any]:
    source_inputs: list[dict[str, Any]] = []
    warnings: list[str] = []

    cross_rows, cross_input = _load_source_rows(
        provider_root / CROSS_THREAD_SOURCE,
        CROSS_THREAD_SOURCE,
    )
    source_inputs.append(cross_input)
    _warn_if_missing(warnings, cross_input)

    review_rows, review_input = _load_source_rows(
        provider_root / REVIEW_QUEUE_SOURCE,
        REVIEW_QUEUE_SOURCE,
    )
    source_inputs.append(review_input)
    _warn_if_missing(warnings, review_input)

    lexical_rows, lexical_input = _load_source_rows(
        provider_root / LEXICAL_SOURCE,
        LEXICAL_SOURCE,
    )
    source_inputs.append(lexical_input)
    _warn_if_missing(warnings, lexical_input)

    topic_summary_rows, topic_summary_input = _load_globbed_source_rows(
        provider_root,
        TOPIC_SUMMARIES_SOURCE,
    )
    source_inputs.append(topic_summary_input)
    _warn_if_missing(warnings, topic_summary_input)

    cross_summary = _summarize_cross_thread(cross_rows)
    review_summary = _summarize_review_queue(review_rows)
    lexical_summary = _summarize_lexical(lexical_rows)
    topic_summary_summary = _summarize_topic_summaries(topic_summary_rows)

    candidate_counts_by_source_artifact = {
        CROSS_THREAD_SOURCE: len(cross_rows),
        REVIEW_QUEUE_SOURCE: len(review_rows),
        LEXICAL_SOURCE: len(lexical_rows),
        TOPIC_SUMMARIES_SOURCE: len(topic_summary_rows),
    }
    candidate_counts_by_source_artifact = {
        key: value
        for key, value in sorted(candidate_counts_by_source_artifact.items())
        if value > 0
    }
    candidate_counts_by_type = _merge_counts(
        cross_summary["candidate_counts_by_type"],
        review_summary["candidate_counts_by_type"],
        lexical_summary["candidate_counts_by_type"],
        topic_summary_summary["candidate_counts_by_type"],
    )
    lifecycle_proxy_counts = _merge_counts(
        cross_summary["lifecycle_proxy_counts"],
        review_summary["lifecycle_proxy_counts"],
        topic_summary_summary["lifecycle_proxy_counts"],
    )
    risk_counts = _merge_counts(
        cross_summary["risk_counts"],
        review_summary["risk_counts"],
    )

    return {
        "artifact_type": TOPIC_LIFECYCLE_ARTIFACT_TYPE,
        "schema_version": TOPIC_LIFECYCLE_SCHEMA_VERSION,
        "diagnostics_mode": "candidate_lifecycle_proxy_only",
        "limitation": (
            "True topic lifecycle states are not inferred in this version. "
            "Counts are conservative candidate-lifecycle proxy signals from "
            "existing candidate and topic-summary artifacts."
        ),
        "candidate_count": len(cross_rows) + len(review_rows) + len(lexical_rows),
        "candidate_counts_by_source_artifact": candidate_counts_by_source_artifact,
        "candidate_counts_by_type": candidate_counts_by_type,
        "lifecycle_proxy_counts": lifecycle_proxy_counts,
        "risk_counts": risk_counts,
        "reason_code_counts": _merge_counts(
            cross_summary["reason_code_counts"],
            review_summary["reason_code_counts"],
            lexical_summary["reason_code_counts"],
        ),
        "source_inputs": source_inputs,
        "warnings": warnings,
        "cross_thread_candidates": cross_summary,
        "review_queue_candidates": review_summary,
        "lexical_candidates": lexical_summary,
        "topic_summaries": topic_summary_summary,
    }


def _load_source_rows(
    path: Path,
    source_artifact: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
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


def _load_globbed_source_rows(
    provider_root: Path,
    source_artifact: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    paths = sorted(provider_root.glob(source_artifact))
    if not paths:
        return [], {
            "source_artifact": source_artifact,
            "path": str(provider_root / source_artifact),
            "status": "missing",
            "candidate_count": 0,
            "files_found": 0,
        }
    rows: list[dict[str, Any]] = []
    for path in paths:
        rows.extend(_read_jsonl(path))
    return rows, {
        "source_artifact": source_artifact,
        "path": source_artifact,
        "status": "loaded",
        "candidate_count": len(rows),
        "files_found": len(paths),
        "files": [str(path) for path in paths],
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
                raise TopicLifecycleError(
                    f"invalid JSON in {path}:{line_no}: {exc.msg}"
                ) from exc
            if not isinstance(payload, dict):
                raise TopicLifecycleError(f"invalid JSON object in {path}:{line_no}")
            rows.append(payload)
    return rows


def _warn_if_missing(warnings: list[str], source_input: dict[str, Any]) -> None:
    if source_input["status"] == "missing":
        warnings.append(f"missing source artifact: {source_input['source_artifact']}")


def _summarize_cross_thread(rows: list[dict[str, Any]]) -> dict[str, Any]:
    reason_counts: Counter[str] = Counter()
    risk_counts: Counter[str] = Counter()
    lifecycle_counts: Counter[str] = Counter()

    for row in rows:
        reason_codes = _reason_codes(row)
        reason_counts.update(reason_codes)
        lifecycle_counts["cross_thread_link_candidate"] += 1
        if _is_low_score(row):
            risk_counts["low_score"] += 1
            lifecycle_counts["weak_candidate"] += 1
        if row.get("continuity_mask") is True or _nested_get(row, "evidence", "continuity_mask") is True:
            risk_counts["continuity_mask"] += 1
            lifecycle_counts["continuity_masked"] += 1
        if _is_recurring_proxy(row, reason_codes):
            lifecycle_counts["recurring_or_resurfaced_proxy"] += 1
        if _is_stale_proxy(row, reason_codes):
            lifecycle_counts["stale_or_dormant_proxy"] += 1

    return {
        "source_artifact": CROSS_THREAD_SOURCE,
        "candidate_count": len(rows),
        "candidate_counts_by_type": {"cross_thread_link": len(rows)} if rows else {},
        "reason_code_counts": dict(sorted(reason_counts.items())),
        "risk_counts": dict(sorted(risk_counts.items())),
        "lifecycle_proxy_counts": dict(sorted(lifecycle_counts.items())),
        "low_score_candidate_count": risk_counts.get("low_score", 0),
        "continuity_mask_candidate_count": risk_counts.get("continuity_mask", 0),
        "recurring_or_resurfaced_proxy_count": lifecycle_counts.get(
            "recurring_or_resurfaced_proxy",
            0,
        ),
    }


def _summarize_review_queue(rows: list[dict[str, Any]]) -> dict[str, Any]:
    candidate_type_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    risk_counts: Counter[str] = Counter()
    lifecycle_counts: Counter[str] = Counter()

    for row in rows:
        candidate_type = str(row.get("candidate_type") or "unknown")
        candidate_type_counts[candidate_type] += 1
        reason_counts.update(_diagnostic_reason_codes(row))
        risk_flags = row.get("risk_flags")
        if isinstance(risk_flags, list):
            for flag in risk_flags:
                key = str(flag)
                if key:
                    risk_counts[key] += 1
                    if "low_score" in key:
                        lifecycle_counts["weak_candidate"] += 1
                    if "continuity" in key:
                        lifecycle_counts["continuity_masked"] += 1
        if candidate_type == "cross_thread_link":
            lifecycle_counts["review_queue_cross_thread_link"] += 1

    return {
        "source_artifact": REVIEW_QUEUE_SOURCE,
        "candidate_count": len(rows),
        "candidate_counts_by_type": dict(sorted(candidate_type_counts.items())),
        "reason_code_counts": dict(sorted(reason_counts.items())),
        "risk_counts": dict(sorted(risk_counts.items())),
        "lifecycle_proxy_counts": dict(sorted(lifecycle_counts.items())),
    }


def _summarize_lexical(rows: list[dict[str, Any]]) -> dict[str, Any]:
    candidate_type_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    for row in rows:
        candidate_type_counts[str(row.get("candidate_type") or "unknown")] += 1
        reason_counts.update(_reason_codes(row))
    return {
        "source_artifact": LEXICAL_SOURCE,
        "candidate_count": len(rows),
        "candidate_counts_by_type": dict(sorted(candidate_type_counts.items())),
        "reason_code_counts": dict(sorted(reason_counts.items())),
    }


def _summarize_topic_summaries(rows: list[dict[str, Any]]) -> dict[str, Any]:
    lifecycle_counts: Counter[str] = Counter()
    conclusion_counts: Counter[str] = Counter()
    for row in rows:
        status = str(row.get("conclusion_status") or "unknown")
        conclusion_counts[status] += 1
        if status in {"open", "unresolved", "unknown"}:
            lifecycle_counts[f"topic_summary_{status}"] += 1
    return {
        "source_artifact": TOPIC_SUMMARIES_SOURCE,
        "row_count": len(rows),
        "candidate_counts_by_type": {"topic_summary": len(rows)} if rows else {},
        "conclusion_status_counts": dict(sorted(conclusion_counts.items())),
        "lifecycle_proxy_counts": dict(sorted(lifecycle_counts.items())),
    }


def _reason_codes(row: dict[str, Any]) -> list[str]:
    evidence = row.get("evidence")
    if not isinstance(evidence, dict):
        return []
    reason_codes = evidence.get("reason_codes")
    if not isinstance(reason_codes, list):
        return []
    return [str(code) for code in reason_codes if str(code)]


def _diagnostic_reason_codes(row: dict[str, Any]) -> list[str]:
    diagnostics = row.get("diagnostics")
    if not isinstance(diagnostics, dict):
        return []
    reason_codes = diagnostics.get("reason_codes")
    if not isinstance(reason_codes, list):
        return []
    return [str(code) for code in reason_codes if str(code)]


def _is_low_score(row: dict[str, Any]) -> bool:
    score = row.get("score")
    return isinstance(score, (int, float)) and score < LOW_SCORE_THRESHOLD


def _is_recurring_proxy(row: dict[str, Any], reason_codes: list[str]) -> bool:
    if _positive_number(row.get("temporal_gap_seconds")):
        return True
    if _positive_number(_nested_get(row, "evidence", "temporal_gap_seconds")):
        return True
    return any(
        "recurr" in code
        or "resurface" in code
        or "timestamp_distance" in code
        or "dormant" in code
        for code in reason_codes
    )


def _is_stale_proxy(row: dict[str, Any], reason_codes: list[str]) -> bool:
    if _positive_number(row.get("dormancy_score")):
        return True
    if _positive_number(_nested_get(row, "evidence", "dormancy_score")):
        return True
    return any("dormant" in code or "stale" in code for code in reason_codes)


def _positive_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and value > 0


def _nested_get(value: dict[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _merge_counts(*counts: dict[str, int]) -> dict[str, int]:
    merged: Counter[str] = Counter()
    for count_map in counts:
        merged.update(count_map)
    return dict(sorted(merged.items()))


def render_topic_lifecycle_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Topic Lifecycle Diagnostics",
        "",
        "> Diagnostics only. This artifact does not change candidate scoring,",
        "> topic segmentation, suppression, review queue behavior, policy files,",
        "> or L4 outputs.",
        "",
        f"- diagnostics_mode: {report['diagnostics_mode']}",
        f"- candidate_count: {report['candidate_count']}",
        f"- limitation: {report['limitation']}",
        "",
        "## Candidate Types",
        "",
    ]
    _append_count_lines(lines, report.get("candidate_counts_by_type", {}))
    lines.extend(["", "## Source Artifacts", ""])
    _append_count_lines(lines, report.get("candidate_counts_by_source_artifact", {}))
    lines.extend(["", "## Lifecycle Proxy Counts", ""])
    _append_count_lines(lines, report.get("lifecycle_proxy_counts", {}))
    lines.extend(["", "## Risk Counts", ""])
    _append_count_lines(lines, report.get("risk_counts", {}))
    lines.extend(["", "## Source Inputs", ""])
    for source in report.get("source_inputs", []):
        suffix = ""
        if "files_found" in source:
            suffix = f", files={source['files_found']}"
        lines.append(
            f"- {source['source_artifact']}: {source['status']} "
            f"({source['candidate_count']} row(s){suffix})"
        )
    lines.extend(["", "## Warnings", ""])
    _append_list_lines(lines, report.get("warnings", []))
    return "\n".join(lines).rstrip() + "\n"


def _append_count_lines(lines: list[str], counts: dict[str, Any]) -> None:
    if counts:
        for key, count in sorted(counts.items()):
            lines.append(f"- {key}: {count}")
    else:
        lines.append("- none")


def _append_list_lines(lines: list[str], values: list[str]) -> None:
    if values:
        for value in values:
            lines.append(f"- {value}")
    else:
        lines.append("- none")


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
