from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Protocol

from .llm_client_protocol import LLMClient
from .ollama_client import OllamaClient
from .analyzer_intra_thread_topics import (
    IntraThreadTopicsError,
    intra_thread_segments_artifact_path,
    intra_thread_topics_dir,
    reconstruct_thread_messages,
)
from .l1_derivation import discover_parsed_jsonl
from .schema_validation import load_intra_thread_topic_summary_validator
from .structured_llm import generate_structured_json

TOPIC_SUMMARY_SCHEMA_VERSION = "0.1"
DEFAULT_LOCAL_LLM_MODEL = "gemma4-Q8_K_XL:latest"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_TIMEOUT_SECONDS = 120.0
LOCAL_LLM_PROMPT_VARIANT = "intra_thread_topic_summary_v0"
LOCAL_LLM_MAX_PROMPT_TOKENS = 4096
LOCAL_LLM_PROMPT_TOKEN_MARGIN = 32
LOCAL_LLM_PROMPT_FALLBACK_HEAD_CHARS = 8000
LOCAL_LLM_PROMPT_FALLBACK_TAIL_CHARS = 4000
LOCAL_LLM_PROMPT_TRUNCATION_SEPARATOR = "\n...\n"
SUMMARY_MAX_CHARS = 280
TITLE_MAX_CHARS = 72
TITLE_MAX_TOKENS = 8
KEYWORD_LIMIT = 8
KEYWORD_MIN_LENGTH = 2
TOKEN_RE = re.compile(r"[a-z][a-z0-9_/-]*|[一-龯ぁ-んァ-ヶー]+", re.IGNORECASE)
PROMPT_TOKEN_RE = re.compile(
    r"\s+|[A-Za-z0-9_/-]+|[一-龯ぁ-んァ-ヶー]+|[^\s]",
    re.IGNORECASE,
)
PROMPT_TOKEN_CHUNK_CHARS = 4
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "can",
        "for",
        "from",
        "how",
        "into",
        "not",
        "the",
        "this",
        "that",
        "these",
        "those",
        "with",
        "you",
        "your",
        "です",
        "ます",
        "これ",
        "それ",
        "この",
        "その",
        "こと",
        "ため",
        "よう",
        "ください",
        "お願いします",
    }
)
LOCAL_LLM_PAYLOAD_KEYS = frozenset(
    {
        "title",
        "summary",
        "conclusion_text",
        "conclusion_status",
        "keywords",
        "confidence",
    }
)
CONCLUSION_STATUSES = frozenset({"explicit", "inferred", "unknown"})
EXPLICIT_CONCLUSION_PATTERNS = (
    re.compile(r"\bDecision\s*:", re.IGNORECASE),
    re.compile(r"\bWe decided\b", re.IGNORECASE),
    re.compile(r"\bWe will\b", re.IGNORECASE),
    re.compile(r"\bAgreed\b", re.IGNORECASE),
    re.compile(r"\bLet's proceed with\b", re.IGNORECASE),
    re.compile(r"\bShip it\b", re.IGNORECASE),
    re.compile(r"決定"),
    re.compile(r"これで決まり"),
    re.compile(r"今回は.+で進め"),
    re.compile(r"にします"),
    re.compile(r"でいく"),
    re.compile(r"一旦これで"),
    re.compile(r"これでOK", re.IGNORECASE),
    re.compile(r"採用"),
)

# Keep the local LLM prompt contract centralized until prompt profiles are
# externalized under resources/prompts/. The helper functions below are the
# intended boundary for future profile-backed prompt loading.
LOCAL_LLM_PROMPT_TEMPLATE = """You summarize one intra-thread conversation segment for a downstream matching index.

Return exactly one JSON object and nothing else. Do not use markdown. Do not add reasoning.

Required JSON shape:
{
  "title": "short extracted phrase, max 8 words",
  "summary": "1-2 sentence conservative summary grounded only in the segment",
  "conclusion_text": null or "short quoted/near-quoted conclusion if explicitly present",
  "conclusion_status": "explicit" | "inferred" | "unknown",
  "keywords": ["3-8 concise keywords"],
  "confidence": 0.0
}

Rules:
- Use "unknown" and null conclusion_text when the segment has no clear conclusion.
- Use "explicit" only when a decision/resolution is directly stated.
- Assistant suggestions alone must not become explicit conclusions.
- If the segment only contains advice, options, planning, or unresolved discussion, use unknown.
- Use "inferred" only for a strongly implied outcome, not a guess.
- Prefer preserving the segment language for title and summary when possible.
- Translation is acceptable only if faithful.
- Do not invent conclusions, technologies, dates, owners, or outcomes.

Explicit conclusion markers include:
- English: "Decision:", "We decided", "We will", "Agreed", "Let's proceed with", "Use X", "Ship it"
- Japanese: "決定", "これで決まり", "今回は〜で進める", "〜にします", "〜でいく", "一旦これで", "これでOK", "採用"

If uncertain, return unknown.

Segment:
<<<
{segment_text}
>>>"""


class IntraThreadTopicSummaryError(RuntimeError):
    pass


class _PromptTokenizer(Protocol):
    def encode(self, text: str) -> list[str]:
        ...

    def decode(self, tokens: list[str]) -> str:
        ...


class _LocalPromptTokenizer:
    """Lightweight local prompt tokenizer used only for budget estimation.

    This is intentionally isolated and conservative. It preserves exact text
    through decode(), while giving the prompt path a token-counting boundary
    that can be replaced by a model-specific tokenizer later.
    """

    def encode(self, text: str) -> list[str]:
        tokens: list[str] = []
        for match in PROMPT_TOKEN_RE.finditer(text):
            value = match.group(0)
            if not value:
                continue
            if value.isspace() or len(value) <= PROMPT_TOKEN_CHUNK_CHARS:
                tokens.append(value)
                continue
            tokens.extend(
                value[index : index + PROMPT_TOKEN_CHUNK_CHARS]
                for index in range(0, len(value), PROMPT_TOKEN_CHUNK_CHARS)
            )
        return tokens

    def decode(self, tokens: list[str]) -> str:
        return "".join(tokens)


def intra_thread_topic_summaries_artifact_path(parsed_path: Path) -> Path:
    return intra_thread_topics_dir(parsed_path) / "topic-summaries.jsonl"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        handle = path.open("r", encoding="utf-8")
    except FileNotFoundError as exc:
        raise IntraThreadTopicSummaryError(f"segments artifact not found: {path}") from exc
    with handle:
        for line_no, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise IntraThreadTopicSummaryError(
                    f"invalid JSON in {path}:{line_no}: {exc.msg}"
                ) from exc
            if not isinstance(row, dict):
                raise IntraThreadTopicSummaryError(
                    f"invalid record in {path}:{line_no}: expected object"
                )
            rows.append(row)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(path)
    return path


def _compact_text(text: str) -> str:
    return " ".join(text.split()).strip()


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3].rstrip() + "..."


def _title_from_text(text: str) -> str:
    compact = _compact_text(text)
    if not compact:
        return ""
    first_line = next(
        (line.strip() for line in text.splitlines() if line.strip()),
        compact,
    )
    normalized = _compact_text(first_line)
    tokens = normalized.split()
    if len(tokens) > TITLE_MAX_TOKENS:
        normalized = " ".join(tokens[:TITLE_MAX_TOKENS])
    return _truncate(normalized, TITLE_MAX_CHARS)


def _keyword_tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    position = 0
    for token in TOKEN_RE.findall(normalized):
        token = token.strip()
        if not token or token in STOPWORDS or token.isdigit():
            continue
        if re.fullmatch(r"[a-z0-9_/-]+", token) and len(token) < KEYWORD_MIN_LENGTH:
            continue
        counts[token] = counts.get(token, 0) + 1
        first_seen.setdefault(token, position)
        position += 1
    ranked = sorted(counts, key=lambda item: (-counts[item], first_seen[item], item))
    return ranked[:KEYWORD_LIMIT]


def _prompt_hash() -> str:
    """Return the provenance hash for the active local LLM prompt contract."""
    payload = LOCAL_LLM_PROMPT_TEMPLATE.encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _load_prompt_tokenizer() -> _PromptTokenizer | None:
    """Return the active local tokenizer, or None to use character fallback."""
    try:
        return _LocalPromptTokenizer()
    except Exception:
        return None


def _truncate_segment_text_for_prompt_by_chars(segment_text: str) -> str:
    max_chars = (
        LOCAL_LLM_PROMPT_FALLBACK_HEAD_CHARS
        + len(LOCAL_LLM_PROMPT_TRUNCATION_SEPARATOR)
        + LOCAL_LLM_PROMPT_FALLBACK_TAIL_CHARS
    )
    if len(segment_text) <= max_chars:
        return segment_text
    return (
        segment_text[:LOCAL_LLM_PROMPT_FALLBACK_HEAD_CHARS]
        + LOCAL_LLM_PROMPT_TRUNCATION_SEPARATOR
        + segment_text[-LOCAL_LLM_PROMPT_FALLBACK_TAIL_CHARS :]
    )


def _truncate_segment_text_for_prompt(
    segment_text: str,
    *,
    tokenizer: _PromptTokenizer | None = None,
    max_prompt_tokens: int = LOCAL_LLM_MAX_PROMPT_TOKENS,
) -> str:
    """Shorten segment text for local LLM prompts with a token head+tail budget.

    The tokenizer is isolated so a model-specific tokenizer can replace the
    current local estimator. If tokenization is unavailable, the previous
    character-based head+tail fallback is used.
    """
    prompt_tokenizer = tokenizer or _load_prompt_tokenizer()
    if prompt_tokenizer is None:
        return _truncate_segment_text_for_prompt_by_chars(segment_text)

    template_without_segment = LOCAL_LLM_PROMPT_TEMPLATE.replace("{segment_text}", "")
    template_tokens = prompt_tokenizer.encode(template_without_segment)
    segment_tokens = prompt_tokenizer.encode(segment_text)
    available_segment_tokens = (
        max_prompt_tokens - len(template_tokens) - LOCAL_LLM_PROMPT_TOKEN_MARGIN
    )

    if available_segment_tokens <= 0:
        return ""
    if len(segment_tokens) <= available_segment_tokens:
        return segment_text

    separator_tokens = prompt_tokenizer.encode(LOCAL_LLM_PROMPT_TRUNCATION_SEPARATOR)
    content_budget = available_segment_tokens - len(separator_tokens)
    if content_budget <= 0:
        return ""
    if content_budget == 1:
        return prompt_tokenizer.decode(segment_tokens[:1])

    tail_budget = max(1, content_budget // 3)
    head_budget = content_budget - tail_budget
    return (
        prompt_tokenizer.decode(segment_tokens[:head_budget])
        + LOCAL_LLM_PROMPT_TRUNCATION_SEPARATOR
        + prompt_tokenizer.decode(segment_tokens[-tail_budget:])
    )


def _build_local_llm_prompt(segment_text: str) -> str:
    """Build the active local LLM prompt.

    This intentionally isolates prompt assembly so the template can later move
    to a prompt-profile resource without changing generation and fallback flow.
    """
    prompt_segment_text = _truncate_segment_text_for_prompt(segment_text)
    return LOCAL_LLM_PROMPT_TEMPLATE.replace("{segment_text}", prompt_segment_text)


def _has_explicit_conclusion_marker(segment_text: str) -> bool:
    return any(pattern.search(segment_text) for pattern in EXPLICIT_CONCLUSION_PATTERNS)


def _normalize_llm_keywords(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    keywords: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            return None
        normalized = _compact_text(item)
        if not normalized:
            return None
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        keywords.append(normalized)
        if len(keywords) == KEYWORD_LIMIT:
            break
    return keywords


def _validated_llm_fields(
    payload: dict[str, Any],
    *,
    segment_text: str,
) -> dict[str, Any] | None:
    if frozenset(payload) != LOCAL_LLM_PAYLOAD_KEYS:
        return None

    title = payload.get("title")
    summary = payload.get("summary")
    conclusion_text = payload.get("conclusion_text")
    conclusion_status = payload.get("conclusion_status")
    confidence = payload.get("confidence")
    keywords = _normalize_llm_keywords(payload.get("keywords"))

    if not isinstance(title, str) or not isinstance(summary, str):
        return None
    if conclusion_text is not None and not isinstance(conclusion_text, str):
        return None
    if conclusion_status not in CONCLUSION_STATUSES:
        return None
    if keywords is None:
        return None
    if not isinstance(confidence, (int, float)):
        return None
    normalized_confidence = float(confidence)
    if not 0.0 <= normalized_confidence <= 1.0:
        return None
    if conclusion_status == "unknown" and conclusion_text is not None:
        return None
    if conclusion_status == "explicit" and not _has_explicit_conclusion_marker(
        segment_text
    ):
        return None

    return {
        "title": _compact_text(title),
        "summary": _compact_text(summary),
        "conclusion_text": _compact_text(conclusion_text)
        if isinstance(conclusion_text, str)
        else None,
        "conclusion_status": conclusion_status,
        "keywords": keywords,
        "confidence": round(normalized_confidence, 4),
    }


def _local_llm_summary_row(
    *,
    heuristic_row: dict[str, Any],
    segment_text: str,
    client: LLMClient,
    model: str,
) -> dict[str, Any] | None:
    try:
        payload = generate_structured_json(
            client,
            model=model,
            prompt=_build_local_llm_prompt(segment_text),
            options={
                "temperature": 0.0,
                "num_predict": 320,
            },
        )
    except RuntimeError:
        return None

    fields = _validated_llm_fields(payload, segment_text=segment_text)
    if fields is None:
        return None

    row = {
        **heuristic_row,
        **fields,
        "source": "local_llm",
        "model": f"ollama/{model}",
        "prompt_variant": LOCAL_LLM_PROMPT_VARIANT,
        "prompt_hash": _prompt_hash(),
    }
    validator = load_intra_thread_topic_summary_validator()
    if list(validator.iter_errors(row)):
        return None
    return row


def _coerce_segment_row(
    row: dict[str, Any],
    *,
    path: Path,
    line_no: int,
) -> dict[str, Any]:
    if row.get("record_type") != "intra_thread_segment":
        raise IntraThreadTopicSummaryError(
            f"invalid segment record_type in {path}:{line_no}"
        )
    required = (
        "provider_id",
        "conversation_id",
        "segment_id",
        "start_index",
        "end_index",
        "message_ids",
        "message_count",
        "text_sha1",
    )
    missing = [key for key in required if key not in row]
    if missing:
        raise IntraThreadTopicSummaryError(
            f"segment row missing required field(s) in {path}:{line_no}: "
            f"{', '.join(missing)}"
        )
    message_ids = row.get("message_ids")
    if not isinstance(message_ids, list) or not message_ids:
        raise IntraThreadTopicSummaryError(
            f"segment row has invalid message_ids in {path}:{line_no}"
        )
    if any(
        not isinstance(message_id, str) or not message_id
        for message_id in message_ids
    ):
        raise IntraThreadTopicSummaryError(
            f"segment row has invalid message_ids in {path}:{line_no}"
        )
    if row.get("message_count") != len(message_ids):
        raise IntraThreadTopicSummaryError(
            f"segment row message_count mismatch in {path}:{line_no}"
        )
    return row


def _segment_text(
    *,
    parsed_path: Path,
    segment_row: dict[str, Any],
    messages_by_id: dict[str, Any],
) -> str:
    texts: list[str] = []
    for message_id in segment_row["message_ids"]:
        message = messages_by_id.get(message_id)
        if message is None:
            raise IntraThreadTopicSummaryError(
                f"segment {segment_row['segment_id']} references unknown message_id "
                f"in {parsed_path}: {message_id}"
            )
        if message.provider_id != segment_row["provider_id"]:
            raise IntraThreadTopicSummaryError(
                f"segment {segment_row['segment_id']} provider_id mismatch for "
                f"message_id={message_id}"
            )
        if message.conversation_id != segment_row["conversation_id"]:
            raise IntraThreadTopicSummaryError(
                f"segment {segment_row['segment_id']} conversation_id mismatch for "
                f"message_id={message_id}"
            )
        if message.text:
            texts.append(message.text)
    text = "\n\n".join(texts)
    text_sha1 = hashlib.sha1(text.encode("utf-8")).hexdigest()
    if text_sha1 != segment_row["text_sha1"]:
        raise IntraThreadTopicSummaryError(
            f"segment text_sha1 drift for {segment_row['segment_id']} in {parsed_path}: "
            f"expected {segment_row['text_sha1']}, got {text_sha1}"
        )
    return text


def build_intra_thread_topic_summary_row(
    *,
    segment_row: dict[str, Any],
    segment_text: str,
) -> dict[str, Any]:
    compact = _compact_text(segment_text)
    return {
        "record_type": "intra_thread_topic_summary",
        "schema_version": TOPIC_SUMMARY_SCHEMA_VERSION,
        "provider_id": segment_row["provider_id"],
        "conversation_id": segment_row["conversation_id"],
        "segment_id": segment_row["segment_id"],
        "start_index": segment_row["start_index"],
        "end_index": segment_row["end_index"],
        "message_ids": list(segment_row["message_ids"]),
        "message_count": segment_row["message_count"],
        "segment_text_sha1": segment_row["text_sha1"],
        "title": _title_from_text(segment_text),
        "summary": _truncate(compact, SUMMARY_MAX_CHARS),
        "conclusion_text": None,
        "conclusion_status": "unknown",
        "keywords": _keyword_tokens(segment_text),
        "confidence": 0.3 if compact else 0.0,
        "source": "heuristic",
    }


def _build_intra_thread_topic_summary_rows_with_stats(
    parsed_path: Path,
    *,
    source: str = "heuristic",
    model: str = DEFAULT_LOCAL_LLM_MODEL,
    base_url: str = DEFAULT_OLLAMA_BASE_URL,
    timeout_seconds: float = DEFAULT_OLLAMA_TIMEOUT_SECONDS,
    client: LLMClient | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if source not in {"heuristic", "local_llm"}:
        raise IntraThreadTopicSummaryError(
            "--source must be either 'heuristic' or 'local_llm'"
        )
    if source == "local_llm":
        if not isinstance(model, str) or not model.strip():
            raise IntraThreadTopicSummaryError("--model must be a non-empty string")
        if timeout_seconds <= 0:
            raise IntraThreadTopicSummaryError("--timeout-seconds must be > 0")

    segments_path = intra_thread_segments_artifact_path(parsed_path)
    segment_rows = [
        _coerce_segment_row(row, path=segments_path, line_no=line_no)
        for line_no, row in enumerate(_load_jsonl(segments_path), start=1)
    ]
    if not segment_rows:
        return [], {
            "heuristic_rows": 0,
            "local_llm_rows": 0,
            "local_llm_failures": 0,
        }

    try:
        messages = reconstruct_thread_messages(parsed_path)
    except IntraThreadTopicsError as exc:
        raise IntraThreadTopicSummaryError(str(exc)) from exc
    messages_by_id = {message.message_id: message for message in messages}

    llm_client = client
    if source == "local_llm" and llm_client is None:
        llm_client = OllamaClient(base_url=base_url, timeout=timeout_seconds)

    rows: list[dict[str, Any]] = []
    local_llm_rows = 0
    local_llm_failures = 0
    for segment_row in segment_rows:
        segment_text = _segment_text(
            parsed_path=parsed_path,
            segment_row=segment_row,
            messages_by_id=messages_by_id,
        )
        heuristic_row = build_intra_thread_topic_summary_row(
            segment_row=segment_row,
            segment_text=segment_text,
        )
        output_row = heuristic_row
        if source == "local_llm" and llm_client is not None:
            if not segment_text.strip():
                local_llm_failures += 1
            else:
                llm_row = _local_llm_summary_row(
                    heuristic_row=heuristic_row,
                    segment_text=segment_text,
                    client=llm_client,
                    model=model.strip(),
                )
                if llm_row is None:
                    local_llm_failures += 1
                else:
                    output_row = llm_row
                    local_llm_rows += 1
        rows.append(output_row)

    validator = load_intra_thread_topic_summary_validator()
    for index, row in enumerate(rows, start=1):
        errors = list(validator.iter_errors(row))
        if errors:
            raise IntraThreadTopicSummaryError(
                f"topic summary schema validation failed for row {index}: "
                f"{errors[0].message}"
            )
    return rows, {
        "heuristic_rows": len(rows) - local_llm_rows,
        "local_llm_rows": local_llm_rows,
        "local_llm_failures": local_llm_failures,
    }


def build_intra_thread_topic_summary_rows(
    parsed_path: Path,
    *,
    source: str = "heuristic",
    model: str = DEFAULT_LOCAL_LLM_MODEL,
    base_url: str = DEFAULT_OLLAMA_BASE_URL,
    timeout_seconds: float = DEFAULT_OLLAMA_TIMEOUT_SECONDS,
    client: LLMClient | None = None,
) -> list[dict[str, Any]]:
    rows, _stats = _build_intra_thread_topic_summary_rows_with_stats(
        parsed_path,
        source=source,
        model=model,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        client=client,
    )
    return rows


def write_intra_thread_topic_summaries(
    input_path: Path,
    *,
    overwrite: bool = False,
    source: str = "heuristic",
    model: str = DEFAULT_LOCAL_LLM_MODEL,
    base_url: str = DEFAULT_OLLAMA_BASE_URL,
    timeout_seconds: float = DEFAULT_OLLAMA_TIMEOUT_SECONDS,
    client: LLMClient | None = None,
) -> dict[str, Any]:
    parsed_files = discover_parsed_jsonl(input_path)
    written_paths: list[Path] = []
    total_rows = 0
    total_local_llm_rows = 0
    total_local_llm_failures = 0

    for parsed_path in parsed_files:
        output_path = intra_thread_topic_summaries_artifact_path(parsed_path)
        if output_path.exists() and not overwrite:
            raise IntraThreadTopicSummaryError(
                f"artifact already exists: {output_path} (rerun with --overwrite)"
            )
        rows, stats = _build_intra_thread_topic_summary_rows_with_stats(
            parsed_path,
            source=source,
            model=model,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            client=client,
        )
        written_paths.append(_write_jsonl(output_path, rows))
        total_rows += len(rows)
        total_local_llm_rows += stats["local_llm_rows"]
        total_local_llm_failures += stats["local_llm_failures"]

    return {
        "threads": len(parsed_files),
        "summaries": total_rows,
        "source": source,
        "local_llm_summaries": total_local_llm_rows,
        "local_llm_failures": total_local_llm_failures,
        "artifacts": written_paths,
    }
