# AGENTS.md

## Purpose

This repository builds deterministic, local-first tooling for parsing,
normalizing, exporting, and analyzing LLM conversation logs.

Contributors should prioritize:

- correctness
- determinism
- rebuildability
- explainability
- local-first operation
- stable contracts
- low operational surprise

The repository is not optimized for “clever” AI-agent behavior.
It is optimized for long-term maintainability and interpretable outputs.

---

# Core Principles

## 1. Canonical artifacts are the source of truth

`parsed.jsonl` is the canonical intermediate representation.

All downstream layers must treat canonical artifacts as authoritative.

Derived outputs:

- Markdown
- metrics
- token stats
- narrative layers
- semantic candidate layers
- SQLite indexes

must remain rebuildable from canonical artifacts.

Do not introduce hidden state.

---

## 2. Determinism over convenience

Prefer deterministic behavior whenever practical.

Avoid:

- hidden randomness
- unstable ordering
- timestamp-dependent outputs
- implicit mutation
- silent fallback behavior

If nondeterminism is unavoidable, document it explicitly.

---

## 3. Preserve structure before interpretation

Parser layers extract and normalize structure.

They do not:

- summarize
- flatten meaning
- rewrite text
- infer semantics

Semantic interpretation belongs in downstream layers.

---

## 4. Keep responsibilities separated

Maintain clear layer boundaries.

### Provider adapters

Responsible for:

- provider-specific structure expansion
- normalization
- schema mapping

Not responsible for:

- formatting
- semantic interpretation
- narrative generation

### Parser layer

Responsible for:

- canonical JSONL generation
- validation
- thread segmentation
- deterministic structure handling

### Export layer

Responsible for:

- rendering
- flattening
- Markdown formatting
- human-readable presentation

### Semantic layers

Responsible for:

- topic summaries
- recurrence candidates
- semantic reconstruction
- narrative diagnostics

---

# Contributor Working Style

## 5. Prefer outcome-oriented implementation

Focus on:

- intended behavior
- observable contracts
- validation criteria
- downstream impact

Do not over-prescribe internal implementation steps unless necessary.

---

## 6. Minimize unnecessary complexity

Prefer:

- explicit logic
- readable heuristics
- inspectable data flow
- simple contracts

Avoid introducing abstraction layers without clear long-term value.

---

## 7. Do not prematurely generalize

Design for extension,
but avoid speculative architecture that is not yet required.

Especially avoid:

- framework-heavy rewrites
- premature distributed systems patterns
- unnecessary async complexity
- over-engineered plugin systems

---

## 8. Diagnostics are first-class outputs

Human-readable diagnostics are important.

Semantic and heuristic systems must remain inspectable.

Contributors should prefer:

- explicit diagnostics
- evidence visibility
- traceable scoring
- explainable suppression behavior

over opaque scoring systems.

---

# Semantic Layer Guidance

## 9. L3 is a candidate preparation layer

L3 does not establish final semantic truth.

Its role is:

- segmentation
- topic normalization
- candidate preparation
- semantic evidence surfacing

Avoid pushing excessive semantic responsibility into segmentation logic.

---

## 10. Lexical policies must remain externalizable

Avoid hardcoding project-specific lexical rules into Python constants.

Prefer resource-backed policies whenever possible.

Especially avoid embedding:

- user-specific names
- project-private motifs
- conversational rituals
- persona-specific vocabulary

into generic shared defaults.

---

## 11. Suppression systems must remain balanced

Suppressing noise is important.

Over-suppression is also dangerous.

World motifs, recurring narrative elements,
and persistent semantic entities may carry legitimate continuity.

Do not assume all recurring persona tokens are meaningless.

---

# Validation & Review

## 12. Validation matters more than intuition

Behavior changes should be validated against:

- real artifacts
- regression fixtures
- diagnostics
- narrative outputs
- downstream semantic quality

Do not rely purely on theoretical reasoning.

---

## 13. Preserve interpretability

Contributors should prefer systems that humans can inspect and debug.

If a scoring system becomes difficult to explain,
it is likely too complex.

---

## 14. Keep outputs reviewable

Generated artifacts should remain:

- readable
- diff-friendly
- stable across runs
- easy to inspect in git

Avoid unnecessary churn in generated outputs.

---

# Safety & Privacy

## 15. Local-first is non-negotiable

Do not introduce hidden telemetry or silent external transmission.

Network access must remain explicit and reviewable.

---

## 16. Treat user conversation data carefully

Conversation logs may contain:

- personal information
- emotional discussions
- credentials
- proprietary material

Contributors must avoid accidental leakage through:

- diagnostics
- logs
- fixtures
- example outputs
- test artifacts

---

# Documentation

## 17. Documentation is part of the system

Major behavior changes should update relevant docs.

Especially:

- output contracts
- schema expectations
- semantic behavior
- validation workflows
- config behavior
- diagnostics meaning

---

# Final Guideline

Prefer systems that are:

- understandable
- rebuildable
- inspectable
- stable
- explainable

over systems that are merely impressive.