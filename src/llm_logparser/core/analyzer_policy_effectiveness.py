from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


POLICY_EFFECTIVENESS_SCHEMA_VERSION = "0.1"
POLICY_EFFECTIVENESS_ARTIFACT_TYPE = "policy_effectiveness_diagnostics"
LEXICAL_SOURCE = "l3/lexical-rules/candidates.jsonl"
CROSS_THREAD_SOURCE = "l3/cross-thread-candidates/candidates.jsonl"
LOW_CROSS_THREAD_SCORE_THRESHOLD = 0.45


class PolicyEffectivenessError(RuntimeError):
    pass


def policy_effectiveness_dir(input_root: Path) -> Path:
    return input_root / "l3" / "diagnostics"


def policy_effectiveness_json_path(input_root: Path) -> Path:
    return policy_effectiveness_dir(input_root) / "policy_effectiveness.json"


def policy_effectiveness_markdown_path(input_root: Path) -> Path:
    return policy_effectiveness_dir(input_root) / "policy_effectiveness.md"


def write_policy_effectiveness_artifacts(
    input_root: Path,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    provider_root = input_root.expanduser()
    if not provider_root.exists() or not provider_root.is_dir():
        raise PolicyEffectivenessError(f"provider root not found: {provider_root}")

    output_paths = [
        policy_effectiveness_json_path(provider_root),
        policy_effectiveness_markdown_path(provider_root),
    ]
    existing_outputs = [path for path in output_paths if path.exists()]
    if existing_outputs and not overwrite:
        raise PolicyEffectivenessError(
            "policy effectiveness diagnostics already exist; rerun with --overwrite: "
            + ", ".join(str(path) for path in existing_outputs)
        )

    report = build_policy_effectiveness_report(provider_root)
    markdown = render_policy_effectiveness_markdown(report)

    output_dir = policy_effectiveness_dir(provider_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(policy_effectiveness_json_path(provider_root), report)
    _write_text(policy_effectiveness_markdown_path(provider_root), markdown)

    return {
        "candidate_count": report["candidate_count"],
        "warnings": report["warnings"],
        "json_path": policy_effectiveness_json_path(provider_root),
        "markdown_path": policy_effectiveness_markdown_path(provider_root),
    }


def build_policy_effectiveness_report(provider_root: Path) -> dict[str, Any]:
    source_inputs: list[dict[str, Any]] = []
    warnings: list[str] = []

    lexical_rows, lexical_input = _load_source_rows(
        provider_root / LEXICAL_SOURCE,
        LEXICAL_SOURCE,
    )
    source_inputs.append(lexical_input)
    if lexical_input["status"] == "missing":
        warnings.append(f"missing source artifact: {LEXICAL_SOURCE}")

    cross_rows, cross_input = _load_source_rows(
        provider_root / CROSS_THREAD_SOURCE,
        CROSS_THREAD_SOURCE,
    )
    source_inputs.append(cross_input)
    if cross_input["status"] == "missing":
        warnings.append(f"missing source artifact: {CROSS_THREAD_SOURCE}")

    lexical_summary = _summarize_lexical_candidates(lexical_rows)
    cross_thread_summary = _summarize_cross_thread_candidates(cross_rows)
    all_reason_counts = _counter_dict(
        list(lexical_summary["reason_code_counts"].items())
        + list(cross_thread_summary["reason_code_counts"].items())
    )

    candidate_counts_by_type = _merge_counts(
        lexical_summary["candidate_counts_by_type"],
        cross_thread_summary["candidate_counts_by_type"],
    )

    return {
        "artifact_type": POLICY_EFFECTIVENESS_ARTIFACT_TYPE,
        "schema_version": POLICY_EFFECTIVENESS_SCHEMA_VERSION,
        "policy_effect": "diagnostics_only_no_policy_mutation",
        "candidate_count": len(lexical_rows) + len(cross_rows),
        "candidate_counts_by_type": candidate_counts_by_type,
        "reason_code_counts": all_reason_counts,
        "source_inputs": source_inputs,
        "warnings": warnings,
        "lexical_candidates": lexical_summary,
        "cross_thread_candidates": cross_thread_summary,
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
                raise PolicyEffectivenessError(
                    f"invalid JSON in {path}:{line_no}: {exc.msg}"
                ) from exc
            if not isinstance(payload, dict):
                raise PolicyEffectivenessError(f"invalid JSON object in {path}:{line_no}")
            rows.append(payload)
    return rows


def _summarize_lexical_candidates(rows: list[dict[str, Any]]) -> dict[str, Any]:
    candidate_type_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    risk_counts: Counter[str] = Counter()
    already_active_count = 0

    for row in rows:
        candidate_type = str(row.get("candidate_type") or "unknown")
        candidate_type_counts[candidate_type] += 1
        reason_counts.update(_reason_codes(row))
        if row.get("already_active") is True:
            already_active_count += 1
            risk_counts["already_active_policy"] += 1
        if "persona" in candidate_type:
            risk_counts["persona_token_candidate"] += 1
        if "generic" in candidate_type:
            risk_counts["generic_token_candidate"] += 1
        if row.get("value_kind") == "token" or candidate_type.endswith("_token"):
            risk_counts["token_candidate"] += 1

    return {
        "source_artifact": LEXICAL_SOURCE,
        "candidate_count": len(rows),
        "candidate_counts_by_type": dict(sorted(candidate_type_counts.items())),
        "reason_code_counts": dict(sorted(reason_counts.items())),
        "already_active_policy_candidate_count": already_active_count,
        "persona_generic_token_risk_counts": dict(sorted(risk_counts.items())),
    }


def _summarize_cross_thread_candidates(rows: list[dict[str, Any]]) -> dict[str, Any]:
    reason_counts: Counter[str] = Counter()
    risk_counts: Counter[str] = Counter()

    for row in rows:
        reason_counts.update(_reason_codes(row))
        score = row.get("score")
        if isinstance(score, (int, float)) and score < LOW_CROSS_THREAD_SCORE_THRESHOLD:
            risk_counts["low_score"] += 1
        if row.get("continuity_mask") is True:
            risk_counts["continuity_mask"] += 1

    candidate_counts_by_type = {"cross_thread_link": len(rows)} if rows else {}
    return {
        "source_artifact": CROSS_THREAD_SOURCE,
        "candidate_count": len(rows),
        "candidate_counts_by_type": candidate_counts_by_type,
        "reason_code_counts": dict(sorted(reason_counts.items())),
        "low_score_candidate_count": risk_counts.get("low_score", 0),
        "continuity_mask_candidate_count": risk_counts.get("continuity_mask", 0),
        "risk_counts": dict(sorted(risk_counts.items())),
    }


def _reason_codes(row: dict[str, Any]) -> list[str]:
    evidence = row.get("evidence")
    if not isinstance(evidence, dict):
        return []
    reason_codes = evidence.get("reason_codes")
    if not isinstance(reason_codes, list):
        return []
    return [str(code) for code in reason_codes if str(code)]


def _merge_counts(*counts: dict[str, int]) -> dict[str, int]:
    merged: Counter[str] = Counter()
    for count_map in counts:
        merged.update(count_map)
    return dict(sorted(merged.items()))


def _counter_dict(items: Iterable[tuple[str, int]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for key, count in items:
        counter[str(key)] += int(count)
    return dict(sorted(counter.items()))


def render_policy_effectiveness_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Policy Effectiveness Diagnostics",
        "",
        "> Diagnostics only. This artifact does not activate, accept, reject,",
        "> promote, suppress, or mutate policy. It summarizes existing candidate",
        "> artifacts without changing scoring behavior.",
        "",
        f"- candidate_count: {report['candidate_count']}",
        "",
        "## Candidate Types",
        "",
    ]
    _append_count_lines(lines, report.get("candidate_counts_by_type", {}))
    lines.extend(["", "## Source Inputs", ""])
    for source in report.get("source_inputs", []):
        lines.append(
            f"- {source['source_artifact']}: {source['status']} "
            f"({source['candidate_count']} candidate(s))"
        )
    lines.extend(["", "## Warnings", ""])
    _append_list_lines(lines, report.get("warnings", []))

    lexical = report.get("lexical_candidates", {})
    lines.extend(["", "## Lexical Candidates", ""])
    lines.append(
        "- already_active_policy_candidate_count: "
        f"{lexical.get('already_active_policy_candidate_count', 0)}"
    )
    lines.append("- persona_generic_token_risk_counts:")
    _append_count_lines(
        lines,
        lexical.get("persona_generic_token_risk_counts", {}),
        indent="  ",
    )
    lines.append("- reason_code_counts:")
    _append_count_lines(lines, lexical.get("reason_code_counts", {}), indent="  ")

    cross_thread = report.get("cross_thread_candidates", {})
    lines.extend(["", "## Cross-Thread Candidates", ""])
    lines.append(
        "- low_score_candidate_count: "
        f"{cross_thread.get('low_score_candidate_count', 0)}"
    )
    lines.append(
        "- continuity_mask_candidate_count: "
        f"{cross_thread.get('continuity_mask_candidate_count', 0)}"
    )
    lines.append("- risk_counts:")
    _append_count_lines(lines, cross_thread.get("risk_counts", {}), indent="  ")
    lines.append("- reason_code_counts:")
    _append_count_lines(lines, cross_thread.get("reason_code_counts", {}), indent="  ")
    return "\n".join(lines).rstrip() + "\n"


def _append_count_lines(
    lines: list[str],
    counts: dict[str, Any],
    *,
    indent: str = "",
) -> None:
    if counts:
        for key, count in sorted(counts.items()):
            lines.append(f"{indent}- {key}: {count}")
    else:
        lines.append(f"{indent}- none")


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
