# Provider Guide

## OpenAI (ChatGPT export)

- Input: ChatGPT full export JSON containing `mapping` with nested message nodes.
  Also supports JSON array or JSONL with individual conversation objects.
- The adapter (`providers/openai/chatgpt/adapter.py`) processes conversation trees:
  1. Extracts nodes from the `mapping` field
  2. Builds a parent–child graph
  3. Linearizes via BFS (timestamp-ordered)
  4. Outputs normalized message records

### Normalized output fields

| Raw field | Normalized field | Notes |
|-----------|-----------------|-------|
| `conversation_id` / `id` / `uuid` | `conversation_id` | Falls back to SHA1 hash if missing |
| `message.id` / node key | `message_id` | — |
| `parent` (node) | `parent_id` | `null` if no parent |
| `message.author.role` | `role` | Falls back to `"unknown"` |
| `message.create_time` | `ts` | Epoch milliseconds |
| `message.content` | `content` | `{ "content_type": "…", "parts": [...] }` |
| *(derived from parts)* | `text` | `"\n".join(content.parts)` |

Adapter responsibility note:

- adapters are the primary boundary that produces normalized top-level `text`
- exporter-side reconstruction from `content.parts` is only a defensive fallback for malformed or incomplete normalized rows
- that fallback does not redefine the canonical contract or move normalization responsibility out of adapters

> **Note:** In the current MVP, normalization is handled internally by Python code.
> The file `mapping.sample.yaml` in `docs/examples/` is provided only as a **sample** for future external mapping support.
> It is **not yet used** by the CLI or parser.
> The current runtime path is adapter-based: the parser imports `src/llm_logparser/core/providers/<provider>/adapter.py`
> and normalizes provider exports there.

### Extractor

The `extract` subcommand uses a dedicated extractor (`providers/openai/extractor.py`) that:

- Locates a specific conversation by `conversation_id`
- Applies config-driven sanitization
- Writes `extract.json` plus `extract.meta.json`

Default sanitize behavior stays enabled for compatibility. The canonical profile
shape is:

```yaml
sanitize:
  enabled: true
  replacement: REDACTED
  scope: content_parts
  extra_keywords: [credential]
  mask_patterns:
    - acct-\d+
```

Supported scopes:

- `content_parts`
- `all_strings`

## Adding a Provider

1. Create a package under `src/llm_logparser/core/providers/<id>/`.
2. Implement `adapter.py` with a function `adapter(conversation: dict) -> list[dict]` and `get_adapter() -> Callable`.
3. Optionally implement `extractor.py` with `get_extractor()`.
4. Ensure golden tests for sample → expected Markdown.

## Example Mapping Specs

- [`docs/examples/providers/openai/chatgpt.yaml`](/Users/tanabeshunji/Documents/llm-logparser/docs/examples/providers/openai/chatgpt.yaml)
- [`docs/examples/providers/anthropic/claude.yaml`](/Users/tanabeshunji/Documents/llm-logparser/docs/examples/providers/anthropic/claude.yaml)
- [`docs/examples/providers/xai/grok.yaml`](/Users/tanabeshunji/Documents/llm-logparser/docs/examples/providers/xai/grok.yaml)

These files are documentation/examples only today.
They are not active runtime config until external provider mapping support is implemented.

- `openai/chatgpt.yaml`: example/spec for current ChatGPT normalization shape
- `anthropic/claude.yaml`: example/spec aligned with the current Claude adapter
- `xai/grok.yaml`: documentation/example only until a Grok runtime adapter exists

## Mistral AI (Le Chat export)

- Provider ID: `mistral_ai`
- Service/family: `le_chat`
- Input: one JSON file per thread where the top-level value is a non-empty list of message objects
- Detection is schema-first:
  - top-level JSON must be a non-empty list
  - sampled items must contain `id`, `chatId`, `role`, `createdAt`
  - sampled items must contain either `content` or `contentChunks`
  - `contentChunks: null` is accepted

### Normalized mapping

| Raw field | Normalized field | Notes |
|-----------|-----------------|-------|
| `chatId` | `conversation_id` | Shortened with the shared ID helper for consistency with existing providers |
| `id` | `message_id` | Shortened with the shared ID helper |
| *(not available in export)* | `parent_id` | Always `null` in v1 |
| `role` | `role` | Lowercased with small alias handling |
| `createdAt` | `ts` | ISO-8601 → epoch milliseconds |
| `content` | `text` | Canonical top-level text comes directly from `content` |
| `contentChunks[*].text` / `content` | `content.parts` | Uses chunk texts first, then falls back to `[content]`, then `[]` |
| provider-specific fields | `meta` | Includes `service="le_chat"` plus raw IDs and extra provider fields |

## Google (Gemini My Activity)

- Provider ID: `google`
- Service/family: `gemini_activity`
- Input: Google Takeout My Activity JSON arrays containing Gemini activity records
- Import mode: event-first

Google Gemini My Activity is imported as deterministic event-scoped mini-threads because the export does not expose stable conversation or thread identifiers. This is intentional: it keeps parse output truthful and deterministic instead of implying multi-turn fidelity that the source does not provide.

### Detection

- top-level JSON must be a non-empty list
- sampled items must contain `title`, `time`, and `products`
- at least some sampled items must contain `safeHtmlItem`
- and Gemini activity must be indicated by `header` / `products`

### Normalized mapping

| Raw field | Normalized field | Notes |
|-----------|-----------------|-------|
| synthetic hash over raw event fields | `conversation_id` | One source event becomes one synthetic mini-thread |
| synthetic hash over raw event fields + role | `message_id` | Deterministic per event and role |
| `title` | user `text` | `送信したメッセージ: ` is stripped when present |
| `safeHtmlItem[*].html` | assistant `text` | HTML is converted to plain text with a real HTML parser |
| `time` | `ts` | ISO-8601 → epoch milliseconds |
| provider-specific fields | `meta` | Includes `service="gemini_activity"` and raw activity fields |

### Current limitations

- exact original conversation boundaries are not guaranteed by the source export
- canonical parse v1 does not do heuristic time-gap clustering or semantic re-threading
- future higher-level re-threading can be added separately without redefining the canonical import
