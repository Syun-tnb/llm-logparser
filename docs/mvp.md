# MVP Definition

## Scope (2025-10)
- Input: ChatGPT export logs (JSON array format)
- Output: JSONL shards + manifest.json
- Supported policies:
  - id_policy: composite (conv_id + msg_id)
  - text_policy: strip_control_chars, keep emoji

## Out of Scope
- JSONL input
- Other LLM providers (Claude, Gemini, etc.)
- Large scale (GB+) streaming parse

## Roadmap
- [ ] Add JSONL input support
- [ ] Provider adapters
- [ ] Streaming parser
