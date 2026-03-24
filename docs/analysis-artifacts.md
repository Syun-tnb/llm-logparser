# Analysis Artifacts and Processing Stages

This document defines the architectural boundaries between the canonical
parse output, **deterministic analyzer sidecars/views (L1)**, the optional
**SQLite analysis index (L2)**, and optional higher derived layers in
`llm-logparser`.

The goal is to ensure that:

- `parse` remains **lightweight and deterministic**
- deterministic and rebuildable artifacts remain the stable base
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
deterministic analyzer commands / sidecars (L1)
│
▼
optional SQLite index (L2)
│
▼
optional higher derived layers (L3 / L4 / future GUI-oriented outputs)

```

Each stage has a clearly defined responsibility.

| Stage | Purpose |
|------|------|
| parse | canonical normalization and deterministic parse-time artifact generation |
| deterministic analyzer commands (L1) | local stats/datasheet/timeline views plus `token_stats.json` and `metrics.json` sidecar artifacts |
| optional SQLite index (L2) | deterministic query accelerator built from canonical or canonical-derived artifacts |
| optional higher layers (L3 / L4) | additive model-derived or downstream artifacts such as local-LLM or API-backed outputs |

Future GUI-oriented data products follow the same rule as L3/L4: they are
additional derived layers on top of canonical or deterministic artifacts, not
replacements for them.

A core design principle is:

> **Later stages must never require re-running earlier stages if artifacts already exist.**

Boundary rules:

- `parsed.jsonl` is the canonical source of truth
- L1 and L2 artifacts are deterministic and rebuildable
- L2 is an optional analysis index, not canonical storage
- L3, L4, and future GUI-oriented outputs are additive higher layers
- higher-layer outputs must not replace or redefine canonical or deterministic artifacts
- users who do not use L2/L3/L4 or GUI-related features should remain on the same canonical/deterministic workflow

> [!NOTE]
> `analyze stats` computes from canonical `parsed.jsonl`.
> `analyze datasheet` may opportunistically reuse `thread_stats.json` and
> `metrics.json` sidecar artifacts when they are already present, but it still
> falls back to canonical `parsed.jsonl` when they are missing or unusable.


---

# 2. Canonical Source of Truth

The canonical normalized dataset is:

```

parsed.jsonl

```

All higher-level artifacts must be derivable from this dataset.

This is the stable, deterministic base of the system. Canonical correctness
remains anchored here even when additional sidecars, indexes, reports, or
future model-derived outputs exist.

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

The analyzer currently adds deterministic sidecar artifacts in the same thread directory:

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
  - `schema_version`: currently `"1.0"`
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
  - it now emits an explicit `schema_version` for contract stability
  - analyzers may consume it as a lightweight summary in the future, but it is not the canonical source for analysis
  - analyzer correctness must still come from `parsed.jsonl`, even if `thread_stats.json` is missing or stale

SQLite correspondence for downstream consumers:

- `thread_stats.json.character_count` currently populates both SQLite
  `threads.character_count` and `threads.characters_total` with the same value
  for legacy compatibility; `characters_total` is the preferred canonical name
  going forward
- `thread_stats.json.first_timestamp` / `last_timestamp` are stored in SQLite `threads.first_timestamp` / `last_timestamp` after conversion from UTC ISO 8601 text to epoch milliseconds
- `thread_stats.json.other_role_breakdown` is currently imported into SQLite
  `threads.other_role_breakdown` as JSON-serialized `TEXT`

`message_windows.jsonl` is a deterministic thread-local text artifact derived
only from canonical message rows. The first version uses simple fixed-size
contiguous message windows with preserved role sequence and message traceability.

Contract:

- schema: `src/llm_logparser/core/schemas/message_windows.schema.json`
- purpose: stable machine-readable row contract for one deterministic message window
- required per-row fields emitted today:
  - `record_type`: always `message_window`
  - `schema_version`: currently `"1.0"`
  - `provider_id`: provider identifier for the source thread
  - `conversation_id`: canonical thread/conversation identifier
  - `window_id`: deterministic window identifier within the thread
  - `message_ids`: ordered canonical message identifiers included in the window
  - `roles`: ordered normalized roles for the included messages, using `unknown` when needed
  - `message_count`: number of messages in the window
  - `char_count`: total characters across the included canonical message text
  - `ts_start`: earliest message timestamp in the window as epoch milliseconds, or `null`
  - `ts_end`: latest message timestamp in the window as epoch milliseconds, or `null`
  - `text`: deterministic concatenated window text
- notes:
  - `message_windows.jsonl` is a parse-time thread-local artifact, not canonical storage
  - rows now emit an explicit `schema_version` for contract stability
  - the schema describes the emitted row contract as it exists today; it does not imply future chunking or L3 structure

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

The primary contract remains the normalized top-level `text` emitted during
parse. When downstream consumers fall back to `content.parts`, they are doing
defensive recovery over an already normalized artifact. That fallback is for
resilience only; it does not redefine canonical text semantics or move text
normalization responsibility away from adapters/parser.

For analyzer-generated sidecars, canonical text resolution is:

1. use top-level `text` when it is a string
2. otherwise, if `content.parts` is a list, join its string elements with `"\n"`
3. otherwise use `""`

`token_stats.json` exposes this per message as `text_source` with values
`text`, `content.parts`, or `empty`. `metrics.json` uses the same canonical
text fallback chain internally for character/diversity/heuristic inputs, but
does not emit per-message `text_source`.


---

# 4. Parse Stage (Canonical Base)

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
These artifacts form the stable rebuildable base above parse.

Implemented today:

- `analyze stats`
- `analyze timeline`
- `analyze tokens`
- `analyze metrics`

Separately implemented:

- `analyze sqlite-build` builds the optional SQLite index described below

Artifact boundary:

- `token_stats.json` and `metrics.json` are deterministic L1 sidecars
- `analysis.db` is a separate optional L2 index artifact
- future L3/L4/GUI outputs must be separate additive artifacts, not replacements
  for canonical or deterministic ones

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
- heuristic `safety.refusal` plus additive safety intervention tracking
- heuristic `interaction.revision`
- additive `user_effort` metrics derived from assistant → user timing and character totals

The refusal, safety intervention, and revision phrase lists are locale-backed resources under
`src/llm_logparser/i18n/` and fall back to `en-US` when a selected locale
does not define the relevant key.

Current heuristic behavior:

- refusal detection is a normalized substring match on assistant messages only
- intervention detection is a normalized substring match on assistant messages only
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
  - `safety`: refusal counters plus broader intervention counters/rates derived from locale-backed refusal and caveat indicators
  - `interaction`: revision, correction, clarification, and retry counters/rates derived from locale-backed cues
  - `user_effort`:
    - `rapid_revisions`: count of assistant → next user transitions whose delta is under `60` seconds
    - `response_length_ratio`: `assistant_characters / user_characters`, or `null` when the user denominator is `0`
    - `negative_deltas`: count of assistant → next user transitions whose timestamp delta is negative
    - `human_read_time`: summary of assistant → next user deltas with `avg_seconds`, `median_seconds`, `min_seconds`, `max_seconds`, `sample_count`, `excluded_long_gaps`, and the fixed threshold `session_gap_seconds`
- reproducibility notes:
  - character-based metrics use the same canonical text fallback chain described above
  - aggregate token fields are read from sibling `token_stats.json`, so metrics inherit the tokenizer basis selected by `analyze tokens`
  - diversity prefers the same `tiktoken` encoding recorded in `token_stats.json.tokenizer.resolved_encoding`
  - if that tokenizer metadata is unavailable or unusable, diversity falls back to whitespace-split pieces for both the unique-token count and total-token denominator
  - `diversity.type_token_ratio` and `diversity.unique_token_ratio` currently use the same formula: `unique_units / total_units`
  - refusal and revision phrase matching uses normalized text (`casefold` + collapsed whitespace) against locale-backed YAML cues
  - `safety.intervention_count` is message-based: each assistant message counts at most once even if it matches both refusal and caveat indicators
  - `safety.trigger_types` exposes subtype totals for `refusal` and `caveat`, so one assistant message may increment both subtype counters
  - `user_effort.human_read_time` excludes assistant → user gaps larger than `3600` seconds from its summary statistics and records them as `excluded_long_gaps`
  - `user_effort.negative_deltas` counts assistant → user transitions with negative timestamp deltas, and those transitions are excluded from rapid-revision and read-time statistics
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

The implemented Layer 2 SQLite index is an optional per-provider database built
by `analyze sqlite-build` as a query accelerator:

```text
<outdir>/<provider>/analysis.db
```

This database is **not canonical state**. It is an implemented optional
analysis index. It must be fully rebuildable from
existing canonical or canonical-derived artifacts such as:

- `thread_stats.json`
- `parsed.jsonl`
- `message_windows.jsonl`

The SQLite build step must not mutate those artifacts.

Current thread-level SQLite correspondence includes:

- `threads.character_count` and `threads.characters_total`, which currently
  store the same value for legacy compatibility; `characters_total` is the
  preferred canonical name going forward
- `threads.other_role_breakdown`, which stores imported
  `thread_stats.json.other_role_breakdown` as JSON-serialized `TEXT`

L2 expectations:

- deterministic and rebuildable
- optional for users who need indexed querying
- additive to the canonical/deterministic base
- safe to delete and rebuild without loss of canonical correctness

For v1.4, the intended direction is narrow:

- SQLite remains a deterministic query/index layer
- it is not yet being expanded into a broader analysis query engine
- ingesting `token_stats.json` or `metrics.json` into SQLite is deferred and
  out of scope for v1.4
- future L3/L4/GUI-oriented outputs should remain separate from L2 by command
  surface, artifact/output layer, and, if needed later, storage/DB boundary
- `analysis.db` is not a catch-all store for every future derived output

This preserves the project's canonical-first and deterministic-first design
while leaving room for future additive layers.

If `analysis.db` is missing, canonical and deterministic analyzer workflows
must remain unaffected.

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

The implemented `analyze stats`, `analyze datasheet`, and `analyze timeline` commands perform
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

`analyze stats` now includes an additive `research_summary` section in JSON mode
and a matching compact text section. This summary remains deterministic and local:

- temporal aggregates are derived from canonical thread timestamp spans
- turn-taking aggregates summarize per-thread `characters_user / characters_assistant`
- safety aggregates may reuse existing `metrics.json` sidecars for thread-level
  refusal/intervention counts, but must still work without them by recomputing
  deterministic canonical heuristics
- structural aggregates are intentionally lightweight heuristics based on
  canonical message text and `content.parts`, including fenced code block detection

`analyze datasheet` builds on the same research-oriented summary concepts, but
renders them as an appendix-ready Markdown report by default, with optional JSON.
It may opportunistically reuse existing `thread_stats.json` and `metrics.json`
sidecars, but it must still work correctly from canonical `parsed.jsonl` alone.

Example commands:

```

llm-logparser analyze stats
llm-logparser analyze datasheet
llm-logparser analyze timeline

```

These commands may perform full dataset scans if needed.

The analyze command family separates concerns deliberately:

```text
parsed.jsonl (canonical)
    -> analyze tokens     -> token_stats.json
    -> analyze metrics    -> metrics.json
    -> analyze stats      -> aggregation and exploratory summaries
    -> analyze datasheet  -> report layer for appendix-ready Markdown/JSON
```

In this model:

- `analyze stats` is the aggregation/exploration layer
- `analyze metrics` is the per-thread sidecar artifact layer
- `analyze datasheet` is the reporting layer built on the same deterministic
  research-oriented summary concepts
- existing sidecar artifacts are preferred when available, but canonical
  `parsed.jsonl` remains the source of truth


---

# 9. Optional Higher Layers (L3 / L4 / GUI-Oriented Outputs)

L3 and L4 refer to optional higher layers outside the deterministic base.

Examples include:

- local LLM analysis
- external/API-backed LLM analysis
- RAG pipelines
- embedding generation
- search indexing
- clustering
- conversation summarization
- future GUI-oriented caches, annotations, or derived summaries

These systems should operate on artifacts produced by the canonical base, L1,
and optionally L2.

Typical inputs:

```

parsed.jsonl
chunks.jsonl
thread_stats.json
analysis outputs

```

Architectural rule:

- higher-layer outputs are additive derived artifacts
- they must not replace `parsed.jsonl`, `token_stats.json`, `metrics.json`, or `analysis.db`
- they may be model-derived and non-deterministic
- they remain opt-in and isolated from users who do not use them

Provenance expectation:

Future higher-layer artifacts should carry their own provenance and metadata
appropriate to the layer that produced them. At minimum, they should be able to
identify their source thread or dataset inputs and the model/configuration basis
used to derive them. This document does not define a full L3/L4 schema, but the
artifacts must remain clearly separate from the canonical/deterministic base.

This separation ensures that:

- L3/L4 pipelines can evolve independently
- the parser remains deterministic
- deterministic artifacts remain stable and rebuildable
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

Canonical correctness must always be reconstructible from `parsed.jsonl`.
Deterministic L1/L2 artifacts must be rebuildable from canonical or lower-layer
deterministic artifacts.

Higher-layer L3/L4/GUI artifacts may depend on model execution, but they must
remain additive and must not replace the deterministic base.

### Incrementality

Artifacts should support incremental updates without full recomputation when possible.

### Isolation

Optional indexes, model-derived outputs, and future GUI-oriented data products
must remain isolated from users who do not enable them.


---

# 11. Architectural Summary

| Stage | Allowed Work |
|------|------|
| parse | normalization, thread-local stats, lightweight accumulators |
| analyze (L1/L2) | deterministic sidecars/views plus optional rebuildable SQLite indexing |
| L3/L4 | semantic analysis, embeddings, search, LLM workflows, other additive higher-layer outputs |

Or more simply:

```

parse   = produce canonical deterministic building blocks
L1/L2   = produce deterministic and rebuildable analysis artifacts
L3/L4   = perform optional additive semantic or AI processing

```


---

# 12. Rationale

This architecture ensures:

- fast parsing even for large datasets
- deterministic canonical artifacts
- flexible downstream analysis
- compatibility with local LLM pipelines
- minimal coupling between stages


---

# 13. Future Extensions

Possible future additions include:

- chunk manifests
- conversation window artifacts
- timeline precomputation

Any new artifact must follow the principles described in this document.
In particular, future L3/L4/GUI-oriented outputs must be additive, optional,
and isolated from the canonical/deterministic base rather than redefining it.
