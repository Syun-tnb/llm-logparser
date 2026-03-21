# Analysis Artifacts and Processing Stages

This document defines the architectural boundaries between **parse**, **deterministic analyzer sidecars/views**, the optional **SQLite analysis index (L2)**, and downstream processing in `llm-logparser`.

The goal is to ensure that:

- `parse` remains **lightweight and deterministic**
- reusable artifacts are produced early enough for **local LLM pipelines**
- optional indexed or downstream computation is layered cleanly on top

This document describes **what data is produced at each stage** and **why**.


---

# 1. Processing Model

`llm-logparser` is structured into four logical stages.

```

raw provider export
│
▼
parse
│
▼
deterministic analyzer commands / sidecars
│
▼
optional SQLite index (L2) or downstream / L3
(local LLM, search, indexing, etc.)

```

Each stage has a clearly defined responsibility.

| Stage | Purpose |
|------|------|
| parse | canonical normalization and deterministic parse-time artifact generation |
| deterministic analyzer commands | local stats/timeline views plus `token_stats.json` and `metrics.json` sidecars |
| optional SQLite index (L2) | query accelerator built from canonical or canonical-derived artifacts |
| L3 | external consumers (local LLM pipelines, embeddings, search) |

A core design principle is:

> **Later stages must never require re-running earlier stages if artifacts already exist.**

> [!NOTE]
> `analyze stats` currently recomputes from canonical `parsed.jsonl` rather than
> consuming `thread_stats.json`. This keeps correctness anchored to the source
> of truth today; a future optimization may use `thread_stats.json` when present.


---

# 2. Canonical Source of Truth

The canonical normalized dataset is:

```

parsed.jsonl

```

All higher-level artifacts must be derivable from this dataset.

Key properties:

- deterministic
- append-safe
- provider-independent schema
- streaming friendly


---

# 3. Current Artifact Layout

The current parser produces the following directory structure.

```text
<outdir>/
  <provider>/
    manifest.json
    thread-<conversation_id>/
        parsed.jsonl
        thread_stats.json
        message_windows.jsonl
```

Each thread is stored in an isolated directory.

Thread artifact:

```text
thread-<conversation_id>/
    parsed.jsonl
    thread_stats.json
    message_windows.jsonl
```

The analyzer currently adds deterministic sidecars in the same thread directory:

```text
thread-<conversation_id>/
    parsed.jsonl
    token_stats.json
    metrics.json
```

`thread_stats.json` is a cheap deterministic thread-local artifact generated
during parse from the same canonical message rows written to `parsed.jsonl`.
It provides lightweight counts and timestamp-derived metadata for downstream
L3/local-LLM pipelines without requiring a later thread rescan.

Contract:

- schema: `src/llm_logparser/core/schemas/thread_stats.schema.json`
- purpose: stable parse-time thread-local summary for one canonical thread
- required top-level fields emitted today:
  - `artifact_type`: always `thread_stats`
  - `provider_id`: provider identifier for the thread
  - `conversation_id`: canonical thread/conversation identifier
  - `message_count`: total message records in the thread
  - `character_count`: total characters across canonical message `text`
  - `first_timestamp`: earliest message timestamp as UTC ISO 8601, or `null`
  - `last_timestamp`: latest message timestamp as UTC ISO 8601, or `null`
  - `conversation_span_seconds`: integer span between first/last timestamps, or `null`
  - `user_messages`: count of messages whose raw role is `user`
  - `assistant_messages`: count of messages whose raw role is `assistant`
  - `other_roles`: count of all other or missing roles
  - `characters_user`: characters from messages whose raw role is `user`
  - `characters_assistant`: characters from messages whose raw role is `assistant`
  - `other_role_breakdown`: sorted mapping of each non-`user`/`assistant` raw role label to its count, using `unknown` for missing roles
- notes:
  - `thread_stats.json` is parse-time metadata, not an analyzer-generated sidecar
  - it does not currently emit `schema_version`
  - analyzers may consume it as a lightweight summary in the future, but it is not the canonical source for analysis
  - analyzer correctness must still come from `parsed.jsonl`, even if `thread_stats.json` is missing or stale

SQLite correspondence for downstream consumers:

- `thread_stats.json.character_count` maps to SQLite `threads.characters_total`
- `thread_stats.json.first_timestamp` / `last_timestamp` are stored in SQLite `threads.first_timestamp` / `last_timestamp` after conversion from UTC ISO 8601 text to epoch milliseconds
- `thread_stats.json.other_role_breakdown` is not currently imported into SQLite; it remains available only in the JSON artifact

`message_windows.jsonl` is a deterministic thread-local text artifact derived
only from canonical message rows. The first version uses simple fixed-size
contiguous message windows with preserved role sequence and message traceability.

The `parsed.jsonl` file contains:

- one thread metadata record
- multiple message records

Example structure:

```json
{"record_type":"thread","provider_id":"openai","conversation_id":"abc","message_count":42}
{"record_type":"message", ...}
{"record_type":"message", ...}
```

The provider-level `manifest.json` acts as a lightweight global index.

Example:

```json
{
  "schema_version": "1.3",
  "provider": "openai",
  "index": {
    "threads": [
      {
        "conversation_id": "abc",
        "path": "thread-abc/parsed.jsonl",
        "count": 42,
        "ts_min": 1710000000000,
        "ts_max": 1710003600000
      }
    ]
  }
}
```

The manifest enables:

- quick thread discovery
- lightweight metadata inspection
- incremental parsing (skip unchanged threads)

without scanning all thread files.


### Shared Deterministic Derivation Layer

L1 deterministic analysis should be implemented as reusable helpers over canonical
`parsed.jsonl` records, not embedded directly in CLI handlers.

Current shared responsibilities include:

- discovering `parsed.jsonl` files from a file or directory input
- iterating canonical message records
- extracting timestamps in a normalized way
- computing cheap thread-local metrics such as message counts, character counts,
  role counts, and first/last timestamps
- detecting canonical header metadata
- resolving canonical message text from `text` with `content.parts` fallback
- normalizing roles and small deterministic numeric helpers

These helpers are intended to be reusable both from `analyze` subcommands and
from future parse-time thread-local artifact generation.

For analyzer-generated sidecars, canonical text resolution is:

1. use top-level `text` when it is a string
2. otherwise, if `content.parts` is a list, join its string elements with `"\n"`
3. otherwise use `""`

`token_stats.json` exposes this per message as `text_source` with values
`text`, `content.parts`, or `empty`. `metrics.json` uses the same canonical
text fallback chain internally for character/diversity/heuristic inputs, but
does not emit per-message `text_source`.


---

# 4. Parse Stage (L1)

The `parse` stage performs provider normalization and generates thread-local artifacts.

### Design Goals

- deterministic output
- minimal computational overhead
- single-pass processing
- streaming friendly
- no cross-thread dependency

### Allowed Operations

During `parse`, the system **may generate artifacts that are naturally available during the parsing loop**.

These operations must be **O(1) incremental updates** or **thread-local calculations**.

Allowed categories:

| Category | Example |
|------|------|
| normalization | provider → canonical schema |
| thread-local metrics | message counts, char counts |
| time extraction | first/last timestamps |
| deterministic chunking | message windows |
| lightweight aggregation | counters and min/max updates |

### Examples of Valid Parse-Time Artifacts

Thread-local artifacts:

```

parsed.jsonl
thread_stats.json
message_windows.jsonl
chunks.jsonl

```

Global lightweight accumulator:

```

analysis_manifest.json

```

The manifest may contain simple accumulators such as:

```

threads
messages
characters_total
user_messages
assistant_messages
first_timestamp
last_timestamp

```

These values must be computable **incrementally during parsing**.


### Forbidden Operations in Parse

The parse stage **must not perform operations requiring global dataset scans**.

Forbidden examples:

- global sorting
- ranking threads
- computing top-N statistics
- timeline bucketing across all threads
- semantic analysis
- embeddings
- SQLite indexing
- LLM calls

These tasks belong to **analyze or downstream processing**.


### Canonical-Based Chunking

All chunking and message window artifacts must be derived from
the canonical normalized dataset (`parsed.jsonl`).

Chunking must never operate directly on raw provider exports.

---

# 5. Deterministic Analyzer Sidecars

Implemented deterministic analyzer commands read canonical `parsed.jsonl` and
produce optional sidecars or rendered views without changing parse-time behavior.

Implemented today:

- `analyze stats`
- `analyze timeline`
- `analyze tokens`
- `analyze metrics`

Separately implemented:

- `analyze sqlite-build` builds the optional SQLite index described below

Current thread-local analyzer artifacts:

Analyzer sidecar schema-version policy:

- `token_stats.json` and `metrics.json` currently emit `schema_version: "1.0"`
- within major version `1`, additive fields are intended to remain backward-compatible
- removing fields, changing field meaning, or otherwise breaking existing consumers requires a new major schema version
- consumers should ignore unknown additive fields when reading analyzer sidecars

### `token_stats.json`

Derived from canonical message text only.

Includes:

- tokenizer metadata
- thread-level token totals
- per-role token counts
- per-message token counts

Current backend:

- `tiktoken`

Runtime caveat:

- `tiktoken` may perform a one-time network fetch on first use to download
  encoding assets
- later runs use the local cache

Contract:

- schema: `src/llm_logparser/core/schemas/token_stats.schema.json`
- purpose: stable machine-readable token accounting sidecar for one thread
- generation source: `analyze tokens` over canonical `parsed.jsonl`
- required top-level fields: `artifact_type`, `schema_version`, `provider_id`,
  `conversation_id`, `tokenizer`, `summary`, `by_role`, `messages`
- important nested structures:
  - `tokenizer`: tokenizer family/library metadata plus the resolved model or encoding
  - `summary`: message, turn, and token totals plus average and text-fallback counters
  - `by_role`: per-role `messages` / `tokens` counters
  - `messages`: per-message `message_id`, normalized `role`, `token_count`, and `text_source`
- incremental behavior:
  - default CLI behavior rebuilds and overwrites an existing `token_stats.json`
  - `analyze tokens --skip-existing` leaves an existing `token_stats.json` untouched
  - `analyze tokens --dry-run` previews detected threads and planned sidecar actions without writing files

### `metrics.json`

Derived from `parsed.jsonl` plus `token_stats.json`.

Includes:

- deterministic ratio / token / character / distribution / diversity metrics
- heuristic `safety.refusal`
- heuristic `interaction.revision`

The refusal and revision phrase lists are locale-backed resources under
`src/llm_logparser/i18n/` and fall back to `en-US` when a selected locale
does not define the relevant key.

Current heuristic behavior:

- refusal detection is a normalized substring match on assistant messages only
- revision detection compares consecutive user messages using cue matching and normalized similarity
- very short user messages are ignored for revision counting to reduce false positives
- correction beats clarification when both subtype cues match; otherwise the revision is counted as a retry
- revision candidate minimum normalized length is `8`
- normalized similarity threshold is `0.78`

Contract:

- schema: `src/llm_logparser/core/schemas/metrics.schema.json`
- purpose: stable machine-readable derived metrics sidecar for one thread
- generation source: `analyze metrics` over canonical `parsed.jsonl` plus sibling `token_stats.json`
- dependency: `token_stats.json` must already exist from `analyze tokens`
- required top-level fields: `artifact_type`, `schema_version`, `provider_id`,
  `conversation_id`, `ratios`, `tokens`, `characters`, `distribution`,
  `diversity`, `safety`, `interaction`
- important nested structures:
  - `ratios`, `tokens`, `characters`, `distribution`, `diversity`: deterministic numeric summaries
  - `safety`: refusal counters and refusal rate derived from locale-backed refusal indicators
  - `interaction`: revision, correction, clarification, and retry counters/rates derived from locale-backed cues
- reproducibility notes:
  - character-based metrics use the same canonical text fallback chain described above
  - aggregate token fields are read from sibling `token_stats.json`, so metrics inherit the tokenizer basis selected by `analyze tokens`
  - diversity prefers the same `tiktoken` encoding recorded in `token_stats.json.tokenizer.resolved_encoding`
  - if that tokenizer metadata is unavailable or unusable, diversity falls back to whitespace-split pieces for both the unique-token count and total-token denominator
  - `diversity.type_token_ratio` and `diversity.unique_token_ratio` currently use the same formula: `unique_units / total_units`
  - refusal and revision phrase matching uses normalized text (`casefold` + collapsed whitespace) against locale-backed YAML cues
- incremental behavior:
  - default CLI behavior rebuilds and overwrites an existing `metrics.json`
  - `analyze metrics --skip-existing` leaves an existing `metrics.json` untouched
  - when `metrics.json` is missing, `token_stats.json` must already exist from `analyze tokens`
  - `analyze metrics --dry-run` previews detected threads and planned sidecar actions without writing files

Reasons:

provider logs may contain irregular role sequences  
(e.g. tool calls, system inserts, retries)

different providers expose different role vocabularies

raw exports may include partial or provider-specific structures

By deriving chunks from canonical messages:

role normalization is already complete

timestamps are normalized

message ordering is deterministic

Chunking logic must not assume a strict
user → assistant → user → assistant pattern.

Instead, chunk boundaries should be determined using
message sequence and role-aware heuristics.

---

# 6. Optional SQLite Accelerator (L2)

An optional per-provider SQLite database may be built later as a query
accelerator:

```text
<outdir>/<provider>/analysis.db
```

This database is **not canonical state**. It must be fully rebuildable from
existing canonical or canonical-derived artifacts such as:

- `thread_stats.json`
- `parsed.jsonl`
- `message_windows.jsonl`

The SQLite build step must not mutate those artifacts.

---

# 7. Global Accumulator (Parse-Time)

Parse may maintain a **lightweight global accumulator**.

Purpose:

- avoid re-scanning large datasets
- provide quick summary metadata
- support fast CLI inspection

Constraints:

- must be incremental
- must not require cross-thread sorting
- must not depend on future threads

Typical fields:

```

threads
messages
characters_total
characters_user
characters_assistant
first_timestamp
last_timestamp

```

The accumulator **must remain optional**.

If it is missing, the system must still be able to recompute results from `parsed.jsonl`.


---

# 8. Deterministic Analyzer Views

The implemented `analyze stats` and `analyze timeline` commands perform
deterministic dataset-level aggregation and presentation-level processing.

These commands operate on canonical input and **do not modify canonical parse results**.

Typical responsibilities:

| Category | Example |
|------|------|
| sorting | top threads by messages |
| filtering | role-based analysis |
| aggregation | timeline buckets |
| summarization | dataset statistics |
| CLI formatting | human-readable views |

Example commands:

```

llm-logparser analyze stats
llm-logparser analyze timeline

```

These commands may perform full dataset scans if needed.


---

# 9. L3 / Downstream Processing

L3 refers to systems outside the core parser.

Examples include:

- local LLM analysis
- RAG pipelines
- embedding generation
- search indexing
- clustering
- conversation summarization

L3 systems should operate on artifacts produced by L1 and L2.

Typical inputs:

```

parsed.jsonl
chunks.jsonl
thread_stats.json
analysis outputs

```

This separation ensures that:

- L3 pipelines can evolve independently
- the parser remains deterministic
- heavy compute stays outside the core tool


---

# 10. Artifact Design Principles

Artifacts produced by `llm-logparser` must follow these rules.

### Deterministic

Running parse multiple times on identical input must produce identical artifacts.

### Locality

Prefer **thread-local artifacts** over global artifacts.

This enables partial processing and incremental datasets.

### Reconstructibility

All artifacts must be reconstructible from `parsed.jsonl`.

### Incrementality

Artifacts should support incremental updates without full recomputation when possible.


---

# 10. Architectural Summary

| Stage | Allowed Work |
|------|------|
| parse | normalization, thread-local stats, lightweight accumulators |
| analyze | cross-thread computation, sorting, aggregation |
| L3 | semantic analysis, embeddings, search, LLM workflows |

Or more simply:

```

parse   = produce deterministic building blocks
analyze = assemble cross-thread views
L3      = perform semantic or AI processing

```


---

# 11. Rationale

This architecture ensures:

- fast parsing even for large datasets
- deterministic canonical artifacts
- flexible downstream analysis
- compatibility with local LLM pipelines
- minimal coupling between stages


---

# 12. Future Extensions

Possible future additions include:

- chunk manifests
- conversation window artifacts
- SQLite optional indexes
- timeline precomputation

Any new artifact must follow the principles described in this document.
