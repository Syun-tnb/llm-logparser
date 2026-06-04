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

`thread_stats.json` is a cheap deterministic thread-local artifact derived
from the same canonical message rows written to `parsed.jsonl`.
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
  - `user_messages`: count of messages whose canonical normalized role is `user`
  - `assistant_messages`: count of messages whose canonical normalized role is `assistant`
  - `other_roles`: count of all other or missing roles
  - `characters_user`: characters from messages whose canonical normalized role is `user`
  - `characters_assistant`: characters from messages whose canonical normalized role is `assistant`
  - `other_role_breakdown`: sorted mapping of each non-`user`/`assistant` canonical normalized role label to its count, using `unknown` for missing or unexpected roles
- notes:
  - `thread_stats.json` is a deterministic L1 artifact, not canonical storage
  - it may be materialized during parse for convenience, but its conceptual ownership remains L1
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

`message_windows.jsonl` is a deterministic L1 thread-local segmentation artifact
derived only from canonical message rows. It is intentionally a thin
message-membership substrate rather than a rendered semantic window.

Optional L3 token-dictionary artifacts under `l3/token-dictionary/` may also
be consumed by later L3 cross-thread analysis as additive evidence. These
signals remain rebuildable and optional; they do not become canonical or L1/L2
requirements.

Contract:

- schema: `src/llm_logparser/core/schemas/message_windows.schema.json`
- purpose: stable machine-readable row contract for one deterministic candidate span
- required per-row fields emitted today:
  - `record_type`: always `message_window`
  - `schema_version`: currently `"3.0"`
  - `provider_id`: provider identifier for the source thread
  - `conversation_id`: canonical thread/conversation identifier
  - `window_id`: deterministic window identifier within the thread
  - `message_ids`: ordered canonical message identifiers included in the window
  - `char_count`: total characters across the included canonical message text
  - `ts_start`: earliest message timestamp in the window as epoch milliseconds, or `null`
  - `ts_end`: latest message timestamp in the window as epoch milliseconds, or `null`
  - `window_size`: configured window size used to generate the row
  - `window_stride`: configured stride used to generate the row
- notes:
  - `message_windows.jsonl` is an L1 deterministic artifact, not canonical storage
  - it may be materialized during parse for convenience or performance, but its conceptual ownership remains L1
  - it is a deterministic candidate-span substrate, not a semantic unit
  - `message_ids` are the primary deterministic anchor of the artifact
  - semantic consumers must reconstruct message text and roles from canonical `parsed.jsonl` using the ordered `message_ids`
  - current L3 preview/topic flows use an explicit reconstruction layer rather
    than inferring message boundaries by splitting rendered window text
  - the artifact intentionally does not carry rendered semantic text, role sequences, or other convenience fields that duplicate canonical message storage
  - rows now emit an explicit `schema_version` for contract stability
  - window IDs remain deterministic sequential IDs in emission order; changing
    size or stride changes which message spans receive those IDs, but not the
    deterministic ordering rule itself
  - omitted stride preserves legacy non-overlapping behavior by defaulting the
    stride to the configured size
  - this Step 3 contract change is intentionally breaking; previously generated downstream L3 artifacts that depended on older `message_windows.jsonl` convenience fields should be regenerated
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
- computing cheap thread-local metrics such as message counts, character counts,
  role counts, and first/last timestamps
- detecting canonical header metadata
- reading canonical top-level `text`
- reading canonical normalized roles
- converting canonical epoch-millisecond timestamps into derived UTC ISO 8601
  strings or span seconds
- small deterministic numeric helpers

These helpers are intended to be reusable both from `analyze` subcommands and
from deterministic L1 artifact materialization paths, including convenience
writes that happen during parse.

### Role Boundary Rule

For deterministic L1 artifacts and metrics:

- canonical normalized roles are the only allowed semantic role representation
- raw/provider role labels must not influence shared L1 logic or outputs
- raw roles may survive only in explicitly non-semantic pass-through paths
  outside L1, such as indexing or display-only utilities

The primary contract is the top-level canonical `text` emitted during parse.
L1 deterministic consumers must read that field directly. They do not rebuild
text from provider-native `content`.

For analyzer-generated sidecars, canonical text resolution is:

1. use top-level `text` when it is a string
2. otherwise use `""`

`token_stats.json` exposes this per message as `text_source` with values
`text` or `empty`. `metrics.json` uses the same canonical text access
internally for character/diversity/heuristic inputs, but does not emit
per-message `text_source`.


---

# 4. Parse Stage (Canonical Base)

The `parse` stage performs provider normalization and may materialize
deterministic thread-local L1 artifacts alongside canonical output.

### Design Goals

- deterministic output
- minimal computational overhead
- single-pass processing
- streaming friendly
- no cross-thread dependency

### Allowed Operations

During `parse`, the system **may materialize deterministic L1 artifacts that are naturally available during the parsing loop**.

These operations must be **O(1) incremental updates** or **thread-local calculations**.

Allowed categories:

| Category | Example |
|------|------|
| normalization | provider → canonical schema |
| thread-local metrics | message counts, char counts |
| time extraction | first/last timestamps |
| deterministic L1 segmentation | message windows |
| lightweight aggregation | counters and min/max updates |

### Examples of Valid Parse-Time Materialization

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
When `message_windows.jsonl` is materialized during parse, it still remains an
L1 artifact built from canonical normalized fields rather than parse-owned meaning.
Semantic text belongs downstream: L3 consumers must rebuild excerpts or preview
text from canonical parsed messages when needed.

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
  - `summary`: message, turn, and token totals plus average and empty-text counters
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

The refusal, safety intervention, and revision phrase lists used by
deterministic machine computation are fixed analyzer rule sets. They do not
come from locale YAML resources and do not vary with runtime locale.

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
  - `safety`: refusal counters plus broader intervention counters/rates derived from the fixed machine refusal and caveat indicators
  - `interaction`: revision, correction, clarification, and retry counters/rates derived from the fixed machine cue set
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
  - refusal and revision phrase matching uses normalized text (`casefold` + collapsed whitespace) against the fixed locale-independent machine cue sets
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
- `messages_fts`, an FTS5 virtual table populated from non-empty canonical
  `messages.text` rows during a full `sqlite-build` rebuild; this is a local
  search index only, and joins back to `messages` by SQLite rowid internally
  while canonical identity remains provider/conversation/message fields
- `analyze recall`, a read-only L2 query path over the existing FTS index; it
  does not write query artifacts and reports canonical message identity fields
  only
- optional recall context windows selected around each FTS anchor within the
  same conversation; context selection does not affect anchor ranking
- optional recall bookends containing first/last same-conversation messages as
  compact evidence only

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
  canonical message text, including fenced code block detection

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
message_windows.jsonl
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

Experimental prototype note:

- `analyze semantic-prototype` is an experimental higher-layer bridge built on
  `message_windows.jsonl`
- it currently writes rebuildable `window_embeddings.jsonl` and
  `window_neighbors.jsonl` artifacts plus a minimal `window_clusters.jsonl`
  membership artifact next to each thread's window artifact
- it supports a default `deterministic-hash` backend plus an `ollama` backend
  for local embedding models served by Ollama
- Ollama-backed runs automatically chunk oversized window text with a
  deterministic UTF-8 byte budget and aggregate chunk embeddings back into one
  final embedding per source window
- neighbor construction now supports `--min-score` thresholding and emits
  lightweight progress logs for long-running phases
- the current default `--min-score` is `0.62`; that default was selected from
  repeated real-data subset validation because it reduced broad noisy
  cross-thread clusters more effectively than the old permissive default

Auxiliary L3 note:

- `analyze token-dictionary` writes rebuildable auxiliary artifacts under
  `<provider-root>/l3/token-dictionary/`
- it currently emits `observed_tokens.json`, `bundles.json`, `provenance.json`,
  and `lexical_rules.json`
- `observed_tokens.json` is an observed token index / corpus token statistics
  artifact. Legacy `dictionary.json` files remain readable as a
  backward-compatible alias. `bundles.json` records corpus-derived cooccurrence
  bundles
- these artifacts are derived from canonical `parsed.jsonl` plus optional
  `token_stats.json` and `topics.json`
- they are signal sources for later L3/L4 analysis only; they do not redefine
  canonical text, L1 sidecars, or SQLite state
- `lexical_rules.json` is generated from packaged token-dictionary seed-rule
  resources for compatibility/inspection. It is not reviewed active policy.
- cross-thread generic admission anchors and topic-summary scoring token policy
  are lexical policy owned by the built-in cross-thread lexical resources, not
  by token dictionary artifacts; Python fallback lists are minimal deterministic
  compatibility shims rather than the normal policy source
- explicitly provided reviewed project/user lexical rule files can be merged as
  additive policy layers above the built-in resources; no automatic discovery or
  promotion is implemented. Reviewed `persona_weak_tokens` are not OSS common
  defaults; when explicitly provided, they weaken topic-summary score inflation
  from standalone persona/name overlap without hard-suppressing candidate links
- `analyze lexical-rule-candidates` writes inactive diagnostic suggestions under
  `<provider-root>/l3/lexical-rules/`; Phase 1 suggests
  `generic_scoring_token` candidates from high-spread `observed_tokens.json`
  rows and `persona_weak_token` candidates for recurring
  persona/name/address-like overlap, plus `distinctive_allow_token` candidates
  for domain/project/topic tokens that may need protection from generic
  weakening, with fallback reading for legacy `dictionary.json`, and uses
  conservative token-shape filtering before review; optional topic-summary
  evidence adds compact counts and capped sample references when available, and
  `review.md` provides copyable manual-review snippets; the command never writes
  reviewed rule files. Candidate-generation heuristics are loaded from a
  built-in, versioned resource and summarized compactly in `diagnostics.json`;
  no project/user override for candidate generation exists yet
- cross-thread candidate `summary.json` records compact diagnostics for resolved
  built-in and reviewed lexical resources, including locale chain, safe resource
  paths, resource SHA1 hashes, owner scope, schema version, and category counts;
  it does not emit full token lists, and full resolved-policy export is deferred
- `lexical policy validate` and `lexical policy resolve` provide the same
  reviewed-policy validation and compact resolved-policy diagnostics without
  running candidate generation. They are read-only: reviewed project/user YAML
  remains the active editable policy surface, while
  `l3/token-dictionary/lexical_rules.json` is generated seed / legacy
  policy-like data rather than reviewed policy
- `lexical observed list` / `inspect` provide read-only views of
  `observed_tokens.json`, optional `bundles.json`, and optional provenance
  metadata. They display observed corpus facts only and do not classify, score,
  or activate tokens.
- `lexical candidates list` / `inspect` provide read-only views of inactive
  `l3/lexical-rules/candidates.jsonl` rows and compact diagnostics. They do not
  promote, reject, edit, or activate reviewed lexical policy.
- `analyze review-candidates` writes an inactive derived review queue under
  `<provider-root>/l3/review-queue/` from existing
  `l3/lexical-rules/candidates.jsonl` and
  `l3/cross-thread-candidates/candidates.jsonl` when present. It writes
  `candidates.jsonl`, `report.json`, and `report.md`; missing source artifacts
  are warnings. It does not regenerate source candidates, modify reviewed
  policy, promote rules, suppress topics, or change candidate scoring.
- `analyze policy-effectiveness` writes inactive diagnostics under
  `<provider-root>/l3/diagnostics/` from the same existing candidate artifacts
  when present. It writes `policy_effectiveness.json` and
  `policy_effectiveness.md`; missing source artifacts are warnings. It only
  summarizes emitted candidate types, reason codes, already-active lexical
  candidates, lexical token/persona/generic risk counts, and cross-thread
  low-score or continuity-mask counts. It does not modify reviewed policy,
  source artifacts, suppression policy, or scoring behavior.
- `analyze topic-lifecycle` writes inactive diagnostics under
  `<provider-root>/l3/diagnostics/` from existing cross-thread candidates,
  review queue rows, lexical candidates, and documented intra-thread topic
  summaries when present. It writes `topic_lifecycle.json` and
  `topic_lifecycle.md`; missing source artifacts are warnings. This first
  version reports conservative candidate-lifecycle proxy signals only, such as
  recurring/resurfaced evidence when inferable, stale/dormant or weak
  indicators, continuity-mask and low-score counts, and candidate counts by
  source/type. It does not infer authoritative topic lifecycle states, modify
  segmentation, scoring, suppression, review queue behavior, policy files, or
  L4 outputs.
- `user_lexical_profile` is reserved as a future provider-crossing user-level
  lexical memory contract. It is not implemented or wired into scoring; reviewed
  project/user lexical YAML remains the active human-reviewed policy surface
- cross-thread candidates also write `narrative.md`, a deterministic Markdown
  review/debug artifact derived from existing candidates, summary metadata, and
  topic summaries when available; it does not change scoring or semantic
  interpretation. It includes a compact candidate index table, and its
  diagnostics may include capped token-level hints derived from candidate
  evidence and topic-summary display fields, for example shared keywords and
  possible persona/address/generic overlap tokens. Topic-summary candidate rows
  may include diagnostic-only `evidence.overlap_diagnostics` buckets for generic,
  persona weak, residue, and specific overlap. Suspicious/high-ratio buckets are
  rendered only inside candidate-detail `#### Diagnostics`, not in the candidate
  index, and do not affect scoring, admission, thresholds, or reason codes.
  Low-confidence candidates are detailed only when the low-confidence set is
  small; larger low-confidence sets remain compact
- citation and tool residue tokens, for example `cite` and `turn0search*`, are
  non-semantic lexical markers and are suppressed from topic-summary admission
  evidence
- topic-summary candidate scoring uses a separate semantic profile from the
  default semantic-topics recurrence scorer; title overlap, summary keyphrases,
  specificity-aware keyword overlap, and local-LLM provenance are emphasized,
  while recurrence-style signals remain secondary support
- generic UI/system/domain/date terms, for example `link`, `viewing`, `ai`,
  `company`, `entity`, `年`, `月`, and `日`, are treated as low-information
  keyword/title overlap and cannot inflate topic-summary score bands by
  themselves
- current L3 cross-thread candidate generation can optionally use those token
  and bundle signals while building a selective-context `task_nucleus_text`
  from representative spans; this remains additive and rebuildable rather than
  canonical
  without the extra fragmentation seen at stricter nearby thresholds
- when `--sqlite-db` is provided, candidate retrieval uses L2 `analysis.db`
  filters before similarity scoring instead of a global dense comparison
- SQLite-assisted runs now compare windows symmetrically inside each narrowed
  candidate pool, improving mutual-link recovery without reintroducing
  corpus-wide all-pairs comparison
- backend/model selection is config- or CLI-owned; code keeps conservative
  fallback embedding settings (`max_input_bytes=256`,
  `chunk_overlap_bytes=32`, `aggregate=mean`) plus a small compatibility shim
  for a couple of historic Ollama model IDs
- those outputs are non-canonical and intentionally limited to embeddings,
  thresholded nearest-neighbor structure, and minimal connected-component
  grouping over retained mutual links
- cluster edge eligibility remains mutual-only, but same-thread mutual edges
  are dropped when the paired windows share more than one source message; that
  default was selected from the repository artifact corpus to reduce
  sliding-window chaining while preserving slightly more useful links than a
  stricter zero-overlap rule
- cross-thread mutual edges are also gated more strictly at clustering time:
  production derives the current run's P75 cross-thread mutual score from the
  retained neighbor rows and only keeps cross-thread edges at or above that
  threshold; this keeps mutual-only semantics intact while reducing broad
  cross-thread components without depending on `./tmp` experiment outputs
- when older or partial neighbor rows do not carry usable cross-thread scores,
  clustering falls back to the legacy mutual-only behavior for those edges
  instead of failing
- `window_clusters.jsonl` rows use
  `src/llm_logparser/core/schemas/window_clusters.schema.json` and currently
  emit one deterministic membership row per source window with
  `cluster_id`, `cluster_size`, and `edge_policy`
- `window_clusters.jsonl` is not a topic summary artifact: it does not add
  labels, summaries, lifecycle state, or canonical meaning
- `analyze semantic-preview` is a read-only renderer for those same prototype
  artifacts; it reads `message_windows.jsonl`, `window_clusters.jsonl`, and
  optional `window_neighbors.jsonl` and can print:
  a default cluster list, one cluster in detail, one conversation's cluster
  participation, or the older single-window neighbor preview, all without
  writing new files
- `analyze semantic-prototype` no longer requires stored `message_windows.jsonl`
  as its only entry point: it can also derive the same default deterministic
  candidate windows directly from canonical `parsed.jsonl`, treating
  `message_windows.jsonl` as a convenience substrate rather than the sole
  gateway into L3
- `analyze semantic-topic` is a read-only L4 consumer of the same L3
  artifacts; it reads `message_windows.jsonl` and `window_clusters.jsonl`,
  selects representative windows from each cluster, and asks a local Ollama
  model for a label, short summary, and keywords
- representative-window selection is deterministic and lightweight: it prefers
  windows with more retained intra-cluster neighbor links first, then higher
  average retained intra-cluster scores, then larger message/character
  footprints when centrality signals tie or are unavailable
- `analyze semantic-topics` is the formal topic artifact builder for that same
  boundary; it writes provider-scoped artifacts under
  `<provider-root>/l3/semantic-topics/`
- `topics.json` is the forward topic index: one current topic record per L3
  cluster, with deterministic `topic_id`, span/message-grounded references
  back to semantic membership, conversation coverage, time bounds, and
  optional model-derived label / summary / keywords
- each topic record may also carry additive `quality_signals` for observation
  only, such as cluster size, conversation count, and retained intra-cluster
  score summaries when neighbor data is available
- `topics.json` now uses `schema_version: "2.0"` and carries top-level
  `generated_at`, `source_inputs`, and `provenance`; provenance records the
  topic builder mode plus the upstream clustering policy used to produce the
  source L3 clusters
- topic records are now grounded primarily by `span_refs`, `message_refs`, and
  `representative_spans`; `window_refs` and `representative_windows` remain
  only as compatibility/presentation overlays and are no longer the semantic
  contract core
- prompt provenance is execution-based rather than capability-based:
  structural-only runs keep `labeling_model`, `prompt_variant`, and
  `prompt_hash` as `null`; model-enriched runs populate them from the actual
  labeling invocation
- structural-only runs now also fill topic `label` with a deterministic
  heuristic phrase derived from representative topic text; `summary` remains
  empty and labels stay additive convenience metadata rather than canonical
  meaning
- `topics.json` now emits `schema_version: "2.1"`
- each topic record now includes canonical heuristic state fields:
  `state` with values `unresolved|in_progress|done` plus
  topic-level `state_confidence`
- each `span_ref` and `representative_span` now also carries
  `state`, `state_confidence`, and diagnostic `state_signals`
- the current state engine is an L3 deterministic heuristic path only:
  it operates on reconstructed canonical span messages, uses tail-priority
  last-message-wins conflict handling, and treats recency as a modifier rather
  than a primary classifier
- span-state phrase sets now live in locale-scoped L3 resource files rather
  than hardcoded Python tuples; `semantic-topic` / `semantic-topics` accept an
  explicit `state_locale`, and unsupported locales fall back to `en-US`
- `topic_membership.jsonl` is the reverse lookup index: it now uses
  `schema_version: "1.0"` and emits explicit `membership_type=cluster|span|message`
  rows so cluster provenance, span membership, and message membership are all
  direct lookups rather than implied joins
- `analyze semantic-topic-explore` is the read-only consumer of that reverse
  index; it loads `topics.json`, `topic_membership.jsonl`, and
  `message_windows.jsonl`, then builds:
  `topic_id -> members`, `message_id -> topic_id`, and
  `conversation_id -> topic_id` indexes in memory for navigation
- the explorer's default list ordering is deterministic and scan-oriented:
  larger topics first, then broader conversation coverage, then higher
  observed intra-cluster scores when present, with representative previews and
  lightweight quality hints surfaced in the text view
- browse-time filters such as singleton suppression or minimum topic size are
  runtime-only UX controls; they improve navigation but do not rewrite
  `topics.json` or `topic_membership.jsonl`
- because `message_windows.jsonl` carries `message_ids` and deterministic
  provenance, the explorer can join reverse membership rows back to canonical
  messages, reconstruct excerpts, and preserve temporal order without touching
  clustering logic or making any LLM calls
- model-derived fields remain additive only; if `semantic-topics` runs without
  `--model`, it still writes the structural topic index and reverse membership
  rows, now with heuristic labels but without model summaries
- Step 5 semantic artifact migration is explicit and breaking: older
  window-centric `topics.json` / `topic_membership.jsonl` contracts are not
  equivalent to the current span/message-first contract. Regenerate semantic
  artifacts from canonical inputs under the current pipeline rather than
  attempting to reuse older semantic artifacts in place
- the current production topic prompt was selected from the repository's
  prompt experiment harness under `./tmp`; runtime does not depend on those
  tmp files and instead uses the fixed winning settings directly

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
