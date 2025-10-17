# Output Contract (Markdown) — Minimal, Implementable v1

## Location
- Root: `artifacts/output/{provider_id}/thread-{conversation_id}/`
- File name: `thread-{conversation_id}__{chunk_key}.md`
  - `chunk_key` MUST be ASCII-only. Suggested patterns:
    - Date: `YYYY-MM-DD[_partNN]` (e.g., `2025-10-09_part01`)
    - Size: `size20mb_pNN`
    - Count: `count8000_pNN`

## Front Matter (YAML)
YAML front matter MUST appear as the first block:

```yaml
---
thread: "<conversation_id>"
provider: "<provider_id>"
messages: <int>            # number of messages in THIS file
range:
  start: "YYYY-MM-DDTHH:mm:ssZ"
  end:   "YYYY-MM-DDTHH:mm:ssZ"
models: ["gpt-4o", "gpt-5"]   # optional
locale: "en-US"
timezone: "Asia/Tokyo"
schema_version: "1.0"
---
```

## Message Section
Each message renders as:

```
## [YYYY-MM-DD HH:mm] <role> [<author_name?>]
<content...>
```

- `role` ∈ {`system`,`user`,`assistant`,`tool`}
- Content is **verbatim** (code blocks/links preserved).
- Lines MUST be separated by `\n` (LF).

## Meta JSON (Optional)
When produced, place alongside Markdown as `meta.json`:

```json
{
  "thread": "abc123",
  "file": "thread-abc123__2025-10-09_part01.md",
  "messages": 3,
  "range": {"start":"2025-10-01T00:00:00Z","end":"2025-10-02T12:34:00Z"},
  "split": {"by":"size","size_mb":20,"index":1,"total":1}
}
```

## Invariants
- File/dir names: ASCII only.
- Message order: strictly ascending timestamp.
- No HTML sanitization in exporter (viewer handles it).
- All timestamps in front matter are **UTC ISO 8601** with `Z`.
- Locale/timezone only affect **rendered labels**, not timestamps.

## Re-parse & Idempotency (Full Export Inputs)

This tool assumes **every provider export is a full snapshot**.

### Thread-level decision

- Let `update_time` be the thread’s last-updated timestamp (ISO8601 UTC).
- Comparison rule:
  - If the new `update_time` **equals** the cached one → **SKIP** (unchanged)
  - If the new `update_time` **is newer than** the cached one → **REPLACE** (regenerate full thread)
  - If the new `update_time` **is older than** the cached one → **WARN & SKIP**
    (e.g. the user accidentally parsed an outdated export)

> `last_message_id` may be recorded for diagnostics only. It does not affect the decision above.

### Fallback when `update_time` is unavailable
- **MVP policy:** treat as **REPLACE** on re-parse for safety. (Heuristics like count/fingerprint may be introduced later behind a flag.)

### Non-goals (MVP)
- Append-only incremental writes are **not** performed when `update_time` differs (always full re-split).
- Source JSON is **never modified**.
