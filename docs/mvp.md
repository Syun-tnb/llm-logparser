# MVP Definition

## Scope (2025-10)
- Input: ChatGPT export logs (JSON array / JSONL / NDJSON)
- Output: JSONL shards + Markdown (GFM) export
- Supported policies:
  - id_policy: composite (conv_id + msg_id)
  - text_policy: strip_control_chars, keep emoji

## Implemented (since initial MVP)
- [x] JSONL / NDJSON input support
- [x] Provider adapter architecture (OpenAI/ChatGPT implemented)
- [x] Anthropic/Claude adapter support
- [x] Streaming parser (via `ijson` when available)
- [x] Markdown exporter with thread splitting (size / count / auto)
- [x] `extract` subcommand (Gemini-compatible JSON output)
- [x] JSON Schema validation (`--validate-schema`)
- [x] Config file loading from `config.yaml` (auto-discovery + profiles for CLI defaults)

## Out of Scope (for now)
- Other LLM providers beyond the current OpenAI and Anthropic adapters
- Full GUI
- Cloud sync / network APIs
- External provider mapping YAML as active runtime config
- xAI/Grok runtime adapter support

## Roadmap
- [ ] More provider adapters (Gemini, xAI)
- [ ] Minimal HTML Viewer
- [ ] External provider mapping YAML support
