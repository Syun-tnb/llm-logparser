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

Exporter reads parsed JSONL and produces Markdown:

* grouped per conversation thread
* sorted chronologically
* optional splitting by size, count, or date
* YAML front-matter for metadata

Output location example:

```
artifacts/output/{provider}/thread-{conversation_id}/
```

Markdown remains human-readable and tool-friendly (GitHub, VS Code, Obsidian, etc.).

---

## 6. CLI Commands (MVP)

The CLI provides:

* `parse` — normalize logs and write JSONL
* `export` — generate Markdown (and optional meta files)
* `config` — manage runtime configuration
* `viewer` — reserved (minimal HTML preview in the future)

A `--chain` option allows `parse → export` in one run.

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

* CLI messages and Markdown headers support localization
* `--locale` and `--timezone` are applied consistently
* English remains the default fallback

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
