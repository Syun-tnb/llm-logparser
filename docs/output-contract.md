# Output Contract (Markdown) — v1.1 (Exporter MVP Spec)

This document defines the **output format contract** between the Exporter, Viewer,
and future Apps SDK. It supersedes the legacy `v1.0` draft (Oct 2025).

---

## 💬 Message Heading Rules

Rules for each rendered message block:

- Heading pattern: `## [<role or name>] <localized datetime>`
- Supported roles: `system`, `user`, `assistant`, `tool`
- Message content is emitted **verbatim** (code blocks, links, images preserved)
- Line endings MUST be `\n` (LF)
- Markdown MUST comply with **GFM (GitHub Flavored Markdown)**

---

## 🪶 Formatting Rules (GFM Compliance)

| Element      | Rule                                                                 |
| ------------ | -------------------------------------------------------------------- |
| Paragraphs   | Keep original line breaks                                            |
| Code blocks  | Use fenced blocks (``` ``` style)                                    |
| Inline code  | Use backticks                                                        |
| Quotes       | Preserve the leading `>` prefix; do not trim                        |
| Tables       | Use GFM pipe syntax (`\|` between columns)                           |
| Lists        | Use `-` or numbered lists (`1.`), indent with two spaces            |
| Escape policy| Escape minimal characters (`*`, `_`, `#`, `>`) only when necessary  |
| Encoding     | UTF-8, LF, no BOM                                                    |

Exporter output MUST lint cleanly under `markdownlint-cli2` using the shared
`.markdownlint.yaml`.

---

## 🧩 Meta JSON

`meta.json` is optional but recommended for the Viewer and SDK integration.

It is generated when `--with-meta` is set.

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
* Translations are resolved through `src/llm_logparser/core/i18n/{locale}.yaml`
* Dates are rendered using locale-aware formats (UTC internally)
* Missing keys fall back to English (`en-US`) — warnings may be logged in some cases

Example localized dates:

| Locale | Example                |
| ------ | ---------------------- |
| ja-JP  | 2025年10月18日 10:15      |
| en-US  | Oct 18, 2025, 10:15 AM |

---

## 🔁 Cache and Idempotency Rules

The Exporter follows Parser cache guidance (`§8.1` of requirements):

| Case | Condition | Action |
| ---- | --------- | ------ |

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

## 🏷 Schema Versioning (Compatibility Policy)

The normalized JSONL format is designed to evolve without breaking existing tools.

A schema version identifier MAY appear in one of the following locations:

* **Thread header (preferred, future)**

  ```json
  { "record_type": "thread", "schema_version": "1.1" }
  ```
* or in `meta.json` (when generated)

  ```json
  { "schema_version": "1.1" }
  ```

### Rules

* Consumers MUST treat unknown fields as optional.
* When `schema_version` is missing, it MUST be interpreted as **`1.0`**.
* New fields MAY be added as long as:

  * existing fields keep their meaning, and
  * files remain readable by older tools.

Breaking changes (field removal / semantic change) require a **major schema version bump** and MUST be documented in this file.

> Goal: tools can safely process archives produced by older and newer versions of the parser without manual migration.

---

## 📄 Canonical JSONL Schema (`parsed.jsonl`)

The Parser produces a **canonical**, thread-scoped JSONL file.
This file is the **single source of truth** for the Exporter, Viewer, and future tools.

Each line is one JSON object.
Line endings MUST be `\n` (LF).

---

### Record types

Two record types exist:

#### 1️⃣ Thread header

```json
{
  "record_type": "thread",
  "provider_id": "<provider>",
  "conversation_id": "<uuid>",
  "message_count": <int>
}
```

* Appears **exactly once**, at the very top of the file
* Describes metadata for the entire thread

---

#### 2️⃣ Message record

```json
{
  "record_type": "message",
  "provider_id": "<provider>",
  "conversation_id": "<uuid>",
  "message_id": "<id>",
  "parent_id": "<id|null>",
  "role": "system|user|assistant|tool",
  "ts": <epoch_ms>,
  "content": {
    "content_type": "text",
    "parts": ["...", "..."]
  },
  "text": "<flattened text>"
}
```

---

### Rules

* Messages are sorted **chronologically**
  (`ts`, then `message_id` as a tie-breaker)

* `text` is always present and equals:

  ```
  "\n".join(content.parts)
  ```

* Additional / unknown fields MAY appear
  (tools MUST ignore what they don’t understand)

* If a required field is missing:

  * the record is skipped, or
  * parsing stops when `--fail-fast` is enabled

This schema is intentionally minimal and stable.
Future fields may be added **without breaking compatibility** as long as these rules hold.

---

### Determinism & compatibility

* Same inputs → **same parsed output** (stable ordering)
* Viewer / Exporter MUST rely only on fields defined here
* Additional provider-specific fields MAY appear but MUST NOT break consumers

> If a future version adds fields, they MUST be backward-compatible.

---

### Why this layer matters

* Keeps provider quirks out of higher layers
* Enables streaming processing and caching
* Makes diffs and audits predictable
* Allows future adapters without touching Exporter

---

*Last updated: **2026-01-02***
Maintainer: Exporter/Docs Team (original draft by Reyna)
