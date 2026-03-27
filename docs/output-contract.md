# Output Contract (Markdown) — v1.1 (Exporter MVP Spec)

This document defines the **output format contract** between the Exporter, Viewer,
and future Apps SDK. It supersedes the legacy `v1.0` draft (Oct 2025).

---

## 💬 Message Heading Rules

Rules for each rendered message block:

- Heading pattern: `## [<role or name>] <local datetime>`
- Supported roles: `system`, `user`, `assistant`, `tool`
- Message content is emitted **verbatim** (code blocks, links, images preserved)
- Line endings MUST be `\n` (LF)
- Markdown MUST comply with **GFM (GitHub Flavored Markdown)**
- Current datetime rendering is timezone-aware, but not locale-formatted:
  `YYYY-MM-DD HH:MM`

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

## 🧩 Meta JSON (Planned — Not Yet Implemented)

> [!NOTE]
> `meta.json` generation and the `--with-meta` flag are **planned for a future release**.
> The specification below describes the intended design.

`meta.json` is optional but recommended for the Viewer and SDK integration.

It will be generated when `--with-meta` is set.

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

Current i18n behavior is narrower than a fully localized exporter:

* i18n is best-effort, non-blocking, and intentionally limited in scope
* missing locale sections or keys are acceptable; fallback behavior is the design
* for the canonical project-wide model, see `docs/requirements.md`

* `--locale` / `--lang` control CLI/help/runtime message localization and analyzer
  locale-backed phrase resources
* `--timezone` controls exporter timestamp conversion
* Locale resolution lives in `src/llm_logparser/core/i18n.py`
* Locale files live under `src/llm_logparser/i18n/`
* Locale files are best-effort YAML mappings and may contain:
  * `messages:` for scalar CLI/help/runtime/error text
  * `analysis:` for structured analyzer phrase resources
* Missing sections and missing keys are allowed; fallback behavior handles partial locale files safely
* Scalar message lookup falls back as:
  selected locale → `en-US` → raw key
* Analyzer resources fall back as:
  selected locale → `en-US`
* Short aliases such as `en` and `ja` are derived from discovered locale filenames when the language prefix is unambiguous
* Locale precedence is:
  `--locale` / `--lang` → `LLP_LOCALE` → `profiles.<name>.locale` → `en-US`
* Unknown locales resolve to `en-US`
* Parser/help output can pick up CLI locale before parser construction via raw argv scanning
* Config locale is applied only after config/profile resolution and does not override
  CLI or environment locale
* `analyze` follows the same locale precedence as the other runtime commands
* Argparse built-ins (`usage:`, parser-generated errors, built-in help boilerplate)
  are not localized

Output-contract caution:

* Human-readable CLI/help/runtime text is localized
* `analyze stats` / `analyze timeline` text summaries are intentionally English-only
* Human-readable Markdown timestamps are timezone-aware but not locale-formatted
* Stable machine-readable artifacts and field names remain English

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
| `meta.json`           | JSON     | planned  | Viewer metadata (not yet implemented) |
| `locale` / `timezone` | CLI settings | optional | Locale selects CLI/runtime text and analyzer resources; timezone affects human-readable timestamp rendering |
| `checksum`            | string   | planned  | SHA1 for diff detection (not yet implemented) |

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

* `text` is part of the canonical normalized message contract and is expected to equal:

  ```
  "\n".join(content.parts)
  ```

* adapters/parser own producing this normalized field in `parsed.jsonl`

* Additional / unknown fields MAY appear
  (tools MUST ignore what they don’t understand)

* If a required field is missing:

  * the record is skipped, or
  * parsing stops when `--fail-fast` is enabled

This schema is intentionally minimal and stable.
Future fields may be added **without breaking compatibility** as long as these rules hold.

> [!NOTE]
> The Exporter includes a defensive fallback that reconstructs text from
> `content.parts` when a malformed or incomplete normalized row is encountered.
> This is a resilience measure only. It does not redefine the canonical JSONL
> contract, does not create a second authoritative text-generation path, and
> does not shift responsibility away from adapters/parser for emitting normalized
> `text`.

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
