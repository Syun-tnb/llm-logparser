# Analysis Artifacts and Processing Stages

This document defines the architectural boundaries between **parse**, **analyze (L1/L2)**, and **L3 processing** in `llm-logparser`.

The goal is to ensure that:

- `parse` remains **lightweight and deterministic**
- reusable artifacts are produced early enough for **local LLM pipelines**
- heavy cross-thread computation is deferred to **analyze**

This document describes **what data is produced at each stage** and **why**.


---

# 1. Processing Model

`llm-logparser` is structured into three logical processing levels.

```

raw provider export
│
▼
parse (L1)
│
▼
analyze (L2)
│
▼
downstream / L3
(local LLM, search, indexing, etc.)

```

Each stage has a clearly defined responsibility.

| Stage | Purpose |
|------|------|
| parse (L1) | canonical normalization and deterministic artifact generation |
| analyze (L2) | cross-thread analysis and presentation |
| L3 | external consumers (local LLM pipelines, embeddings, search) |

A core design principle is:

> **Later stages must never require re-running earlier stages if artifacts already exist.**


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

## 3. Current Artifact Layout

The current parser produces the following directory structure.

```text
<outdir>/
  <provider>/
    manifest.json
    thread-<conversation_id>/
        parsed.jsonl
```

Each thread is stored in an isolated directory.

Thread artifact:

```text
thread-<conversation_id>/
    parsed.jsonl
```

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


---

# 5. Global Accumulator (Parse-Time)

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

# 6. Analyze Stage (L2)

The analyze stage performs **cross-thread computation** and **presentation-level processing**.

Analyze operates on existing artifacts and **does not modify canonical parse results**.

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

# 7. L3 / Downstream Processing

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

# 8. Artifact Design Principles

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

# 9. Architectural Summary

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

# 10. Rationale

This architecture ensures:

- fast parsing even for large datasets
- deterministic canonical artifacts
- flexible downstream analysis
- compatibility with local LLM pipelines
- minimal coupling between stages


---

# 11. Future Extensions

Possible future additions include:

- chunk manifests
- conversation window artifacts
- SQLite optional indexes
- timeline precomputation

Any new artifact must follow the principles described in this document.
