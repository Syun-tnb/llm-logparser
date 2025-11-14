# Output Contract (Markdown / HTML) — v1.1 (Exporter MVP Spec)

This document defines the **output format contract** between the Exporter, Viewer, and future Apps SDK.  
It supersedes the legacy `v1.0` draft (Oct 2025).

---

## 🗂 Location & Structure

- Root directory:  
```

artifacts/output/{provider_id}/thread-{conversation_id}/

```
- Files:
```

parsed.jsonl                  # Parser output (normalized)
thread-{conversation_id}__{chunk_key}.md   # Exported Markdown
meta.json                     # Optional metadata summary

```

- `{chunk_key}` is ASCII-only.  
Suggested patterns:
- Date split: `2025-11-15_part01`
- Size split: `size20mb_p01`
- Count split: `count8000_p01`
- No split: omit the suffix entirely.

---

## 🧾 YAML Front Matter

Each Markdown begins with a YAML block that provides thread metadata:

```yaml
---
thread: "6811ff1a-2bac-8005-a2ae-5d8e63d7ee3e"
provider: "openai"
title: "Reyna – GPUヒートシンク上より出撃"
messages: 132
models: ["gpt-4o", "gpt-5"]
range: "2025-10-01 〜 2025-10-18"
locale: "ja-JP"
timezone: "Asia/Tokyo"
updated: "2025-10-18T10:15:00Z"
checksum: "f34d2b9a1a98e24b1c3f3dcb01c7e5cbd7a2a4ff"
---
```

### Notes

* `range` uses **localized date formatting**, not ISO timestamps.
* `updated` and `checksum` are optional but recommended for cache validation.
* All timestamps (internal and meta.json) use **UTC ISO 8601**.
* `locale` and `timezone` affect *rendered strings only* — timestamps remain UTC internally.

---

## 💬 Message Section

Each message is rendered in strict timestamp order:

```markdown
## [User] 2025-10-18 10:00
本文...

## [Reyna] 2025-10-18 10:01
本文...
```

Rules:

* Heading pattern: `## [<role or name>] <localized datetime>`
* Supported roles: `system`, `user`, `assistant`, `tool`
* Content is **verbatim** (code blocks, links, images preserved)
* Line endings must be `\n` (LF)
* Markdown follows **GFM (GitHub Flavored Markdown)** conventions

---

## 🪶 Formatting Rules (GFM Compliance)

| Element       | Rule                                                          |
| ------------- | ------------------------------------------------------------- |
| Paragraphs    | Keep original line breaks                                     |
| Code blocks   | Use fenced blocks (` ``` `)                                   |
| Inline code   | Use backticks                                                 |
| Quotes        | Preserve `>` prefix; no trimming                              |
| Tables        | GFM pipe syntax (`\|` between columns)                        |
| Lists         | Use `-` or numbered lists (`1.`), indent with two spaces      |
| Escape policy | Escape minimal characters (`*`, `_`, `#`, `>`) only as needed |
| Encoding      | UTF-8, LF, no BOM                                             |

Exporter output must lint cleanly under `markdownlint-cli2` using the shared `.markdownlint.yaml`.

---

## 🧩 Meta JSON

Optional but recommended for Viewer and SDK integration.
Generated when `--with-meta` is set.

```json
{
  "conversation_id": "6811ff1a-2bac-8005-a2ae-5d8e63d7ee3e",
  "message_count": 132,
  "models": ["gpt-4o"],
  "date_range": ["2025-10-01", "2025-10-18"],
  "exported_at": "2025-10-18T10:15:00Z",
  "split_policy": "date:week",
  "files": [
    "thread-6811ff1a-2bac-8005-a2ae-5d8e63d7ee3e__2025-10-18_part01.md",
    "thread-6811ff1a-2bac-8005-a2ae-5d8e63d7ee3e__2025-10-18_part02.md"
  ]
}
```

### Meta JSON Usage

* Viewer lists threads and their chunk files.
* Apps SDK or future GUI can query `message_count` and `date_range` for filtering.
* File paths are relative to the thread directory.

---

## 🌐 i18n and Locale Behavior

* Controlled via CLI: `--locale <lang-REGION>` and `--timezone <IANA zone>`
* Translations are resolved through `src/llm_logparser/i18n/{locale}.yaml`
* Dates and numbers use `babel`-compatible formatting
* Missing keys fall back to English (`en-US`) with `[WARN][i18n]` notice

Example localized date:

| Locale | Example                |
| ------ | ---------------------- |
| ja-JP  | 2025年10月18日 10:15    |
| en-US  | Oct 18, 2025, 10:15 AM |

---

## 🔁 Cache and Idempotency Rules

The Exporter follows Parser cache guidance (`§8.1` of requirements):

| Case        | Condition                              | Action                    |
| ----------- | -------------------------------------- | ------------------------- |
| NEW         | Thread not in cache                    | Generate new output       |
| SKIP        | Same `update_time` and `message_count` | Skip export               |
| REPLACE     | `update_time` newer                    | Overwrite existing thread |
| WARN & SKIP | `update_time` older                    | Log warning, skip         |
| ERROR       | Cache inconsistency                    | Raise LP8xxx              |

Cache file: `artifacts/cache/{provider_id}_cache.json`

Exporter **never updates** cache directly; it consumes parser metadata only.

---

## 🚫 Non-goals (MVP)

* Append-only incremental writes — **not implemented**
* Partial regeneration — **not supported**
* Attachments download — **not included** (only metadata retained)
* HTML sanitization — handled by Viewer layer

---

## 🔮 Future Additions (for v1.2+)

* `attachments/` directory for extracted images or files
* HTML viewer (`index.html + list.html + page.html`)
* `meta.schema_version` for backward compatibility
* Optional compression: `--compress` → `thread-*.md.gz`

---

## ✅ Summary

| Element               | Type     | Required | Description                       |
| --------------------- | -------- | -------- | --------------------------------- |
| `parsed.jsonl`        | JSONL    | ✔        | Parser output (thread + messages) |
| `thread-*.md`         | Markdown | ✔        | Human-readable log, GFM format    |
| `meta.json`           | JSON     | optional | Viewer metadata                   |
| `locale` / `timezone` | string   | optional | For localized rendering           |
| `checksum`            | string   | optional | SHA1 for diff detection           |

Exporter output must remain **deterministic** under identical inputs and locale settings.

---

*Last updated: 2025-11-15*
*Author: Reyna (Exporter Spec Lead)*

