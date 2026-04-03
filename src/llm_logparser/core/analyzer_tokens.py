from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any

from .i18n import _
from .analyzer_common import (
    ROLE_ORDER,
    UNKNOWN_ROLE,
    plan_sidecar_actions,
    render_artifact_json,
    resolve_canonical_text,
    string_or_none,
    write_json_artifact,
)
from .l1_derivation import canonical_role_or_unknown, discover_parsed_jsonl, iter_parsed_records


@dataclass(frozen=True)
class TokenizerSpec:
    family: str
    library: str
    library_version: str
    resolved_model: str | None
    resolved_encoding: str
    resolution_source: str
    text_policy: str = "canonical_text_only"
    special_token_policy: str = "ordinary_text"


class TiktokenTokenizer:
    """Minimal analyze-time tokenizer wrapper.

    TODO: `tiktoken` may fetch encoding assets on first use. This backend keeps
    resolution isolated so Step 2 can vendor or pre-bundle the required
    encoding assets to preserve strict offline-first behavior.
    """

    def __init__(self, spec: TokenizerSpec, encoder: Any) -> None:
        self.spec = spec
        self._encoder = encoder

    def count_text(self, text: str) -> int:
        return len(self._encoder.encode_ordinary(text))

    def metadata(self) -> dict[str, Any]:
        return {
            "family": self.spec.family,
            "library": self.spec.library,
            "library_version": self.spec.library_version,
            "resolved_model": self.spec.resolved_model,
            "resolved_encoding": self.spec.resolved_encoding,
            "resolution_source": self.spec.resolution_source,
            "text_policy": self.spec.text_policy,
            "special_token_policy": self.spec.special_token_policy,
        }


def resolve_analyze_tokenizer(
    *,
    provider_id: str | None,
    thread_model: str | None,
    model_override: str | None = None,
    encoding_override: str | None = None,
) -> TiktokenTokenizer:
    import tiktoken

    resolved_model = model_override or thread_model

    if encoding_override:
        try:
            encoder = tiktoken.get_encoding(encoding_override)
        except ValueError as exc:
            raise SystemExit(
                _("runtime.analyze_tokens.unsupported_encoding", encoding=encoding_override)
            ) from exc
        spec = TokenizerSpec(
            family="gpt_bpe",
            library="tiktoken",
            library_version=version("tiktoken"),
            resolved_model=resolved_model,
            resolved_encoding=encoding_override,
            resolution_source="explicit_encoding",
        )
        return TiktokenTokenizer(spec, encoder)

    if model_override:
        try:
            encoder = tiktoken.encoding_for_model(model_override)
        except KeyError as exc:
            raise SystemExit(
                _("runtime.analyze_tokens.unsupported_model", model=model_override)
            ) from exc
        spec = TokenizerSpec(
            family="gpt_bpe",
            library="tiktoken",
            library_version=version("tiktoken"),
            resolved_model=model_override,
            resolved_encoding=encoder.name,
            resolution_source="explicit_model",
        )
        return TiktokenTokenizer(spec, encoder)

    if provider_id == "openai" and thread_model:
        try:
            encoder = tiktoken.encoding_for_model(thread_model)
        except KeyError:
            encoder = tiktoken.get_encoding("o200k_base")
            resolution_source = "provider_default"
            resolved_encoding = "o200k_base"
        else:
            resolution_source = "model"
            resolved_encoding = encoder.name

        spec = TokenizerSpec(
            family="gpt_bpe",
            library="tiktoken",
            library_version=version("tiktoken"),
            resolved_model=thread_model,
            resolved_encoding=resolved_encoding,
            resolution_source=resolution_source,
        )
        return TiktokenTokenizer(spec, encoder)

    if provider_id in {"openai", "anthropic", "xai"}:
        encoder = tiktoken.get_encoding("o200k_base")
        spec = TokenizerSpec(
            family="gpt_bpe",
            library="tiktoken",
            library_version=version("tiktoken"),
            resolved_model=thread_model,
            resolved_encoding="o200k_base",
            resolution_source="provider_default",
        )
        return TiktokenTokenizer(spec, encoder)

    provider_label = provider_id or "unknown"
    raise SystemExit(
        _("runtime.analyze_tokens.unsupported_provider", provider=provider_label)
    )


def build_token_stats_artifact(
    parsed_path: Path,
    *,
    model_override: str | None = None,
    encoding_override: str | None = None,
) -> dict[str, Any]:
    by_role: OrderedDict[str, dict[str, int]] = OrderedDict(
        (role, {"messages": 0, "tokens": 0}) for role in ROLE_ORDER
    )
    by_role[UNKNOWN_ROLE] = {"messages": 0, "tokens": 0}

    provider_id: str | None = None
    conversation_id: str | None = None
    thread_model: str | None = None
    buffered_messages: list[dict[str, str]] = []
    messages: list[dict[str, Any]] = []
    message_count = 0
    turn_count = 0
    tokens_total = 0
    tokens_user = 0
    tokens_assistant = 0
    empty_text_messages = 0

    for row in iter_parsed_records(parsed_path):
        record_type = row.get("record_type")
        if record_type == "thread":
            if provider_id is None:
                provider_id = string_or_none(row.get("provider_id"))
            if conversation_id is None:
                conversation_id = string_or_none(row.get("conversation_id"))
            continue

        if record_type != "message":
            continue

        if provider_id is None:
            provider_id = string_or_none(row.get("provider_id"))
        if conversation_id is None:
            conversation_id = string_or_none(row.get("conversation_id"))
        if thread_model is None:
            meta = row.get("meta")
            if isinstance(meta, dict):
                thread_model = string_or_none(meta.get("model"))

        # L1 invariant: must use canonical normalized roles only
        role = canonical_role_or_unknown(row.get("role"))
        text, text_source = resolve_canonical_text(row)
        buffered_messages.append(
            {
                "message_id": string_or_none(row.get("message_id")) or "",
                "role": role,
                "text": text,
                "text_source": text_source,
            }
        )

    if conversation_id is None:
        raise ValueError(f"parsed thread has no conversation_id: {parsed_path}")

    tokenizer = resolve_analyze_tokenizer(
        provider_id=provider_id,
        thread_model=thread_model,
        model_override=model_override,
        encoding_override=encoding_override,
    )

    for row in buffered_messages:
        role = row["role"]
        text = row["text"]
        text_source = row["text_source"]

        if text_source == "empty":
            empty_text_messages += 1

        token_count = tokenizer.count_text(text)
        message_count += 1
        tokens_total += token_count

        if role == "user":
            turn_count += 1
            tokens_user += token_count
        elif role == "assistant":
            tokens_assistant += token_count

        by_role[role]["messages"] += 1
        by_role[role]["tokens"] += token_count
        messages.append(
            {
                "message_id": row["message_id"],
                "role": role,
                "token_count": token_count,
                "text_source": text_source,
            }
        )

    artifact = {
        "artifact_type": "token_stats",
        "schema_version": "2.0",
        "provider_id": provider_id or "unknown",
        "conversation_id": conversation_id,
        "tokenizer": tokenizer.metadata(),
        "summary": {
            "message_count": message_count,
            "turn_count": turn_count,
            "tokens_total": tokens_total,
            "tokens_user": tokens_user,
            "tokens_assistant": tokens_assistant,
            "avg_tokens_per_message": round(tokens_total / message_count, 2)
            if message_count
            else 0.0,
            "avg_tokens_per_turn": round(tokens_total / turn_count, 2)
            if turn_count
            else 0.0,
            "empty_text_messages": empty_text_messages,
        },
        "by_role": {
            role: stats
            for role, stats in by_role.items()
            if role != UNKNOWN_ROLE or stats["messages"] > 0
        },
        "messages": messages,
    }
    return artifact


def render_token_stats_json(artifact: dict[str, Any]) -> str:
    # token_stats.json is a machine-readable sidecar, so its schema stays in
    # stable English even though interactive runtime failures use i18n.
    return render_artifact_json(artifact)


def write_token_stats_artifact(parsed_path: Path, artifact: dict[str, Any]) -> Path:
    artifact_path = parsed_path.with_name("token_stats.json")
    return write_json_artifact(artifact_path, artifact)


def token_stats_artifact_path(parsed_path: Path) -> Path:
    return parsed_path.with_name("token_stats.json")


def analyze_tokens(
    input_path: Path,
    *,
    model_override: str | None = None,
    encoding_override: str | None = None,
    skip_existing: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    parsed_files = discover_parsed_jsonl(input_path)
    plan = plan_sidecar_actions(
        parsed_files,
        token_stats_artifact_path,
        skip_existing=skip_existing,
    )
    written_artifacts: list[Path] = []

    for parsed_path, artifact_path in plan["planned_actions"]:
        artifact = build_token_stats_artifact(
            parsed_path,
            model_override=model_override,
            encoding_override=encoding_override,
        )
        if dry_run:
            continue
        written_artifacts.append(write_json_artifact(artifact_path, artifact))

    return {
        "threads": len(written_artifacts),
        "artifacts": written_artifacts,
        "detected_threads": plan["detected_threads"],
        "existing_threads": plan["existing_threads"],
        "new_threads": plan["new_threads"],
        "rebuild_threads": plan["rebuild_threads"],
        "skipped_threads": plan["skipped_threads"],
        "skipped_artifacts": plan["skipped_artifacts"],
        "dry_run": dry_run,
    }
