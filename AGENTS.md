# AGENTS.md

## 1. Purpose

This repository implements a local-first log analysis engine for exported LLM conversations.

Its responsibilities include:

- parsing provider exports into canonical normalized data
- generating deterministic analysis artifacts
- supporting optional higher-layer semantic analysis

The system is designed to be:

- local-first
- deterministic at its core
- rebuildable from canonical data
- dependency-minimal and predictable

The canonical source of truth is:

- `parsed.jsonl`

All other outputs are derived artifacts.


## 2. Architectural Model

The system follows a layered architecture:

- Canonical base: `parsed.jsonl`
- L1: deterministic analysis artifacts
- L2: optional deterministic index (SQLite)
- L3: optional semantic analysis
- L4: optional model-assisted or API-assisted analysis

Rules:

1. Canonical data must not be redefined by higher layers.
2. L1/L2 must remain deterministic and rebuildable.
3. L3/L4 must remain optional and additive.
4. Derived artifacts must not silently become canonical.


## 3. Canonical Data Rules

Contributors must not:

- modify the meaning of canonical message text during parsing
- inject semantic interpretation into canonical records
- create alternative canonical representations outside defined contracts

Canonical data must remain:

- stable
- provider-normalized
- free of interpretation


## 4. Deterministic Layer Rules (L1/L2)

L1 and L2 must:

- produce reproducible outputs from the same inputs
- avoid dependence on LLMs or external APIs
- remain valid in offline environments

Changes must not:

- introduce hidden non-determinism
- require network access (except explicitly documented exceptions)
- alter existing artifact contracts without explicit migration


## 5. Higher-Layer Rules (L3/L4)

Higher layers may include:

- semantic grouping
- topic tracking
- summarization
- model-assisted analysis

They must:

- remain optional
- remain clearly separated from canonical data
- include provenance where applicable
- be safe to delete and rebuild

They must not:

- overwrite or redefine canonical data
- become required for core functionality


## 6. Dependency Principles

Dependencies must be introduced cautiously.

Preferred:

- standard library
- small, well-maintained libraries
- permissive licenses (MIT, Apache)

Avoid:

- large frameworks without clear architectural benefit
- dependencies that introduce implicit network behavior
- redundant abstractions over existing stable code

When adding a dependency, contributors should consider:

- whether it reduces complexity or just shifts it
- whether it affects determinism
- whether it belongs in core or optional features


## 7. Refactoring Principles

Refactors should prioritize:

1. removing duplication
2. clarifying contracts
3. improving determinism and reproducibility
4. isolating optional functionality

Avoid:

- large-scale rewrites without clear benefit
- introducing new abstractions that obscure data flow
- mixing deterministic and heuristic logic


## 8. Error Handling and Repair

The system should favor:

- clear validation errors
- explicit diagnostics
- reproducible failure modes

It should not:

- modify parsing logic dynamically at runtime
- silently recover in ways that hide data inconsistencies

Repair mechanisms should be:

- explicit
- reviewable
- reproducible


## 9. Semantic Processing Constraints

Natural language inputs may include dialects, colloquialisms, and stylistic variation across languages.

To improve semantic consistency, the system may apply normalization strategies such as:

- dialect-insensitive representations
- standard-language paraphrase overlays
- embedding-oriented normalization

These techniques are intended to reduce surface-level variation while preserving meaning.

However, the following constraints must always be enforced:

- canonical message text must remain unchanged
- normalization must be non-destructive
- normalization must not replace or overwrite canonical data
- normalization must not introduce a secondary canonical representation

Normalization is strictly a downstream concern and must:

- operate only in higher layers (L3/L4)
- be optional and rebuildable
- avoid persistent normalized overlays unless explicitly justified

The system must treat:

- canonical text as the source of truth
- normalized representations as temporary or derived views

Meaning preservation takes priority over normalization convenience.


## 10. Documentation Expectations

Changes should update documentation when they affect:

- artifact structure
- CLI behavior
- configuration behavior
- dependency expectations
- layer boundaries

Documentation should clearly state:

- whether behavior is deterministic or heuristic
- whether artifacts are canonical or derived
- whether changes are breaking or additive


## 11. Non-Goals

This project does not aim to:

- depend on cloud services by default
- introduce hidden telemetry
- replace canonical data with derived indexes
- prioritize trend-driven tooling over stability

The goal is long-term reliability, clarity, and local-first operation.