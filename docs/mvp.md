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
- [x] Streaming parser (via `ijson` when available)
- [x] Markdown exporter with thread splitting (size / count / auto)
- [x] `extract` subcommand (Gemini-compatible JSON output)
- [x] JSON Schema validation (`--validate-schema`)

## Out of Scope (for now)
- Other LLM providers (Claude, Gemini, xAI) — adapter stubs exist but are not yet implemented
- Full GUI
- Cloud sync / network APIs

## Roadmap
- [ ] Multi-provider adapters (Claude, Gemini, xAI)
- [ ] Minimal HTML Viewer
- [ ] Config file loading from `config.yaml`
