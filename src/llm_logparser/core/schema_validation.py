# src/llm_logparser/core/schema_validation.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Optional, Sequence, TYPE_CHECKING

import json

if TYPE_CHECKING:
    from jsonschema import ValidationError


def _import_jsonschema_components():
    try:
        import jsonschema
        from jsonschema import ValidationError
        from jsonschema.validators import validator_for
    except ImportError as exc:
        raise RuntimeError(
            "jsonschema was not found. "
            "Please add `jsonschema` to the dependencies in pyproject.toml."
        ) from exc
    return jsonschema, ValidationError, validator_for


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def _default_schemas_root() -> Path:
    """
    Helper to infer the schemas directory,
    accounting for both development and installed environments.

    Expected layout:
        repo_root/
          schemas/
            message.schema.json
            manifest.schema.json
          src/
            llm_logparser/
              core/
                schema_validation.py  (this file)
    """
    here = Path(__file__).resolve()

    # Look for repo_root/schemas
    # .../src/llm_logparser/core/schema_validation.py
    # parents[0] core
    # parents[1] llm_logparser
    # parents[2] src
    # parents[3] repo_root
    candidates = [
        here.parents[0] / "schemas",       # src/llm_logparser/core/schemas
        here.parents[3] / "schemas",       # Development: repo_root/schemas
        here.parents[1] / "schemas",       # Installation: llm_logparser/schemas
    ]
    for path in candidates:
        if path.is_dir():
            return path

    # If not found, return the first candidate and leave it to error out
    return candidates[0]


SCHEMAS_ROOT: Path = _default_schemas_root()
MESSAGE_SCHEMA_NAME = "message.schema.json"
MANIFEST_SCHEMA_NAME = "manifest.schema.json"
TOKEN_STATS_SCHEMA_NAME = "token_stats.schema.json"
TOKEN_DICTIONARY_SCHEMA_NAME = "token_dictionary.schema.json"
TOKEN_BUNDLES_SCHEMA_NAME = "token_bundles.schema.json"
TOKEN_DICTIONARY_PROVENANCE_SCHEMA_NAME = "token_dictionary_provenance.schema.json"
METRICS_SCHEMA_NAME = "metrics.schema.json"
THREAD_STATS_SCHEMA_NAME = "thread_stats.schema.json"
MESSAGE_WINDOWS_SCHEMA_NAME = "message_windows.schema.json"
WINDOW_EMBEDDING_SCHEMA_NAME = "window_embedding.schema.json"
WINDOW_NEIGHBORS_SCHEMA_NAME = "window_neighbors.schema.json"
WINDOW_CLUSTERS_SCHEMA_NAME = "window_clusters.schema.json"
TOPICS_SCHEMA_NAME = "topics.schema.json"
TOPIC_MEMBERSHIP_SCHEMA_NAME = "topic_membership.schema.json"
SEMANTIC_NORMALIZATION_JOB_CONFIG_SCHEMA_NAME = (
    "semantic_normalization_job_config.schema.json"
)
SEMANTIC_NORMALIZATION_JOB_SPAN_SCHEMA_NAME = (
    "semantic_normalization_job_span.schema.json"
)
SEMANTIC_NORMALIZATION_JOB_RESULT_SCHEMA_NAME = (
    "semantic_normalization_job_result.schema.json"
)
SEMANTIC_NORMALIZATION_JOB_FAILURE_SCHEMA_NAME = (
    "semantic_normalization_job_failure.schema.json"
)
SEMANTIC_SPAN_PROPOSAL_SCHEMA_NAME = "semantic_span_proposal.schema.json"
CROSS_THREAD_CANDIDATE_SCHEMA_NAME = "cross_thread_candidate.schema.json"
CROSS_THREAD_INTENT_EVALUATION_SCHEMA_NAME = (
    "cross_thread_intent_evaluation.schema.json"
)
SCHEMA_FILE_NAMES = frozenset(
    {
        MESSAGE_SCHEMA_NAME,
        MANIFEST_SCHEMA_NAME,
        TOKEN_STATS_SCHEMA_NAME,
        TOKEN_DICTIONARY_SCHEMA_NAME,
        TOKEN_BUNDLES_SCHEMA_NAME,
        TOKEN_DICTIONARY_PROVENANCE_SCHEMA_NAME,
        METRICS_SCHEMA_NAME,
        THREAD_STATS_SCHEMA_NAME,
        MESSAGE_WINDOWS_SCHEMA_NAME,
        WINDOW_EMBEDDING_SCHEMA_NAME,
        WINDOW_NEIGHBORS_SCHEMA_NAME,
        WINDOW_CLUSTERS_SCHEMA_NAME,
        TOPICS_SCHEMA_NAME,
        TOPIC_MEMBERSHIP_SCHEMA_NAME,
        SEMANTIC_NORMALIZATION_JOB_CONFIG_SCHEMA_NAME,
        SEMANTIC_NORMALIZATION_JOB_SPAN_SCHEMA_NAME,
        SEMANTIC_NORMALIZATION_JOB_RESULT_SCHEMA_NAME,
        SEMANTIC_NORMALIZATION_JOB_FAILURE_SCHEMA_NAME,
        SEMANTIC_SPAN_PROPOSAL_SCHEMA_NAME,
        CROSS_THREAD_CANDIDATE_SCHEMA_NAME,
        CROSS_THREAD_INTENT_EVALUATION_SCHEMA_NAME,
    }
)


# ---------------------------------------------------------------------------
# Types / Result objects
# ---------------------------------------------------------------------------

@dataclass
class SchemaViolation:
    """Validation error information for a single object (1 line or 1 file)"""

    path: Path
    location: Optional[str]  # Example: "line=12", "item=0"
    message: str
    field_path: str  # JSON Pointer style: e.g. "messages[3].role"

    @classmethod
    def from_jsonschema_error(
        cls,
        path: Path,
        error: ValidationError,
        *,
        location: Optional[str] = None,
    ) -> "SchemaViolation":
        # jsonschema's error.path is an iterable like ["messages", 3, "role"]
        parts = []
        for p in error.path:
            if isinstance(p, int):
                parts.append(f"[{p}]")
            else:
                if parts:
                    parts.append(f".{p}")
                else:
                    parts.append(str(p))
        field_path = "".join(parts) if parts else "<root>"
        return cls(
            path=path,
            location=location,
            message=error.message,
            field_path=field_path,
        )


@dataclass
class ValidationSummary:
    """Validation result for a single file"""

    path: Path
    ok: bool
    violations: list[SchemaViolation]

    def raise_if_failed(self) -> None:
        if self.ok:
            return
        lines = [f"Schema validation failed for {self.path}:"]
        for v in self.violations:
            loc = f" ({v.location})" if v.location else ""
            lines.append(f"  - {v.field_path}{loc}: {v.message}")
        raise SchemaValidationError("\n".join(lines), summary=self)


class SchemaValidationError(RuntimeError):
    """Exception for bulk handling"""

    def __init__(self, msg: str, *, summary: ValidationSummary):
        super().__init__(msg)
        self.summary = summary


# ---------------------------------------------------------------------------
# Validator preparation
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _make_validator(schema_path: Path):
    schema = _load_json(schema_path)
    _, _, validator_for = _import_jsonschema_components()
    ValidatorCls = validator_for(schema)
    ValidatorCls.check_schema(schema)
    return ValidatorCls(schema)


def load_validator(
    schema_name: str,
    schema_path: Optional[Path] = None,
):
    """Return a validator for one explicit schema file name."""
    if schema_name not in SCHEMA_FILE_NAMES:
        raise ValueError(f"unknown schema name: {schema_name}")
    resolved_path = schema_path or (SCHEMAS_ROOT / schema_name)
    return _make_validator(resolved_path)


def load_message_validator(
    schema_path: Optional[Path] = None,
):
    """
    Returns a validator for message.schema.json.

    Parameters
    ----------
    schema_path:
        If None, SCHEMAS_ROOT / MESSAGE_SCHEMA_NAME is used.
    """
    return load_validator(MESSAGE_SCHEMA_NAME, schema_path)


def load_manifest_validator(
    schema_path: Optional[Path] = None,
):
    """
    Returns a validator for manifest.schema.json.
    """
    return load_validator(MANIFEST_SCHEMA_NAME, schema_path)


def load_token_stats_validator(
    schema_path: Optional[Path] = None,
):
    """
    Returns a validator for token_stats.schema.json.
    """
    return load_validator(TOKEN_STATS_SCHEMA_NAME, schema_path)


def load_token_dictionary_validator(
    schema_path: Optional[Path] = None,
):
    """
    Returns a validator for token_dictionary.schema.json.
    """
    return load_validator(TOKEN_DICTIONARY_SCHEMA_NAME, schema_path)


def load_token_bundles_validator(
    schema_path: Optional[Path] = None,
):
    """
    Returns a validator for token_bundles.schema.json.
    """
    return load_validator(TOKEN_BUNDLES_SCHEMA_NAME, schema_path)


def load_token_dictionary_provenance_validator(
    schema_path: Optional[Path] = None,
):
    """
    Returns a validator for token_dictionary_provenance.schema.json.
    """
    return load_validator(TOKEN_DICTIONARY_PROVENANCE_SCHEMA_NAME, schema_path)


def load_metrics_validator(
    schema_path: Optional[Path] = None,
):
    """
    Returns a validator for metrics.schema.json.
    """
    return load_validator(METRICS_SCHEMA_NAME, schema_path)


def load_thread_stats_validator(
    schema_path: Optional[Path] = None,
):
    """
    Returns a validator for thread_stats.schema.json.
    """
    return load_validator(THREAD_STATS_SCHEMA_NAME, schema_path)


def load_message_windows_validator(
    schema_path: Optional[Path] = None,
):
    """
    Returns a validator for message_windows.schema.json.
    """
    return load_validator(MESSAGE_WINDOWS_SCHEMA_NAME, schema_path)


def load_window_embedding_validator(
    schema_path: Optional[Path] = None,
):
    """
    Returns a validator for window_embedding.schema.json.
    """
    return load_validator(WINDOW_EMBEDDING_SCHEMA_NAME, schema_path)


def load_window_neighbors_validator(
    schema_path: Optional[Path] = None,
):
    """
    Returns a validator for window_neighbors.schema.json.
    """
    return load_validator(WINDOW_NEIGHBORS_SCHEMA_NAME, schema_path)


def load_window_clusters_validator(
    schema_path: Optional[Path] = None,
):
    """
    Returns a validator for window_clusters.schema.json.
    """
    return load_validator(WINDOW_CLUSTERS_SCHEMA_NAME, schema_path)


def load_topics_validator(
    schema_path: Optional[Path] = None,
):
    """
    Returns a validator for topics.schema.json.
    """
    return load_validator(TOPICS_SCHEMA_NAME, schema_path)


def load_topic_membership_validator(
    schema_path: Optional[Path] = None,
):
    """
    Returns a validator for topic_membership.schema.json.
    """
    return load_validator(TOPIC_MEMBERSHIP_SCHEMA_NAME, schema_path)


def load_semantic_normalization_job_config_validator(
    schema_path: Optional[Path] = None,
):
    """Returns a validator for semantic_normalization_job_config.schema.json."""
    return load_validator(SEMANTIC_NORMALIZATION_JOB_CONFIG_SCHEMA_NAME, schema_path)


def load_semantic_normalization_job_span_validator(
    schema_path: Optional[Path] = None,
):
    """Returns a validator for semantic_normalization_job_span.schema.json."""
    return load_validator(SEMANTIC_NORMALIZATION_JOB_SPAN_SCHEMA_NAME, schema_path)


def load_semantic_normalization_job_result_validator(
    schema_path: Optional[Path] = None,
):
    """Returns a validator for semantic_normalization_job_result.schema.json."""
    return load_validator(SEMANTIC_NORMALIZATION_JOB_RESULT_SCHEMA_NAME, schema_path)


def load_semantic_normalization_job_failure_validator(
    schema_path: Optional[Path] = None,
):
    """Returns a validator for semantic_normalization_job_failure.schema.json."""
    return load_validator(SEMANTIC_NORMALIZATION_JOB_FAILURE_SCHEMA_NAME, schema_path)


def load_semantic_span_proposal_validator(
    schema_path: Optional[Path] = None,
):
    """Returns a validator for semantic_span_proposal.schema.json."""
    return load_validator(SEMANTIC_SPAN_PROPOSAL_SCHEMA_NAME, schema_path)


def load_cross_thread_candidate_validator(
    schema_path: Optional[Path] = None,
):
    """Returns a validator for cross_thread_candidate.schema.json."""
    return load_validator(CROSS_THREAD_CANDIDATE_SCHEMA_NAME, schema_path)


def load_cross_thread_intent_evaluation_validator(
    schema_path: Optional[Path] = None,
):
    """Returns a validator for cross_thread_intent_evaluation.schema.json."""
    return load_validator(CROSS_THREAD_INTENT_EVALUATION_SCHEMA_NAME, schema_path)


class MessageValidationError(RuntimeError):
    def __init__(self, error: "ValidationError"):
        super().__init__(error.message)
        self.validation_error = error


class MessageSchemaValidator:
    def __init__(self, schema_path: Optional[Path] = None):
        self.schema_path = schema_path or (SCHEMAS_ROOT / MESSAGE_SCHEMA_NAME)
        self._validator = load_validator(MESSAGE_SCHEMA_NAME, self.schema_path)

    @property
    def validator(self):
        return self._validator

    def validate_message(self, obj: Mapping[str, Any]) -> None:
        errors = list(self._validator.iter_errors(obj))
        if not errors:
            return
        raise MessageValidationError(errors[0])

    def iter_validate_messages(self, messages: Iterable[Mapping[str, Any]]) -> Sequence[MessageValidationError]:
        violations: list[MessageValidationError] = []
        for msg in messages:
            try:
                self.validate_message(msg)
            except MessageValidationError as exc:
                violations.append(exc)
        return violations


class ManifestSchemaValidator:
    def __init__(self, schema_path: Optional[Path] = None):
        self.schema_path = schema_path or (SCHEMAS_ROOT / MANIFEST_SCHEMA_NAME)
        self._validator = load_validator(MANIFEST_SCHEMA_NAME, self.schema_path)

    def validate_manifest(self, obj: Mapping[str, Any]) -> None:
        violations = list(self._validator.iter_errors(obj))
        if violations:
            raise SchemaValidationError(
                "manifest validation failed",
                summary=ValidationSummary(
                    path=self.schema_path,
                    ok=False,
                    violations=[
                        SchemaViolation.from_jsonschema_error(
                            path=self.schema_path,
                            error=violation,
                        )
                        for violation in violations
                    ],
                ),
            )


# ---------------------------------------------------------------------------
# parsed.jsonl validation
# ---------------------------------------------------------------------------

def _iter_json_lines(path: Path) -> Iterator[tuple[int, dict]]:
    """
    Reads JSON Lines (JSONL) / NDJSON line by line, yielding (line_number, object).
    Line numbers are 1-indexed.
    """
    with path.open("r", encoding="utf-8") as f:
        for idx, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            obj = json.loads(stripped)
            if not isinstance(obj, dict):
                raise ValueError(f"{path}: line {idx} is not a JSON object")
            yield idx, obj


def validate_parsed_jsonl(
    path: Path,
    *,
    validator=None,
    schema_path: Optional[Path] = None,
    stop_on_first_error: bool = False,
) -> ValidationSummary:
    """
    Validates parsed.jsonl using message.schema.json.

    Parameters
    ----------
    path:
        Path to parsed.jsonl
    validator:
        If passing an already loaded validator from load_message_validator().
        If None, automatically loaded from schema_path.
    schema_path:
        Schema path to use when validator is None.
    stop_on_first_error:
        If True, immediately aborts on the first violation.

    Returns
    -------
    ValidationSummary
    """
    if isinstance(validator, MessageSchemaValidator):
        validator = validator.validator
    if validator is None:
        validator = load_validator(MESSAGE_SCHEMA_NAME, schema_path)

    violations: list[SchemaViolation] = []

    for line_no, obj in _iter_json_lines(path):
        for err in validator.iter_errors(obj):
            violations.append(
                SchemaViolation.from_jsonschema_error(
                    path=path,
                    error=err,
                    location=f"line={line_no}",
                )
            )
            if stop_on_first_error:
                return ValidationSummary(path=path, ok=False, violations=violations)

    return ValidationSummary(path=path, ok=not violations, violations=violations)


# ---------------------------------------------------------------------------
# manifest.json / meta.json validation
# ---------------------------------------------------------------------------

def validate_json_file(
    path: Path,
    *,
    validator,
    location: Optional[str] = None,
) -> ValidationSummary:
    """
    Validation of a general "single JSON object" file.
    Can be used commonly for manifest.json / meta.json etc.
    """
    obj = _load_json(path)
    violations: list[SchemaViolation] = []

    for err in validator.iter_errors(obj):
        violations.append(
            SchemaViolation.from_jsonschema_error(
                path=path,
                error=err,
                location=location,
            )
        )

    return ValidationSummary(path=path, ok=not violations, violations=violations)


def validate_manifest_file(
    path: Path,
    *,
    validator=None,
    schema_path: Optional[Path] = None,
    stop_on_first_error: bool = False,  # Compatibility dummy (currently unused)
) -> ValidationSummary:
    """
    Helper to validate manifest.json using manifest.schema.json.
    """
    if validator is None:
        validator = load_validator(MANIFEST_SCHEMA_NAME, schema_path)

    summary = validate_json_file(path, validator=validator, location=None)
    if stop_on_first_error and not summary.ok:
        summary.raise_if_failed()
    return summary
