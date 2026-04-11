# llm-logparser Roadmap

## Overview

`llm-logparser` is a layered system for transforming raw conversation logs into structured, analyzable artifacts.

The architecture is designed around:

* a canonical, deterministic base (`parsed.jsonl`)
* reproducible derived artifacts (L1 / L2)
* optional higher semantic layers (L3 / future L4)

The core principle is:

> higher-level features must never compromise canonical correctness or determinism

---

## Current State (2026-04)

### L1 — Deterministic analysis layer

Status: **stable**

* canonical parsing produces:

  * `parsed.jsonl`
  * `thread_stats.json`
  * `message_windows.jsonl`
* deterministic analyzers implemented:

  * stats
  * timeline
  * token analysis
  * metrics

Properties:

* fully rebuildable from canonical
* no external dependencies
* deterministic outputs

---

### L2 — Optional indexing layer

Status: **stable**

* SQLite-based analysis index
* derived strictly from canonical / L1 artifacts
* used as a query accelerator, not a source of truth

Properties:

* optional
* replaceable
* does not affect upstream correctness

---

### L3 — Semantic topic layer

Status: **implemented and under active validation**

Capabilities:

* semantic topic construction based on spans
* cross-window / cross-thread topic continuity
* span-based topic identity (not window-based)
* semantic normalization (batch + provenance tracking)

Artifacts:

* `topics.json`
* normalization job artifacts (config / spans / results)

Key properties:

* canonical reconstruction (no direct provider dependency)
* provenance-aware (prompt / taxonomy consistency)
* batch-capable (L3 becomes a reusable asset, not just a computation)

---

### Cross-thread candidates (L3 extension)

Status: **experimental but functional**

Capabilities:

* detection of cross-thread continuation candidates
* rule-based candidate generation (precision-first)
* symmetric candidate emission
* explainable scoring

Properties:

* no topic merging
* no modification of existing topics
* sidecar-only output

---

### Embedding enrichment

Status: **validated (safety confirmed, value under evaluation)**

Current design:

* embedding is optional and opt-in
* applied only after candidate generation
* used for:

  * tie-break ranking
  * enrichment (`embedding_similarity`)

Constraints:

* must not change candidate count
* must not modify rule scores
* must not introduce non-deterministic candidate selection

Validation status:

* fallback behavior confirmed (safe)
* enrichment execution confirmed
* tie-break scoring produces reasonable signals
* no confirmed ranking improvement yet (limited validation cases)

---

## Current Focus

The project is currently in a **validation and refinement phase**.

### 1. Expand validation coverage

* increase number of tied rule-score candidate groups
* validate embedding behavior across multiple cases
* confirm:

  * stability (no candidate drift)
  * consistency (no field contamination)
  * usefulness (ranking improvement where applicable)

---

### 2. Evaluate ranking quality

Focus:

* identify cases where:

  * rule ranking is suboptimal
  * embedding provides better ordering
* distinguish:

  * confirmation (correct ranking remains unchanged)
  * correction (ranking improves)

---

### 3. Refine scoring only if justified

Scoring changes are **not** the default next step.

Only proceed if:

* multiple real cases show consistent weaknesses
* improvement direction is clear and explainable

---

### 4. Maintain strict layer separation

Must preserve:

* rule-based candidate generation (primary)
* embedding as secondary signal only
* canonical → L1 → L2 → L3 boundaries

---

## Near-term Direction

Once validation reaches sufficient coverage:

* formalize evaluation criteria for ranking quality
* optionally introduce lightweight diagnostics:

  * tie-group summaries
  * ranking difference reports

No major architectural changes are planned in this phase.

---

## Future Exploration

### Scalable cross-thread similarity (ANN / vector indexing)

Not currently implemented.

Motivation:

* avoid O(N²) similarity exploration as corpus grows
* support efficient nearest-neighbor search in large datasets

Possible approaches:

* HNSW-based indexing
* Faiss / Annoy
* locality-sensitive hashing (LSH)

Constraints:

* must not replace rule-based candidate generation
* must not compromise explainability
* must remain optional and bounded in scope

When to consider:

* corpus size makes current approach too slow
* embedding-based similarity becomes a primary exploration tool
* clear UX need for broader similarity discovery

---

### L4 — Higher-level semantic analysis

Not currently implemented.

Potential directions:

* topic summarization
* decision extraction
* next-action detection
* cross-topic reasoning

Constraints:

* must consume L3 outputs, not replace them
* must remain optional (likely API-backed)

---

### Visualization / UI

Deferred.

Potential:

* topic graph visualization
* cross-thread continuity display
* span-level inspection tools

---

## Guiding Principles

* canonical data is the single source of truth
* deterministic layers must remain reproducible
* higher layers must be additive, not mutative
* performance optimizations must not compromise correctness
* experimental features must be isolated and opt-in

---

## Summary

`llm-logparser` has transitioned from a parsing tool into a structured analysis system.

* foundational layers (L1 / L2) are stable
* semantic layer (L3) is implemented
* current work focuses on validation, not expansion

The next milestone is:

> demonstrating that semantic enrichment produces meaningful, human-relevant improvements — without breaking determinism
