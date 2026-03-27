from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
from typing import Any

from llm_logparser.core.i18n import _

SUPPORTED_CONFIG_SCHEMA_MAJOR = 1
SUPPORTED_CONFIG_SCHEMA_VERSION = "1"
_CONFIG_LOG = logging.getLogger("llm_logparser.config")

# These legacy profile-level keys remain accepted for schema_version 1
# compatibility only. A future schema_version 2 cleanup is expected to remove them.
LEGACY_PROFILE_KEY_REPLACEMENTS: dict[str, str] = {
    "outdir": "parse.outdir, chain.outdir, extract.outdir",
    "dry_run": "parse.dry_run, chain.dry_run, extract.dry_run",
    "fail_fast": "parse.fail_fast, chain.fail_fast",
    "validate_schema": "parse.validate_schema, chain.validate_schema",
    "export_outdir": "chain.export_outdir",
    "parsed_root": "chain.parsed_root",
    "conversation_id": "extract.conversation_id",
}

LEGACY_SEMANTIC_EMBEDDING_KEY_REPLACEMENTS: dict[str, str] = {
    "max_input_tokens": "max_input_bytes",
    "chunk_overlap_tokens": "chunk_overlap_bytes",
}


def _raise_config_error(message: str) -> None:
    raise SystemExit(_("runtime.config.invalid", message=message))


def _ensure_mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _raise_config_error(_("runtime.config.must_be_mapping", context=context))
    return value


def _optional_mapping(value: Any, context: str) -> dict[str, Any]:
    if value is None:
        return {}
    return _ensure_mapping(value, context)


def _optional_string(value: Any, context: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    _raise_config_error(_("runtime.config.must_be_string", context=context))


def _optional_bool(value: Any, context: str) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, int):
        return bool(value)
    _raise_config_error(_("runtime.config.must_be_boolean", context=context))


def _optional_int(value: Any, context: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        _raise_config_error(_("runtime.config.must_be_integer", context=context))
    if isinstance(value, int):
        return value
    _raise_config_error(_("runtime.config.must_be_integer", context=context))


def _optional_number(value: Any, context: str) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        _raise_config_error(_("runtime.config.must_be_number", context=context))
    if isinstance(value, (int, float)):
        return value
    _raise_config_error(_("runtime.config.must_be_number", context=context))


def _string_list(value: Any, context: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        _raise_config_error(_("runtime.config.must_be_list_of_strings", context=context))
    out: list[str] = []
    for index, item in enumerate(value):
        normalized = _optional_string(item, f"{context}[{index}]")
        if normalized is not None:
            out.append(normalized)
    return tuple(out)


def _optional_string_list(value: Any, context: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    return _string_list(value, context)


def _validate_regex_patterns(
    patterns: tuple[str, ...] | None,
    *,
    context: str,
) -> tuple[str, ...] | None:
    if patterns is None:
        return None
    for index, pattern in enumerate(patterns):
        try:
            re.compile(pattern)
        except re.error as exc:
            _raise_config_error(
                _("runtime.config.invalid_regex", context=context, index=index, detail=exc)
            )
    return patterns


def _compact_dict(data: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in data.items()
        if value is not None and value != {} and value != []
    }


def _warn_deprecated_profile_keys(profile_name: str, data: dict[str, Any]) -> None:
    for legacy_key, replacement in LEGACY_PROFILE_KEY_REPLACEMENTS.items():
        if legacy_key not in data:
            continue
        _CONFIG_LOG.warning(
            _(
                "runtime.config.deprecated_profile_key",
                profile_name=profile_name,
                legacy_key=legacy_key,
                replacement=replacement,
            )
        )


def _warn_deprecated_semantic_embedding_key(context: str, legacy_key: str) -> None:
    replacement = LEGACY_SEMANTIC_EMBEDDING_KEY_REPLACEMENTS[legacy_key]
    _CONFIG_LOG.warning(
        _(
            "runtime.config.deprecated_semantic_embedding_key",
            context=context,
            legacy_key=legacy_key,
            replacement=replacement,
        )
    )


def normalize_schema_version(raw_version: Any) -> str | None:
    if raw_version is None:
        return None
    if isinstance(raw_version, bool):
        _raise_config_error(_("runtime.config.schema_version_type"))

    if isinstance(raw_version, int):
        normalized = str(raw_version)
    elif isinstance(raw_version, float):
        if not raw_version.is_integer():
            _raise_config_error(_("runtime.config.schema_version_whole_major"))
        normalized = str(int(raw_version))
    elif isinstance(raw_version, str):
        normalized = raw_version.strip()
        if not normalized:
            _raise_config_error(_("runtime.config.schema_version_empty"))
    else:
        _raise_config_error(_("runtime.config.schema_version_type"))

    major_token = normalized.split(".", 1)[0]
    if not major_token.isdigit():
        _raise_config_error(_("runtime.config.schema_version_format"))

    major = int(major_token)
    if major != SUPPORTED_CONFIG_SCHEMA_MAJOR:
        raise SystemExit(
            _(
                "runtime.config.unsupported_schema_version",
                normalized=normalized,
                major=SUPPORTED_CONFIG_SCHEMA_MAJOR,
            )
        )
    return normalized


@dataclass(frozen=True)
class InputConfig:
    path: str | None = None
    paths: tuple[str, ...] = ()
    parsed: str | None = None

    @classmethod
    def from_raw(cls, raw: Any, *, context: str) -> InputConfig:
        data = _optional_mapping(raw, context)
        return cls(
            path=_optional_string(data.get("path"), f"{context}.path"),
            paths=_string_list(data.get("paths"), f"{context}.paths"),
            parsed=_optional_string(data.get("parsed"), f"{context}.parsed"),
        )

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(
            {
                "path": self.path,
                "paths": list(self.paths) if self.paths else None,
                "parsed": self.parsed,
            }
        )


@dataclass(frozen=True)
class OutputConfig:
    path: str | None = None
    formatting: str | None = None
    split: str | None = None
    split_soft_overflow: float | int | None = None
    split_hard: bool | None = None
    split_preview: bool | None = None
    tiny_tail_threshold: int | None = None

    @classmethod
    def from_raw(cls, raw: Any, *, context: str) -> OutputConfig:
        data = _optional_mapping(raw, context)
        return cls(
            path=_optional_string(data.get("path"), f"{context}.path"),
            formatting=_optional_string(data.get("formatting"), f"{context}.formatting"),
            split=_optional_string(data.get("split"), f"{context}.split"),
            split_soft_overflow=_optional_number(
                data.get("split_soft_overflow"),
                f"{context}.split_soft_overflow",
            ),
            split_hard=_optional_bool(data.get("split_hard"), f"{context}.split_hard"),
            split_preview=_optional_bool(
                data.get("split_preview"),
                f"{context}.split_preview",
            ),
            tiny_tail_threshold=_optional_int(
                data.get("tiny_tail_threshold"),
                f"{context}.tiny_tail_threshold",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(
            {
                "path": self.path,
                "formatting": self.formatting,
                "split": self.split,
                "split_soft_overflow": self.split_soft_overflow,
                "split_hard": self.split_hard,
                "split_preview": self.split_preview,
                "tiny_tail_threshold": self.tiny_tail_threshold,
            }
        )


@dataclass(frozen=True)
class LoggingConfig:
    level: str | None = None

    @classmethod
    def from_raw(cls, raw: Any, *, context: str) -> LoggingConfig:
        data = _optional_mapping(raw, context)
        return cls(level=_optional_string(data.get("level"), f"{context}.level"))

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict({"level": self.level})


@dataclass(frozen=True)
class SanitizeConfig:
    # Keep these config-side defaults aligned with `llm_logparser.core.sanitize`.
    # The runtime sanitize module owns the actual default patterns and policy behavior.
    enabled: bool = True
    replacement: str = "REDACTED"
    extra_keywords: tuple[str, ...] = ()
    scope: str = "content_parts"
    mask_patterns: tuple[str, ...] | None = None

    @classmethod
    def from_raw(cls, raw: Any, *, context: str) -> SanitizeConfig:
        data = _optional_mapping(raw, context)
        known_keys = {
            "enabled",
            "replacement",
            "extra_keywords",
            "scope",
            "mask_patterns",
        }
        unknown_keys = sorted(
            key for key in data.keys() if isinstance(key, str) and key not in known_keys
        )
        if unknown_keys:
            _CONFIG_LOG.warning(
                _("runtime.config.unknown_sanitize_keys", context=context, keys=", ".join(unknown_keys))
            )
        enabled = _optional_bool(data.get("enabled"), f"{context}.enabled")
        replacement = _optional_string(data.get("replacement"), f"{context}.replacement")
        scope = _optional_string(data.get("scope"), f"{context}.scope")
        if scope is not None and scope not in {"content_parts", "all_strings"}:
            _raise_config_error(_("runtime.config.sanitize_scope_invalid", context=context))

        raw_patterns = (
            _optional_string_list(data.get("mask_patterns"), f"{context}.mask_patterns")
            if "mask_patterns" in data
            else None
        )
        return cls(
            enabled=True if enabled is None else enabled,
            replacement=replacement or "REDACTED",
            extra_keywords=_string_list(
                data.get("extra_keywords"),
                f"{context}.extra_keywords",
            ),
            scope=scope or "content_parts",
            mask_patterns=_validate_regex_patterns(
                raw_patterns,
                context=f"{context}.mask_patterns",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(
            {
                "enabled": self.enabled,
                "replacement": self.replacement,
                "extra_keywords": list(self.extra_keywords) if self.extra_keywords else None,
                "scope": self.scope,
                "mask_patterns": list(self.mask_patterns)
                if self.mask_patterns is not None
                else None,
            }
        )


@dataclass(frozen=True)
class ParseConfig:
    outdir: str | None = None
    dry_run: bool | None = None
    fail_fast: bool | None = None
    validate_schema: bool | None = None

    @classmethod
    def from_raw(cls, raw: Any, *, context: str) -> ParseConfig:
        data = _optional_mapping(raw, context)
        return cls(
            outdir=_optional_string(data.get("outdir"), f"{context}.outdir"),
            dry_run=_optional_bool(data.get("dry_run"), f"{context}.dry_run"),
            fail_fast=_optional_bool(data.get("fail_fast"), f"{context}.fail_fast"),
            validate_schema=_optional_bool(
                data.get("validate_schema"),
                f"{context}.validate_schema",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(
            {
                "outdir": self.outdir,
                "dry_run": self.dry_run,
                "fail_fast": self.fail_fast,
                "validate_schema": self.validate_schema,
            }
        )


@dataclass(frozen=True)
class ChainConfig:
    outdir: str | None = None
    export_outdir: str | None = None
    parsed_root: str | None = None
    dry_run: bool | None = None
    fail_fast: bool | None = None
    validate_schema: bool | None = None

    @classmethod
    def from_raw(cls, raw: Any, *, context: str) -> ChainConfig:
        data = _optional_mapping(raw, context)
        return cls(
            outdir=_optional_string(data.get("outdir"), f"{context}.outdir"),
            export_outdir=_optional_string(
                data.get("export_outdir"),
                f"{context}.export_outdir",
            ),
            parsed_root=_optional_string(
                data.get("parsed_root"),
                f"{context}.parsed_root",
            ),
            dry_run=_optional_bool(data.get("dry_run"), f"{context}.dry_run"),
            fail_fast=_optional_bool(data.get("fail_fast"), f"{context}.fail_fast"),
            validate_schema=_optional_bool(
                data.get("validate_schema"),
                f"{context}.validate_schema",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(
            {
                "outdir": self.outdir,
                "export_outdir": self.export_outdir,
                "parsed_root": self.parsed_root,
                "dry_run": self.dry_run,
                "fail_fast": self.fail_fast,
                "validate_schema": self.validate_schema,
            }
        )


@dataclass(frozen=True)
class ExtractConfig:
    outdir: str | None = None
    conversation_id: str | None = None
    dry_run: bool | None = None

    @classmethod
    def from_raw(cls, raw: Any, *, context: str) -> ExtractConfig:
        data = _optional_mapping(raw, context)
        return cls(
            outdir=_optional_string(data.get("outdir"), f"{context}.outdir"),
            conversation_id=_optional_string(
                data.get("conversation_id"),
                f"{context}.conversation_id",
            ),
            dry_run=_optional_bool(data.get("dry_run"), f"{context}.dry_run"),
        )

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(
            {
                "outdir": self.outdir,
                "conversation_id": self.conversation_id,
                "dry_run": self.dry_run,
            }
        )


@dataclass(frozen=True)
class SemanticPrototypeEmbeddingConfig:
    max_input_bytes: int | None = None
    chunk_overlap_bytes: int | None = None
    aggregate: str | None = None

    @classmethod
    def from_raw(cls, raw: Any, *, context: str) -> SemanticPrototypeEmbeddingConfig:
        data = _optional_mapping(raw, context)
        if "max_input_tokens" in data:
            _warn_deprecated_semantic_embedding_key(context, "max_input_tokens")
        if "chunk_overlap_tokens" in data:
            _warn_deprecated_semantic_embedding_key(context, "chunk_overlap_tokens")
        return cls(
            max_input_bytes=_optional_int(
                data.get("max_input_bytes", data.get("max_input_tokens")),
                f"{context}.max_input_bytes",
            ),
            chunk_overlap_bytes=_optional_int(
                data.get("chunk_overlap_bytes", data.get("chunk_overlap_tokens")),
                f"{context}.chunk_overlap_bytes",
            ),
            aggregate=_optional_string(data.get("aggregate"), f"{context}.aggregate"),
        )

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(
            {
                "max_input_bytes": self.max_input_bytes,
                "chunk_overlap_bytes": self.chunk_overlap_bytes,
                "aggregate": self.aggregate,
            }
        )


@dataclass(frozen=True)
class SemanticPrototypeBackendOptionsConfig:
    base_url: str | None = None
    timeout_seconds: float | int | None = None

    @classmethod
    def from_raw(cls, raw: Any, *, context: str) -> SemanticPrototypeBackendOptionsConfig:
        data = _optional_mapping(raw, context)
        return cls(
            base_url=_optional_string(data.get("base_url"), f"{context}.base_url"),
            timeout_seconds=_optional_number(
                data.get("timeout_seconds"),
                f"{context}.timeout_seconds",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(
            {
                "base_url": self.base_url,
                "timeout_seconds": self.timeout_seconds,
            }
        )


@dataclass(frozen=True)
class SemanticPrototypeConfig:
    backend: str | None = None
    model: str | None = None
    top_k: int | None = None
    backend_options: SemanticPrototypeBackendOptionsConfig = field(
        default_factory=SemanticPrototypeBackendOptionsConfig
    )
    embedding: SemanticPrototypeEmbeddingConfig = field(
        default_factory=SemanticPrototypeEmbeddingConfig
    )

    @classmethod
    def from_raw(cls, raw: Any, *, context: str) -> SemanticPrototypeConfig:
        data = _optional_mapping(raw, context)
        return cls(
            backend=_optional_string(data.get("backend"), f"{context}.backend"),
            model=_optional_string(data.get("model"), f"{context}.model"),
            top_k=_optional_int(data.get("top_k"), f"{context}.top_k"),
            backend_options=SemanticPrototypeBackendOptionsConfig.from_raw(
                data.get("backend_options"),
                context=f"{context}.backend_options",
            ),
            embedding=SemanticPrototypeEmbeddingConfig.from_raw(
                data.get("embedding"),
                context=f"{context}.embedding",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(
            {
                "backend": self.backend,
                "model": self.model,
                "top_k": self.top_k,
                "backend_options": self.backend_options.to_dict(),
                "embedding": self.embedding.to_dict(),
            }
        )


@dataclass(frozen=True)
class AnalyzeConfig:
    semantic_prototype: SemanticPrototypeConfig = field(
        default_factory=SemanticPrototypeConfig
    )

    @classmethod
    def from_raw(cls, raw: Any, *, context: str) -> AnalyzeConfig:
        data = _optional_mapping(raw, context)
        return cls(
            semantic_prototype=SemanticPrototypeConfig.from_raw(
                data.get("semantic_prototype"),
                context=f"{context}.semantic_prototype",
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(
            {
                "semantic_prototype": self.semantic_prototype.to_dict(),
            }
        )


@dataclass(frozen=True)
class ConfigProfile:
    name: str
    locale: str | None = None
    timezone: str | None = None
    provider: str | None = None
    conversation_id: str | None = None
    input: InputConfig = field(default_factory=InputConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    sanitize: SanitizeConfig = field(default_factory=SanitizeConfig)
    parse: ParseConfig = field(default_factory=ParseConfig)
    chain: ChainConfig = field(default_factory=ChainConfig)
    extract: ExtractConfig = field(default_factory=ExtractConfig)
    analyze: AnalyzeConfig = field(default_factory=AnalyzeConfig)

    @classmethod
    def from_raw(cls, name: str, raw: Any) -> ConfigProfile:
        data = _ensure_mapping(raw, f"profiles.{name}")
        _warn_deprecated_profile_keys(name, data)
        parse = ParseConfig.from_raw(data.get("parse"), context=f"profiles.{name}.parse")
        chain = ChainConfig.from_raw(data.get("chain"), context=f"profiles.{name}.chain")
        extract = ExtractConfig.from_raw(
            data.get("extract"),
            context=f"profiles.{name}.extract",
        )
        analyze = AnalyzeConfig.from_raw(
            data.get("analyze"),
            context=f"profiles.{name}.analyze",
        )

        # Temporary compatibility with earlier profile-level keys.
        legacy_outdir = _optional_string(data.get("outdir"), f"profiles.{name}.outdir")
        legacy_dry_run = _optional_bool(data.get("dry_run"), f"profiles.{name}.dry_run")
        legacy_fail_fast = _optional_bool(
            data.get("fail_fast"),
            f"profiles.{name}.fail_fast",
        )
        legacy_validate_schema = _optional_bool(
            data.get("validate_schema"),
            f"profiles.{name}.validate_schema",
        )
        legacy_export_outdir = _optional_string(
            data.get("export_outdir"),
            f"profiles.{name}.export_outdir",
        )
        legacy_parsed_root = _optional_string(
            data.get("parsed_root"),
            f"profiles.{name}.parsed_root",
        )
        conversation_id = _optional_string(
            data.get("conversation_id"),
            f"profiles.{name}.conversation_id",
        )

        return cls(
            name=name,
            locale=_optional_string(data.get("locale"), f"profiles.{name}.locale"),
            timezone=_optional_string(
                data.get("timezone"),
                f"profiles.{name}.timezone",
            ),
            provider=_optional_string(data.get("provider"), f"profiles.{name}.provider"),
            conversation_id=conversation_id,
            input=InputConfig.from_raw(data.get("input"), context=f"profiles.{name}.input"),
            output=OutputConfig.from_raw(
                data.get("output"),
                context=f"profiles.{name}.output",
            ),
            logging=LoggingConfig.from_raw(
                data.get("logging"),
                context=f"profiles.{name}.logging",
            ),
            sanitize=SanitizeConfig.from_raw(
                data.get("sanitize"),
                context=f"profiles.{name}.sanitize",
            ),
            parse=ParseConfig(
                outdir=parse.outdir or legacy_outdir,
                dry_run=parse.dry_run if parse.dry_run is not None else legacy_dry_run,
                fail_fast=(
                    parse.fail_fast if parse.fail_fast is not None else legacy_fail_fast
                ),
                validate_schema=(
                    parse.validate_schema
                    if parse.validate_schema is not None
                    else legacy_validate_schema
                ),
            ),
            chain=ChainConfig(
                outdir=chain.outdir or legacy_outdir,
                export_outdir=chain.export_outdir or legacy_export_outdir,
                parsed_root=chain.parsed_root or legacy_parsed_root,
                dry_run=chain.dry_run if chain.dry_run is not None else legacy_dry_run,
                fail_fast=(
                    chain.fail_fast if chain.fail_fast is not None else legacy_fail_fast
                ),
                validate_schema=(
                    chain.validate_schema
                    if chain.validate_schema is not None
                    else legacy_validate_schema
                ),
            ),
            extract=ExtractConfig(
                outdir=extract.outdir or legacy_outdir,
                conversation_id=extract.conversation_id or conversation_id,
                dry_run=(
                    extract.dry_run if extract.dry_run is not None else legacy_dry_run
                ),
            ),
            analyze=analyze,
        )

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(
            {
                "locale": self.locale,
                "timezone": self.timezone,
                "provider": self.provider,
                "conversation_id": self.conversation_id,
                "input": self.input.to_dict(),
                "output": self.output.to_dict(),
                "logging": self.logging.to_dict(),
                "sanitize": self.sanitize.to_dict(),
                "parse": self.parse.to_dict(),
                "chain": self.chain.to_dict(),
                "extract": self.extract.to_dict(),
                "analyze": self.analyze.to_dict(),
            }
        )


@dataclass(frozen=True)
class AppConfig:
    schema_version: str | None
    active_profile: str | None
    profiles: dict[str, ConfigProfile]

    @classmethod
    def from_mapping(cls, raw: Any) -> AppConfig:
        data = _ensure_mapping(raw, "config root")
        schema_version = normalize_schema_version(data.get("schema_version"))
        active_profile = _optional_string(data.get("active_profile"), "active_profile")

        raw_profiles = data.get("profiles")
        if raw_profiles is None:
            profiles: dict[str, ConfigProfile] = {}
        else:
            profiles = {}
            for raw_name, profile_raw in _ensure_mapping(raw_profiles, "profiles").items():
                if not isinstance(raw_name, str) or not raw_name.strip():
                    _raise_config_error(_("runtime.config.profile_names_non_empty"))
                name = raw_name.strip()
                profiles[name] = ConfigProfile.from_raw(name, profile_raw)

        if active_profile is not None and active_profile not in profiles:
            raise SystemExit(_("runtime.config.profile_not_found", name=active_profile))

        return cls(
            schema_version=schema_version,
            active_profile=active_profile,
            profiles=profiles,
        )

    def to_dict(self) -> dict[str, Any]:
        return _compact_dict(
            {
                "schema_version": self.schema_version,
                "active_profile": self.active_profile,
                "profiles": {
                    name: profile.to_dict() for name, profile in self.profiles.items()
                },
            }
        )
