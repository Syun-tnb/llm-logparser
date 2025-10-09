# Provider Guide

## OpenAI (ChatGPT export)

- Input: JSON array or JSONL with objects containing `conversation_id`, `message_id`, `author`, `create_time`, `content`.
- Map to unified schema:
  - `conversation_id` → `conversation_id`
  - `message_id`     → `message_id`
  - `create_time`    → `ts` (epoch ms)
  - `author.role`    → `author_role`
  - `author.name?`   → `author_name`
  - `metadata.model?`→ `model`
  - `content`        → `content` (verbatim)

## Adding a Provider

1. Create adapter under `providers/<id>/adapter.py`.
2. Provide YAML mapping (`providers/<id>/mapping.yaml`) with field rules & fallbacks.
3. Ensure golden tests for sample → expected Markdown.
