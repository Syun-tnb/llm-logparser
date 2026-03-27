# Semantic Topic Tracking (L3)

This document defines **Semantic Topic Tracking (L3)** as an optional analysis
layer in `llm-logparser`.

L3 exists to answer a question that thread reconstruction alone cannot answer:

> What work is actually continuing over time, even when it moves across
> threads, providers, sessions, or fragmented conversation histories?

The parse layer, Layer 1 (L1), and Layer 2 (L2) remain the stable base of the
system:

- `parse` produces canonical `parsed.jsonl`
- L1 produces deterministic, rebuildable analysis artifacts
- L2 provides an optional deterministic SQLite query layer

L3 builds on top of those layers without modifying them. It is an additive
semantic interpretation layer whose goal is to reconstruct **topic continuity**
rather than only **thread continuity**.

---

# 1. Purpose and Scope

Semantic Topic Tracking groups semantically related messages into persistent
topics, even when those messages are distributed across multiple threads or
appear weeks apart.

It is intended for workflows where users:

- revisit the same task over time
- split related work across multiple threads
- alternate between unrelated topics in the same conversation
- lose track of earlier decisions, blockers, and next steps

L3 is designed to provide a higher-level working view of ongoing discussions,
including what has already been decided, what remains unresolved, and where a
topic most recently resumed.

This layer is:

- optional
- additive
- best-effort
- potentially non-deterministic
- rebuildable from canonical and lower-layer artifacts

This layer is not canonical storage and must never redefine the meaning of
`parsed.jsonl`, `metrics.json`, `thread_stats.json`, `chunks.jsonl`,
`message_windows.jsonl`, or `analysis.db`.

---

# 2. Architectural Position

Semantic Topic Tracking is a higher derived layer built on top of the canonical
dataset.

```text
raw provider export
    -> parse
    -> parsed.jsonl
    -> L1 deterministic artifacts
    -> L2 optional SQLite index
    -> L3 semantic topic tracking
```

Typical L3 inputs may include:

- `parsed.jsonl`
- `thread_stats.json`
- `chunks.jsonl`
- `message_windows.jsonl`
- optional L2 query results from `analysis.db`

Architectural rules:

- L3 does not modify canonical data
- L3 does not replace thread-local deterministic artifacts
- L3 produces separate derived artifacts only
- L3 may consume L1 and L2 outputs when they improve scale or efficiency
- L3 must remain safe to delete and rebuild

Because topics are intentionally cross-thread, L3 outputs are usually more
useful as dataset-scoped or provider-scoped artifacts than as thread-local
artifacts.

Recommended layout:

```text
<outdir>/
  <provider>/
    manifest.json
    analysis.db
    l3/
      semantic-topics/
        topics.json
        topic_summaries.md
        topic_timeline.jsonl
```

---

# 3. Why Topic Tracking Is Needed

Thread reconstruction answers a structural question:

> Which messages belong to the same exported conversation container?

Semantic Topic Tracking answers a different operational question:

> Which messages are about the same underlying work, regardless of where they
> appear?

In real usage, those two boundaries diverge.

Users often:

- open a new thread for the same task
- resume a topic after a pause
- branch into subproblems and return later
- mix several topics inside one thread
- repeat prior decisions because the earlier context is hard to find

This makes thread-level analysis necessary but insufficient. A thread may
contain multiple topics, while a single topic may span multiple threads.

---

# 4. What Is a Topic

A **topic** is a cluster of semantically related messages that together describe
an ongoing unit of work, inquiry, or decision-making.

A topic is defined by meaning, not by provider thread boundaries.

Properties of a topic:

- it may contain messages from one thread or many threads
- it may be continuous or intermittent over time
- it may contain planning, implementation, review, and follow-up messages
- it may evolve in wording while remaining the same underlying subject

Examples of topics:

- migrating a parser adapter to a new schema
- investigating a recurring production error
- drafting a documentation section across multiple editing sessions
- evaluating whether a feature is complete or still blocked

Topic boundaries should be interpreted as best-effort semantic groupings, not
as exact canonical truth.

## Topic vs. Thread

| Concept | Meaning |
| --- | --- |
| thread | A provider- or export-defined conversation container |
| topic | A semantic unit of related discussion, independent of thread boundaries |

A single thread may contain multiple topics. A single topic may span multiple
threads.

---

# 5. Topic Lifecycle

Each topic should expose a lifecycle so users can understand not only what the
topic is, but also whether it is still active.

Minimum lifecycle fields:

- `first_seen`
- `last_seen`
- `state`

Lifecycle definitions:

- `first_seen`: timestamp of the earliest message associated with the topic
- `last_seen`: timestamp of the most recent message associated with the topic
- `state`: best-effort status derived from recency and semantic signals

Recommended topic states:

- `active`: recently discussed or clearly still in progress
- `dormant`: not recently discussed, but still plausibly unresolved
- `resolved`: appears completed, decided, or explicitly closed

State assignment is interpretive rather than canonical. A topic can move from
`resolved` back to `active` if later messages reopen it.

---

# 6. Topic Metadata

Each topic artifact should capture both structural references and high-level
semantic summaries.

Recommended fields:

| Field | Description |
| --- | --- |
| `topic_id` | Synthetic topic identifier; deterministic when feasible under the same inputs and configuration |
| `summary` | Short human-readable description of the topic |
| `state` | `active`, `dormant`, or `resolved` |
| `first_seen` | Earliest associated timestamp |
| `last_seen` | Latest associated timestamp |
| `related_messages` | Canonical message identifiers or stable analyzer-generated row keys |
| `related_threads` | Set of thread or conversation identifiers touched by the topic |
| `decisions_made` | Short extracted list of conclusions or accepted choices |
| `open_questions` | Outstanding questions, uncertainties, or blockers |
| `next_actions` | Follow-up work implied or stated in the messages |
| `last_updated` | Timestamp when the topic artifact was last regenerated |

Additional implementation metadata is also recommended:

| Field | Description |
| --- | --- |
| `producer_layer` | Should identify this as an L3 artifact |
| `embedding_model` | Embedding model used for similarity computation |
| `clustering_method` | Graph, DBSCAN, HDBSCAN, or another supported method |
| `source_inputs` | Canonical or lower-layer inputs used to build the topic |
| `schema_version` | Version of the topic artifact schema |
| `reproducibility_note` | Brief note describing best-effort, non-deterministic behavior |

## Topic Identifier Guidance

`topic_id` should be synthetic and stable when possible, but stability is
inherently limited by the clustering process.

A practical approach is:

1. compute topic membership from semantic similarity
2. choose a stable anchor set such as earliest message identifiers or a medoid
3. hash the anchor set together with the clustering configuration version

This yields a best-effort stable identifier under unchanged inputs, but L3
should still document that topic assignment may change when embeddings, models,
thresholds, or clustering parameters change.

## Example Topic Record

```json
{
  "topic_id": "topic_7f2c9e4b",
  "summary": "Semantic Topic Tracking documentation and L3 architecture design",
  "state": "active",
  "first_seen": "2026-03-14T02:18:11Z",
  "last_seen": "2026-03-24T08:42:05Z",
  "related_messages": [
    "msg_00192",
    "msg_00201",
    "msg_00444"
  ],
  "related_threads": [
    "thread-a1b2",
    "thread-c9d0"
  ],
  "decisions_made": [
    "L3 will remain additive and non-canonical",
    "Primary outputs will be dataset-scoped"
  ],
  "open_questions": [
    "How should topic state aging thresholds be configured?"
  ],
  "next_actions": [
    "Finalize the public documentation page"
  ],
  "last_updated": "2026-03-24T09:10:00Z"
}
```

---

# 7. Why Thread-Based Models Fail

Thread-based reconstruction remains useful, but it does not capture real
working continuity.

## 7.1 Provider Logs Are Structurally Limited

Provider exports vary in quality and structure.

Some providers expose strong conversation identifiers. Others expose weaker or
less complete continuity signals. In Gemini-style exports in particular,
thread boundaries may not fully represent how the user experienced or resumed
the work. Logs may be fragmented, branch semantics may be unclear, and
cross-session continuation may not be explicit.

L3 should therefore treat thread structure as an input signal, not as a full
model of user intent.

## 7.2 Threads Do Not Match Real Cognitive Workflows

Users do not think in perfectly isolated thread containers.

They often:

- resume a previous task in a new thread
- ask a related follow-up in another provider
- switch between implementation, planning, and debugging within one session
- return to a topic after a long interruption

The exported thread boundary is therefore a storage boundary, not a reliable
cognitive boundary.

## 7.3 Conversations Drift, Merge, and Resume

Topic continuity is dynamic:

- one thread can drift across several subjects
- two previously separate threads can converge on the same topic
- a resolved topic can later be reopened
- the same question can be reformulated multiple times over weeks

Thread-only analysis cannot robustly answer:

- what has already been decided
- whether a topic is still active
- which unresolved questions keep recurring
- where the latest continuation of a topic occurred

---

# 8. High-Level Implementation Direction

This section describes the intended direction for L3. It does not define a
locked implementation.

## 8.1 Core Approach

Semantic Topic Tracking should combine:

- semantic similarity
- clustering
- time-aware continuity tracking
- summary extraction for user-facing topic metadata

The implementation should operate over canonical messages or derived windows,
not over provider-native raw exports.

## 8.2 Embedding-Based Similarity

The primary similarity signal should come from embeddings generated from:

- individual messages
- fixed-size message windows
- deterministic chunks from L1 artifacts

Suitable local-first embedding options may include sentence-transformer-style
models or equivalent local embedding backends.

Embeddings enable the system to group semantically similar content even when:

- wording changes
- the user resumes the topic later
- the conversation appears in another thread

## 8.3 Clustering Strategies

Clustering should remain implementation-pluggable.

Reasonable approaches include:

- graph-based clustering over similarity edges
- `DBSCAN`
- `HDBSCAN`

Guidance:

- graph-based clustering is useful when the system wants explicit control over
  neighborhood construction and bridge edges
- `DBSCAN` is useful when a fixed density threshold is acceptable
- `HDBSCAN` is useful when topic density varies across the dataset

In practice, a production implementation may combine:

- a candidate generation pass
- similarity thresholding
- clustering
- post-processing to merge or split unstable clusters

## 8.4 Topic Continuity Over Time

Clustering alone is not sufficient. L3 should also model temporal continuity.

Recommended behavior:

- preserve `first_seen` and `last_seen`
- update topic state from recency and semantic closure signals
- allow later messages to reactivate a dormant or resolved topic
- keep an auditable link from the topic back to the contributing messages

This is the key difference between generic semantic clustering and topic
tracking: L3 is not only discovering clusters, but also maintaining continuity
states over time.

## 8.5 Summary Extraction

Fields such as `summary`, `decisions_made`, `open_questions`, and
`next_actions` may be generated through:

- deterministic extraction where practical
- local model summarization
- API-based summarization in a higher layer

Regardless of method, these outputs remain derived interpretations and must be
stored as L3 artifacts rather than mixed into canonical data.

## 8.6 Storage and Query Options

L3 should support offline-first operation without requiring external services.

Optional implementation choices may include:

- local embedding files
- SQLite vector extensions
- a vector database for larger corpora

These are execution details, not architectural requirements. The design goal is
to allow local-only usage while still supporting larger-scale semantic search
when users need it.

---

# 9. Outputs

Semantic Topic Tracking should produce separate derived artifacts.

Recommended outputs:

## 9.1 `topics.json`

Primary structured artifact containing the current topic set and associated
metadata.

Typical contents:

- topic records
- lifecycle fields
- related message and thread references
- derived summaries
- provenance metadata

## 9.2 `topic_summaries.md`

Human-readable overview of the tracked topics.

Typical contents:

- one section per topic
- short summary
- state
- latest activity
- decisions made
- open questions
- next actions

This artifact is intended for inspection, review, and repository-friendly
documentation workflows.

## 9.3 `topic_timeline.jsonl`

Chronological event stream for topic evolution.

Typical contents:

- topic created or first detected
- topic resumed
- topic state changed
- topic merged or split
- topic resolved

This artifact is intended for downstream analysis, visualization, or GUI use.

---

# 10. CLI Concept

L3 commands should remain clearly separated from deterministic L1/L2 commands.

Conceptual commands:

```text
llm-logparser analyze semantic-topics <input>
llm-logparser analyze topic-summary <topics.json>
llm-logparser analyze topic-timeline <topics.json>
```

Example usage:

```bash
llm-logparser analyze semantic-topics ./out/openai
```

```bash
llm-logparser analyze topic-summary ./out/openai/l3/semantic-topics/topics.json
```

```bash
llm-logparser analyze topic-timeline ./out/openai/l3/semantic-topics/topics.json
```

Command design expectations:

- `semantic-topics` builds or refreshes the topic artifact set
- `topic-summary` renders a readable summary view from existing L3 artifacts
- `topic-timeline` renders or exports a topic-level temporal view

The exact flag set is intentionally left open. The important boundary is that
L3 commands remain opt-in and do not alter deterministic artifacts.

---

# 11. Design Principles

## 11.1 Canonical-First Architecture

`parsed.jsonl` remains the source of truth. L3 only interprets canonical and
derived data; it does not redefine them.

## 11.2 Separation of Truth vs. Interpretation

Canonical and deterministic artifacts describe what was recorded.

L3 describes a semantic interpretation of what those records appear to mean.
Those are different responsibilities and must remain separate in both storage
and CLI design.

## 11.3 Reproducibility Awareness

L3 is not fully deterministic.

Embedding models, thresholds, clustering parameters, and summarization methods
can all affect the result. L3 artifacts should therefore record enough
provenance to explain how they were produced and should be treated as
best-effort interpretations rather than immutable truth.

## 11.4 Offline-First Compatibility

L3 should be able to run in a local-first environment using local embedding or
local model tooling where available. Network-backed services may be supported,
but they must remain optional.

## 11.5 Additive Outputs Only

L3 must produce separate artifacts and remain safe to delete or rebuild. It
must not mutate canonical records or overwrite deterministic outputs.

---

# 12. Non-Goals

Semantic Topic Tracking does not aim to:

- replace thread reconstruction
- redefine canonical message meaning
- guarantee perfectly stable topic IDs across all model or parameter changes
- infer hidden user intent with certainty
- require an external API or hosted vector system

The purpose of L3 is to improve continuity tracking, not to claim canonical
truth about why every message exists.

---

# 13. Summary

Semantic Topic Tracking (L3) introduces a higher-level semantic view of
conversation history. It groups related messages across threads, tracks topic
lifecycle over time, and surfaces decisions, unresolved questions, and next
actions that thread-based analysis alone cannot reliably recover.

It is intentionally optional, additive, non-canonical, and best-effort. Its
role is to make fragmented conversation data operationally useful without
compromising the canonical-first architecture of `llm-logparser`.
