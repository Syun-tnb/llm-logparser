# LLM Log Parser — Requirements (MVP)

## 1. Goal & Scope

The project provides a CLI tool that:

* reads exported conversation logs from LLM services (JSON / JSONL / NDJSON)
* normalizes them into a stable intermediate format (JSONL, one thread per file)
* exports readable Markdown (GFM) grouped by conversation threads

The MVP focuses on:

* CLI only (no full GUI)
* OpenAI ChatGPT export as the first provider
* reliability, reproducibility, and offline processing

Future work may include a viewer app, more providers, and richer UI — but these are **out of MVP scope**.

---

## 2. Architecture Overview

To keep the system extensible, the pipeline is split into four layers:

1. **Provider Adapter**
   Extracts and normalizes provider-specific JSON structures into a common schema.

2. **Core Parser**
   Outputs normalized JSONL, one file per conversation thread.

3. **Exporter**
   Converts JSONL into Markdown (GFM), flattening text where necessary.

4. **Viewer (future optional)**
   Reads exported Markdown and renders HTML.

Key principle:

> **Parser extracts structure. Exporter handles formatting.**

---

## 3. Input Assumptions

* Input format: **JSON / JSONL / NDJSON**

* Encoding: **UTF-8 (BOM-less preferred)**

* Files may be:

* large

* partially broken

* multilingual

* contain metadata mixed with messages

Exports always represent **full conversation history**, not diffs.

---

## 4. Normalized Schema (Intermediate JSONL)

Each thread becomes one JSONL file:

* first line: thread metadata (`record_type = "thread"`)
* following lines: messages (`record_type = "message"`)

The schema remains stable across providers to avoid breaking downstream tools.

Parser does **not**:

* merge message text
* decode Unicode escape sequences
* remove nulls

Exporter is responsible for readability.

---

## 5. Markdown Export (GFM)

The exporter reads parsed JSONL files and produces Markdown:

* grouped **per conversation thread**
* sorted chronologically
* optional splitting by **size** or **message count**
* YAML front-matter with thread metadata

Output location example:

```
artifacts/output/{provider}/thread-{conversation_id}/
```

Markdown should remain:

* easy to read for humans
* stable for version control
* compatible with common tools (GitHub, VS Code, Obsidian, etc.)

Splitting is intended only for **large threads**, not for logical grouping.
Date-based splitting is **not part of the MVP**, because conversations commonly span days and splitting by calendar boundaries tends to fragment meaningful context.

---

## 6. CLI Commands (MVP)

During development the CLI is invoked via the Python module:

```bash
PYTHONPATH=src python3 -m llm_logparser.cli ...
```

A dedicated `llm-logparser` console script may be added in a later release.

The CLI provides the following subcommands:

* `parse`
  Normalize raw provider log exports and write normalized JSONL files.

* `export`
  Generate Markdown (GFM) from a normalized thread JSONL file.

* `chain`
  Convenience command that runs **parse → export** for all threads in one shot.
  This is implemented as a separate subcommand, not as a `--chain` option.

Two additional subcommands are reserved for future work:

* `viewer` (placeholder)
  Reserved for a future lightweight HTML/Markdown viewer.
  The current implementation only logs a TODO warning.

* `config` (placeholder)
  Reserved for runtime configuration helpers.
  The current implementation only logs a TODO warning.

Global options:

* `--locale` / `--lang` to control CLI message localization.
* `--log-level` to adjust verbosity (DEBUG / INFO / WARNING / ERROR / CRITICAL).

---

## 7. Caching & Duplicates

Exports from providers are full snapshots.
The parser uses a cache to avoid unnecessary reprocessing.

Rules:

* new thread → parse
* unchanged thread → skip
* updated thread → replace existing output

Cache is JSON, local only.

---

## 8. Internationalization (i18n)

CLI user-facing messages and Markdown headers are localized via a dedicated i18n layer.

- All CLI messages MUST go through the i18n layer.
  - No user-facing strings are hard-coded with a fixed language in source code.
- The only required locale is `en-US`.
  - Other locales (e.g. `ja-JP`) are best-effort and may be partially translated.
- When a translation for the selected locale is missing, the message MUST fall back to `en-US`.
- `--locale` and `--timezone` are applied consistently across CLI and exporters.
- If both `--locale` and `--lang` are supplied, `--locale` takes precedence
  (`--lang` exists for compatibility and may be removed in future versions).

---

## 9. Security & Privacy

MVP runs **fully offline**:

* no network access unless explicitly enabled
* logs stay local
* optional masking rules for sensitive text

Goal: safe use in private and corporate environments.

---

## 10. Performance Targets (MVP)

* up to ~2GB input per file
* streaming processing (no full file in memory)
* 1GB processed in ~60 seconds on SSD systems (target)

Parallelism and advanced optimization may come later.

---

## 11. Versioning

The project follows **SemVer**.

Breaking changes require a major version bump.
Intermediate schemas are versioned and backward-compatibility oriented.

---

## 12. Out-of-Scope (for MVP)

To keep the MVP realistic, the following are **explicitly deferred**:

* full-feature GUI
* cloud sync / network APIs
* automatic anonymization or PII detection
* complex indexing or search backends
* real-time collaborative features

These may be revisited gradually as the project matures.

---

## Summary

MVP = **reliable CLI pipeline**:

> Export JSON → Normalize JSONL → Export Markdown

Stable schema, offline-first, and clear separation of responsibilities make the tool easy to extend without rewriting core logic.
