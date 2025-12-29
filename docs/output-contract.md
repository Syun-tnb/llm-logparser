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
````

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

*Last updated: 2025-12-29*
*Author: Reyna (Exporter Spec Lead)*

