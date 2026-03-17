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
