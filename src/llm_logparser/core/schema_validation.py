# src/llm_logparser/core/schema_validation.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Literal, Mapping, Optional, Sequence, TYPE_CHECKING

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
            "jsonschema が見つかりませんでした。"
            "  pyproject.toml の dependencies に `jsonschema` を追加してください。"
        ) from exc
    return jsonschema, ValidationError, validator_for


# ---------------------------------------------------------------------------
# パス解決
# ---------------------------------------------------------------------------

def _default_schemas_root() -> Path:
    """
    開発環境 / インストール環境の両方をある程度ケアしつつ、
    schemas ディレクトリを推測するヘルパー。

    想定レイアウト:
        repo_root/
          schemas/
            message.schema.json
            manifest.schema.json
          src/
            llm_logparser/
              core/
                schema_validation.py  (このファイル)
    """
    here = Path(__file__).resolve()

    # repo_root/schemas を探す
    # .../src/llm_logparser/core/schema_validation.py
    # parents[0] core
    # parents[1] llm_logparser
    # parents[2] src
    # parents[3] repo_root
    candidates = [
        here.parents[0] / "schemas",       # src/llm_logparser/core/schemas
        here.parents[3] / "schemas",       # 開発時: repo_root/schemas
        here.parents[1] / "schemas",       # インストール時: llm_logparser/schemas
    ]
    for path in candidates:
        if path.is_dir():
            return path

    # ダメだったら一番最初をそのまま返してエラーに任せる
    return candidates[0]


SCHEMAS_ROOT: Path = _default_schemas_root()
MESSAGE_SCHEMA_NAME = "message.schema.json"
MANIFEST_SCHEMA_NAME = "manifest.schema.json"


# ---------------------------------------------------------------------------
# 型 / 結果オブジェクト
# ---------------------------------------------------------------------------

@dataclass
class SchemaViolation:
    """単一オブジェクト（1行 or 1ファイル）に対するバリデーションエラー情報"""

    path: Path
    location: Optional[str]  # 例: "line=12", "item=0"
    message: str
    field_path: str  # JSON Pointer 風: "messages[3].role" など

    @classmethod
    def from_jsonschema_error(
        cls,
        path: Path,
        error: ValidationError,
        *,
        location: Optional[str] = None,
    ) -> "SchemaViolation":
        # jsonschema の error.path は ["messages", 3, "role"] のような iterable
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
    """1つのファイルに対するバリデーション結果"""

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
    """まとめて扱うための例外"""

    def __init__(self, msg: str, *, summary: ValidationSummary):
        super().__init__(msg)
        self.summary = summary


# ---------------------------------------------------------------------------
# Validator 準備
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


def load_message_validator(
    schema_path: Optional[Path] = None,
):
    """
    message.schema.json 用の validator を返す。

    Parameters
    ----------
    schema_path:
        None の場合は SCHEMAS_ROOT / MESSAGE_SCHEMA_NAME を使う。
    """
    if schema_path is None:
        schema_path = SCHEMAS_ROOT / MESSAGE_SCHEMA_NAME
    return _make_validator(schema_path)


def load_manifest_validator(
    schema_path: Optional[Path] = None,
):
    """
    manifest.schema.json 用の validator を返す。
    """
    if schema_path is None:
        schema_path = SCHEMAS_ROOT / MANIFEST_SCHEMA_NAME
    return _make_validator(schema_path)


class MessageValidationError(RuntimeError):
    def __init__(self, error: "ValidationError"):
        super().__init__(error.message)
        self.validation_error = error


class MessageSchemaValidator:
    def __init__(self, schema_path: Optional[Path] = None):
        self.schema_path = schema_path or (SCHEMAS_ROOT / MESSAGE_SCHEMA_NAME)
        self._validator = load_message_validator(self.schema_path)

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
        self._validator = load_manifest_validator(self.schema_path)

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
# parsed.jsonl の検証
# ---------------------------------------------------------------------------

def _iter_json_lines(path: Path) -> Iterator[tuple[int, dict]]:
    """
    JSON Lines (JSONL) / NDJSON を 1 行ずつ読み、(行番号, オブジェクト) を yield する。
    行番号は 1 始まり。
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
    parsed.jsonl を message.schema.json で検証する。

    Parameters
    ----------
    path:
        parsed.jsonl のパス
    validator:
        事前に load_message_validator() したものを渡す場合。
        None のときは schema_path から自動ロード。
    schema_path:
        validator が None のときに使うスキーマパス。
    stop_on_first_error:
        True の場合、最初の違反で即座に中断する。

    Returns
    -------
    ValidationSummary
    """
    if isinstance(validator, MessageSchemaValidator):
        validator = validator.validator
    if validator is None:
        validator = load_message_validator(schema_path)

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
# manifest.json / meta.json の検証
# ---------------------------------------------------------------------------

def validate_json_file(
    path: Path,
    *,
    validator,
    location: Optional[str] = None,
) -> ValidationSummary:
    """
    一般的な「単一 JSON オブジェクト」ファイルの検証。
    manifest.json / meta.json など共通で使える。
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
    stop_on_first_error: bool = False,  # 互換用ダミー（今は使わない）
) -> ValidationSummary:
    """
    manifest.json を manifest.schema.json で検証するヘルパー。
    """
    if validator is None:
        validator = load_manifest_validator(schema_path)

    summary = validate_json_file(path, validator=validator, location=None)
    if stop_on_first_error and not summary.ok:
        summary.raise_if_failed()
    return summary
