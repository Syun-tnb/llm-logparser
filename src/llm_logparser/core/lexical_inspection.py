from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from llm_logparser.core.analyzer_common import normalize_analysis_text
from llm_logparser.core.analyzer_lexical_rule_candidates import (
    lexical_rule_candidate_diagnostics_path,
    lexical_rule_candidate_review_path,
    lexical_rule_candidates_path,
)
from llm_logparser.core.analyzer_token_dictionary import (
    legacy_token_dictionary_path,
    observed_tokens_path,
    resolve_existing_token_dictionary_path,
    token_bundles_path,
    token_dictionary_provenance_path,
)
from llm_logparser.core.schema_validation import (
    load_token_bundles_validator,
    load_token_dictionary_provenance_validator,
    load_token_dictionary_validator,
)


class LexicalInspectionError(RuntimeError):
    pass


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LexicalInspectionError(f"invalid JSON in {label} {path}: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise LexicalInspectionError(f"{label} must contain a JSON object: {path}")
    return payload


def _short(value: str, *, limit: int = 120) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: limit - 3].rstrip()}..."


def _escape_markdown_table_cell(value: Any) -> str:
    """Escape compact Markdown table/list cell text without changing JSON data."""

    return (
        str(value)
        .replace("|", r"\|")
        .replace("\r\n", "<br>")
        .replace("\n", "<br>")
        .replace("\r", "<br>")
    )


def _token_key(value: str) -> str:
    return normalize_analysis_text(value)


def load_observed_token_artifacts(provider_root: Path) -> dict[str, Any]:
    root = provider_root.expanduser()
    if not root.exists() or not root.is_dir():
        raise LexicalInspectionError(f"provider root not found: {root}")

    token_path = resolve_existing_token_dictionary_path(root)
    if not token_path.exists():
        raise LexicalInspectionError(
            "observed token artifact not found: "
            f"{observed_tokens_path(root)} "
            f"(legacy alias: {legacy_token_dictionary_path(root)})"
        )
    tokens_payload = _read_json(token_path, label="observed token artifact")
    errors = list(load_token_dictionary_validator().iter_errors(tokens_payload))
    if errors:
        raise LexicalInspectionError(
            f"observed token schema validation failed for {token_path}: {errors[0].message}"
        )

    bundles_payload: dict[str, Any] | None = None
    bundles_path = token_bundles_path(root)
    if bundles_path.exists():
        bundles_payload = _read_json(bundles_path, label="token bundles")
        bundle_errors = list(load_token_bundles_validator().iter_errors(bundles_payload))
        if bundle_errors:
            raise LexicalInspectionError(
                f"token bundles schema validation failed for {bundles_path}: "
                f"{bundle_errors[0].message}"
            )

    provenance_payload: dict[str, Any] | None = None
    provenance_path = token_dictionary_provenance_path(root)
    if provenance_path.exists():
        provenance_payload = _read_json(provenance_path, label="token dictionary provenance")
        provenance_errors = list(
            load_token_dictionary_provenance_validator().iter_errors(provenance_payload)
        )
        if provenance_errors:
            raise LexicalInspectionError(
                f"token dictionary provenance schema validation failed for {provenance_path}: "
                f"{provenance_errors[0].message}"
            )

    return {
        "provider_root": str(root),
        "observed_tokens_path": str(token_path),
        "observed_tokens_source": (
            "observed_tokens" if token_path.name == "observed_tokens.json" else "legacy_dictionary"
        ),
        "observed_tokens": tokens_payload,
        "bundles_path": str(bundles_path) if bundles_payload is not None else None,
        "bundles": bundles_payload,
        "provenance_path": str(provenance_path) if provenance_payload is not None else None,
        "provenance": provenance_payload,
    }


def list_observed_tokens(
    provider_root: Path,
    *,
    limit: int = 20,
) -> dict[str, Any]:
    artifacts = load_observed_token_artifacts(provider_root)
    tokens = sorted(
        artifacts["observed_tokens"].get("tokens", []),
        key=lambda row: (
            -int(row.get("count", 0)),
            -int(row.get("conversation_count", 0)),
            str(row.get("normalized", row.get("token", ""))),
        ),
    )
    rows = [
        {
            "token": row.get("token"),
            "normalized": row.get("normalized"),
            "count": row.get("count"),
            "conversation_count": row.get("conversation_count"),
            "topic_count": row.get("topic_count"),
        }
        for row in tokens[: max(0, limit)]
    ]
    payload = artifacts["observed_tokens"]
    return {
        "artifact_type": "observed_token_list",
        "schema_version": "0.1",
        "provider_id": payload.get("provider_id", "unknown"),
        "observed_tokens_source": artifacts["observed_tokens_source"],
        "observed_tokens_path": artifacts["observed_tokens_path"],
        "token_count": payload.get("token_count", len(tokens)),
        "returned_count": len(rows),
        "tokens": rows,
        "provenance_summary": _observed_provenance_summary(artifacts),
    }


def inspect_observed_token(provider_root: Path, *, token: str) -> dict[str, Any]:
    artifacts = load_observed_token_artifacts(provider_root)
    key = _token_key(token)
    row = next(
        (
            item
            for item in artifacts["observed_tokens"].get("tokens", [])
            if _token_key(str(item.get("normalized") or item.get("token") or "")) == key
            or _token_key(str(item.get("token") or "")) == key
        ),
        None,
    )
    if row is None:
        raise LexicalInspectionError(f"observed token not found: {token}")

    bundles = _bundle_evidence_for_token(artifacts.get("bundles"), key)
    payload = artifacts["observed_tokens"]
    return {
        "artifact_type": "observed_token_inspection",
        "schema_version": "0.1",
        "provider_id": payload.get("provider_id", "unknown"),
        "observed_tokens_source": artifacts["observed_tokens_source"],
        "observed_tokens_path": artifacts["observed_tokens_path"],
        "query": token,
        "token": row.get("token"),
        "normalized": row.get("normalized"),
        "count": row.get("count"),
        "conversation_count": row.get("conversation_count"),
        "topic_count": row.get("topic_count"),
        "conversations": row.get("conversations", []),
        "topics": row.get("topics", []),
        "role_hints": row.get("role_hints", {}),
        "cooccurrence": row.get("cooccurrence", []),
        "bundle_evidence": bundles,
        "provenance_summary": _observed_provenance_summary(artifacts),
        "notes": [
            "Observed tokens are corpus facts only.",
            "This inspection does not classify, score, or activate lexical policy.",
        ],
    }


def _observed_provenance_summary(artifacts: dict[str, Any]) -> dict[str, Any]:
    payload = artifacts["provenance"] or artifacts["observed_tokens"]
    return {
        "provider_id": payload.get("provider_id", "unknown"),
        "created_at": payload.get("created_at"),
        "source_inputs": payload.get("source_inputs", []),
        "token_count": payload.get("token_count", artifacts["observed_tokens"].get("token_count")),
        "bundle_count": payload.get(
            "bundle_count",
            (artifacts.get("bundles") or {}).get("bundle_count", 0),
        ),
    }


def _bundle_evidence_for_token(
    bundles_payload: dict[str, Any] | None,
    token_key: str,
) -> list[dict[str, Any]]:
    if not bundles_payload:
        return []
    rows: list[dict[str, Any]] = []
    for bundle in bundles_payload.get("bundles", []):
        tokens = bundle.get("tokens", [])
        if not isinstance(tokens, list):
            continue
        if token_key not in {_token_key(str(item)) for item in tokens}:
            continue
        rows.append(
            {
                "bundle_id": bundle.get("bundle_id"),
                "tokens": tokens,
                "weight": bundle.get("weight"),
            }
        )
    return sorted(rows, key=lambda item: (-float(item.get("weight") or 0), item["bundle_id"]))


def load_lexical_candidate_artifacts(provider_root: Path) -> dict[str, Any]:
    root = provider_root.expanduser()
    if not root.exists() or not root.is_dir():
        raise LexicalInspectionError(f"provider root not found: {root}")
    candidates_path = lexical_rule_candidates_path(root)
    if not candidates_path.exists():
        raise LexicalInspectionError(f"lexical-rule candidates not found: {candidates_path}")

    rows: list[dict[str, Any]] = []
    with candidates_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise LexicalInspectionError(
                    f"invalid JSON in {candidates_path}:{line_number}: {exc.msg}"
                ) from exc
            if not isinstance(row, dict):
                raise LexicalInspectionError(
                    f"lexical-rule candidate row must be an object: {candidates_path}:{line_number}"
                )
            rows.append(row)

    diagnostics_path = lexical_rule_candidate_diagnostics_path(root)
    diagnostics = (
        _read_json(diagnostics_path, label="lexical-rule candidate diagnostics")
        if diagnostics_path.exists()
        else None
    )
    review_path = lexical_rule_candidate_review_path(root)
    return {
        "provider_root": str(root),
        "candidates_path": str(candidates_path),
        "diagnostics_path": str(diagnostics_path) if diagnostics is not None else None,
        "review_path": str(review_path) if review_path.exists() else None,
        "candidates": rows,
        "diagnostics": diagnostics,
    }


def list_lexical_candidates(
    provider_root: Path,
    *,
    limit: int = 20,
) -> dict[str, Any]:
    artifacts = load_lexical_candidate_artifacts(provider_root)
    rows = sorted(
        artifacts["candidates"],
        key=lambda row: (
            -float(row.get("review", {}).get("score", row.get("score", 0)) or 0),
            str(row.get("candidate_type", "")),
            str(row.get("normalized_value", row.get("value", ""))),
            str(row.get("candidate_id", "")),
        ),
    )
    candidates = [
        {
            "candidate_id": row.get("candidate_id"),
            "candidate_type": row.get("candidate_type"),
            "value": row.get("value"),
            "normalized_value": row.get("normalized_value"),
            "suggested_rule_path": row.get("suggested_rule_path"),
            "status": row.get("status"),
            "activation_state": row.get("activation_state"),
            "already_active": row.get("already_active"),
            "score": row.get("review", {}).get("score", row.get("score")),
            "reason_codes": row.get("review", {}).get("reason_codes", []),
        }
        for row in rows[: max(0, limit)]
    ]
    diagnostics = artifacts.get("diagnostics") or {}
    return {
        "artifact_type": "lexical_rule_candidate_list",
        "schema_version": "0.1",
        "provider_id": diagnostics.get("provider_id", "unknown"),
        "candidate_count": len(rows),
        "returned_count": len(candidates),
        "candidate_type_counts": diagnostics.get("candidate_type_counts", {}),
        "candidates": candidates,
        "diagnostics_summary": _candidate_diagnostics_summary(diagnostics),
    }


def inspect_lexical_candidate(
    provider_root: Path,
    *,
    candidate_id: str,
) -> dict[str, Any]:
    artifacts = load_lexical_candidate_artifacts(provider_root)
    row = next(
        (
            item
            for item in artifacts["candidates"]
            if str(item.get("candidate_id")) == candidate_id
        ),
        None,
    )
    if row is None:
        raise LexicalInspectionError(f"lexical-rule candidate not found: {candidate_id}")
    diagnostics = artifacts.get("diagnostics") or {}
    return {
        "artifact_type": "lexical_rule_candidate_inspection",
        "schema_version": "0.1",
        "provider_id": row.get("provider_id", diagnostics.get("provider_id", "unknown")),
        "candidate_id": row.get("candidate_id"),
        "candidate_type": row.get("candidate_type"),
        "value": row.get("value"),
        "normalized_value": row.get("normalized_value"),
        "suggested_scope": row.get("suggested_scope"),
        "suggested_rule_path": row.get("suggested_rule_path"),
        "status": row.get("status"),
        "activation_state": row.get("activation_state"),
        "already_active": row.get("already_active"),
        "source": row.get("source", {}),
        "evidence": row.get("evidence", {}),
        "sample_refs": row.get("sample_refs", []),
        "review": row.get("review", {}),
        "diagnostics_summary": _candidate_diagnostics_summary(diagnostics),
        "notes": [
            "Lexical-rule candidates are inactive suggestions only.",
            "This inspection does not promote, reject, or modify reviewed policy.",
        ],
    }


def _candidate_diagnostics_summary(diagnostics: dict[str, Any]) -> dict[str, Any]:
    if not diagnostics:
        return {"status": "not_found"}
    active_policy = diagnostics.get("active_policy", {})
    return {
        "status": "loaded",
        "candidate_count": diagnostics.get("candidate_count"),
        "candidate_type_counts": diagnostics.get("candidate_type_counts", {}),
        "skipped_counts": diagnostics.get("skipped_counts", {}),
        "topic_summaries": diagnostics.get("topic_summaries", {}),
        "active_policy": {
            "rule_family": active_policy.get("rule_family"),
            "schema_version": active_policy.get("schema_version"),
            "resolved_locale": active_policy.get("resolved_locale"),
            "project_rules": active_policy.get("project_rules", {}),
            "user_rules": active_policy.get("user_rules", {}),
            "category_counts": active_policy.get("category_counts", {}),
        },
        "notes": diagnostics.get("notes", []),
    }


def render_lexical_inspection_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def render_observed_token_list_text(payload: dict[str, Any]) -> str:
    lines = [
        "Observed tokens",
        f"- provider_id: {payload['provider_id']}",
        f"- token_count: {payload['token_count']}",
        f"- returned_count: {payload['returned_count']}",
        f"- source: {payload['observed_tokens_source']}",
        "",
        "| token | count | conversations | topics |",
        "|---|---:|---:|---:|",
    ]
    for row in payload["tokens"]:
        lines.append(
            "| "
            f"{_escape_markdown_table_cell(row['token'])} | "
            f"{_escape_markdown_table_cell(row['count'])} | "
            f"{_escape_markdown_table_cell(row['conversation_count'])} | "
            f"{_escape_markdown_table_cell(row['topic_count'])} |"
        )
    return "\n".join(lines)


def render_observed_token_inspection_text(payload: dict[str, Any]) -> str:
    lines = [
        "Observed token inspection",
        f"- token: {_escape_markdown_table_cell(payload['token'])}",
        f"- normalized: {_escape_markdown_table_cell(payload['normalized'])}",
        f"- count: {payload['count']}",
        f"- conversation_count: {payload['conversation_count']}",
        f"- topic_count: {payload['topic_count']}",
        f"- source: {_escape_markdown_table_cell(payload['observed_tokens_source'])}",
        "- cooccurrence:",
    ]
    cooccurrence = payload.get("cooccurrence", [])
    lines.extend(f"  - {_escape_markdown_table_cell(item)}" for item in cooccurrence[:10])
    if not cooccurrence:
        lines.append("  - none")
    lines.append("- bundle_evidence:")
    bundles = payload.get("bundle_evidence", [])
    for bundle in bundles[:10]:
        lines.append(
            f"  - {_escape_markdown_table_cell(bundle['bundle_id'])}: "
            f"{_escape_markdown_table_cell(', '.join(bundle['tokens']))} "
            f"(weight={_escape_markdown_table_cell(bundle['weight'])})"
        )
    if not bundles:
        lines.append("  - none")
    provenance = payload.get("provenance_summary", {})
    lines.append("- provenance:")
    lines.append(
        f"  - source_inputs: {_escape_markdown_table_cell(', '.join(provenance.get('source_inputs', [])))}"
    )
    lines.append(f"  - created_at: {_escape_markdown_table_cell(provenance.get('created_at'))}")
    return "\n".join(lines)


def render_lexical_candidate_list_text(payload: dict[str, Any]) -> str:
    lines = [
        "Lexical-rule candidates",
        f"- provider_id: {payload['provider_id']}",
        f"- candidate_count: {payload['candidate_count']}",
        f"- returned_count: {payload['returned_count']}",
        "",
        "| candidate_id | type | value | suggested_rule_path | score | status | active_conflict |",
        "|---|---|---|---|---:|---|---|",
    ]
    for row in payload["candidates"]:
        lines.append(
            "| "
            f"{_escape_markdown_table_cell(row['candidate_id'])} | "
            f"{_escape_markdown_table_cell(row['candidate_type'])} | "
            f"{_escape_markdown_table_cell(row['value'])} | "
            f"{_escape_markdown_table_cell(row['suggested_rule_path'])} | "
            f"{_escape_markdown_table_cell(row['score'])} | "
            f"{_escape_markdown_table_cell(row['status'])} | "
            f"{_escape_markdown_table_cell(row['already_active'])} |"
        )
    return "\n".join(lines)


def render_lexical_candidate_inspection_text(payload: dict[str, Any]) -> str:
    review = payload.get("review", {})
    evidence = payload.get("evidence", {})
    lines = [
        "Lexical-rule candidate inspection",
        f"- candidate_id: {_escape_markdown_table_cell(payload['candidate_id'])}",
        f"- candidate_type: {_escape_markdown_table_cell(payload['candidate_type'])}",
        f"- value: {_escape_markdown_table_cell(payload['value'])}",
        f"- normalized_value: {_escape_markdown_table_cell(payload['normalized_value'])}",
        f"- suggested_rule_path: {_escape_markdown_table_cell(payload['suggested_rule_path'])}",
        f"- status: {_escape_markdown_table_cell(payload['status'])}",
        f"- activation_state: {_escape_markdown_table_cell(payload['activation_state'])}",
        f"- already_active: {_escape_markdown_table_cell(payload['already_active'])}",
        f"- score: {_escape_markdown_table_cell(review.get('score'))}",
        "- reason_codes:",
    ]
    reason_codes = review.get("reason_codes", [])
    lines.extend(f"  - {_escape_markdown_table_cell(item)}" for item in reason_codes)
    if not reason_codes:
        lines.append("  - none")
    lines.append("- evidence:")
    for key in (
        "token_count",
        "document_count",
        "conversation_count",
        "topic_count",
        "bundle_count",
        "topic_summary_total_count",
    ):
        if key in evidence:
            lines.append(
                f"  - {_escape_markdown_table_cell(key)}: "
                f"{_escape_markdown_table_cell(evidence[key])}"
            )
    sample_refs = payload.get("sample_refs", [])
    lines.append("- sample_refs:")
    for ref in sample_refs[:5]:
        field = ref.get("field", "sample")
        excerpt = _short(str(ref.get("excerpt", "")))
        lines.append(
            f"  - {_escape_markdown_table_cell(field)}: "
            f"{_escape_markdown_table_cell(excerpt)}"
        )
    if not sample_refs:
        lines.append("  - none")
    return "\n".join(lines)
