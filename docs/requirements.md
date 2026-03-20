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

The CLI is invoked via the installed console script:

```bash
llm-logparser <command> [options]
```

Alternatively, during development it can be invoked via the Python module:

```bash
PYTHONPATH=src python3 -m llm_logparser.cli <command> [options]
```

The CLI provides the following subcommands:

* `parse`
  Normalize raw provider log exports and write normalized JSONL files.
  Supports `--validate-schema` to validate normalized messages against `message.schema.json`.

* `export`
  Generate Markdown (GFM) from a normalized thread JSONL file.

* `extract`
  Extract a single conversation by `--conversation-id` and output it as Gemini-compatible JSON.
  Applies config-driven sanitization, enabled by default for compatibility.
  The sanitize policy controls replacement text, scope, custom field-name keywords,
  and regex-based masking.

* `chain`
  Convenience command that runs **parse → export** for all threads in one shot.
  This is implemented as a separate subcommand, not as a `--chain` option.
  Supports `--validate-schema` to validate during the parse phase.

* `analyze`
  Deterministic analysis on canonical thread artifacts.
  Current modes include `stats`, `timeline`, `tokens`, `metrics`, and `sqlite-build`.
  `tokens` writes `token_stats.json`.
  `metrics` writes `metrics.json` and includes heuristic refusal / revision sections.

One additional subcommand is reserved for future work:

* `viewer` (placeholder)
  Reserved for a future lightweight HTML/Markdown viewer.
  The current implementation only logs a TODO warning.

* `config`
  Lightweight runtime configuration helpers.
  Supports `config path`, `config show`, and `config validate`.

Global options:

* `--locale` / `--lang` to control CLI/help/runtime localization.
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

Current i18n behavior is split between scalar UI/runtime messages and structured
analyzer phrase resources:

- i18n is best-effort, not strict.
- Translation completeness is not required.
- Missing locale sections and keys are acceptable.
- Fallback behavior is expected design, not an error condition.
- Locale files are user-extensible and must not break execution when partial.
- Locale files live under `src/llm_logparser/i18n/`.
- Locale files are best-effort YAML mappings and may contain `messages:` and/or
  `analysis:`.
- Localized surfaces are:
  CLI/help/runtime/error messages from `messages:` and analyzer heuristic
  resources from `analysis:`.
- Not localized by design are:
  `analyze stats` / `analyze timeline` text summaries, stable
  machine-readable artifacts and schema keys, and argparse built-ins
  (`usage:`, parser-generated errors, built-in help boilerplate).
- The canonical fallback locale is `en-US`.
- Scalar message lookup falls back as:
  selected locale → `en-US` → raw key.
- Analyzer resource lookup falls back as:
  selected locale → `en-US`.
- Short language aliases are auto-derived from discovered locale filenames when
  the language prefix is unambiguous.
- Locale precedence is:
  `--locale` / `--lang` → `LLP_LOCALE` → `profiles.<name>.locale` → `en-US`.
- Unknown or unsupported locales resolve to `en-US`.
- Parser/help output can pick up CLI locale before full parser construction via
  raw argv scanning.
- Profile locale is applied only after config/profile resolution and must not
  override CLI or environment locale.
- `analyze` follows the same locale precedence as the other runtime commands.
- Argparse built-ins (`usage:`, parser-generated errors, built-in help boilerplate)
  are not localized.
- There is no top-level config `locale` and no system-locale fallback.
- Locale selection affects CLI/help/runtime text and analyzer YAML resources.
  Stable machine-readable artifacts are not localized by default.

### Deferred / Non-Goals (i18n)

The following items are intentionally out of scope for the current i18n design.
They may be revisited in the future, but are not required for correctness,
stability, or extensibility of the system.

* System locale fallback (`LANG`, `LC_ALL`)
* Development-time warnings for missing translation keys
* Structured error code system for i18n (e.g., LP6xxx)
* Localization of argparse built-in messages (`usage:`, parser errors, etc.)

Rationale:

The current i18n model prioritizes:

* deterministic behavior
* safe fallback to `en-US` or raw keys
* minimal maintenance overhead
* YAML-driven, community-friendly extensibility

These items improve completeness or developer experience,
but do not provide sufficient value relative to their complexity
for the current phase.

Status: deferred / non-blocking

---

## 9. Security & Privacy

MVP is **offline-first**, with one current caveat:

* parse/export and most analysis flows run locally
* `analyze tokens` / `analyze metrics` rely on `tiktoken`, which may fetch
  encoding assets on first use and then use a local cache afterward
* logs stay local
* optional masking rules for sensitive text

Goal: safe use in private and corporate environments.

---

## 10. Performance Targets (MVP)

* **Input size:** up to ~2GB per export file (JSON / JSONL / NDJSON)
* **Streaming processing for parsing:**

  * JSONL / NDJSON are processed line-by-line
  * JSON arrays are streamed when `ijson` is available
  * Large JSON arrays **without `ijson`** are considered out of scope for the MVP
* The parser should avoid loading the entire export file into memory.
  Individual threads may be materialized in memory during normalization.
* **Performance target (non-strict):**

  * ~1GB processed in ~60 seconds on typical SSD systems (Python 3.x, single process)
  * This is a best-effort goal, not a strict guarantee.
* Parallelism and advanced optimizations are explicitly out of scope for the MVP.

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

Stable schema, local-first analysis, and clear separation of responsibilities make the tool easy to extend without rewriting core logic.
