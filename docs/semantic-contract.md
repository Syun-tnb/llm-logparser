# Semantic Contract (Step 5)

## 1. Purpose

This document summarizes the current semantic contract in `llm-logparser`
after the Step 5 refactor.

It exists as a maintainer reference for what must stay true across the current
L3 semantic pipeline:

- what the semantic core is responsible for
- what candidate providers are allowed to do
- what remains presentation-only
- what semantic artifacts now treat as primary identity and membership
- how the breaking contract transition is intended to work

This document describes the repository's current state. It is not a future
roadmap.

## 2. Design Goals

Step 5 tightened the semantic boundary in six specific ways:

- semantic computation was separated from candidate generation
- semantic identity was separated from `window_id`
- SQLite was reduced to an optional candidate provider
- preview rendering was separated from semantic representative selection
- topic artifacts became span/message-first
- migration was made explicit and regeneration-based rather than compatibility-based

The refactor did **not** redesign semantic scoring, clustering thresholds, or
topic-label generation logic. It changed boundaries and contracts, not the core
semantic intent of the existing prototype.

## 3. Layer Boundaries

The current semantic pipeline has four distinct concerns.

### Semantic core

In [analyzer_semantic_prototype.py](/Users/tanabeshunji/Documents/llm-logparser/src/llm_logparser/core/analyzer_semantic_prototype.py),
the semantic core is the provider-agnostic computation path that:

- works from candidate spans that already exist
- computes similarities
- selects neighbors
- constructs the retained graph
- emits cluster membership

The important boundary is:

`candidate_pools -> semantic core -> neighbor rows / cluster rows`

The semantic core does not query SQLite and does not decide how candidate pools
were assembled.

### Candidate provider layer

Candidate generation now sits behind the provider boundary in
[analyzer_semantic_prototype.py](/Users/tanabeshunji/Documents/llm-logparser/src/llm_logparser/core/analyzer_semantic_prototype.py).

Current providers are:

- full-scan candidate generation
- SQLite-assisted candidate narrowing

Both paths feed the same semantic orchestration entry point. Provider choice is
allowed to change candidate pool scope, but it must not change the meaning of
semantic identity, scoring, or artifact schemas.

### Presentation layer

In [analyzer_semantic_preview.py](/Users/tanabeshunji/Documents/llm-logparser/src/llm_logparser/core/analyzer_semantic_preview.py),
representative selection and rendering are now separated.

Selection decides which records matter.
Presentation decides how selected records are shown.

Display helpers such as excerpt shaping, turn formatting, and truncation are
presentation-only and must not define semantic selection behavior.

### Semantic artifact contract layer

In [analyzer_semantic_topics.py](/Users/tanabeshunji/Documents/llm-logparser/src/llm_logparser/core/analyzer_semantic_topics.py)
and
[analyzer_semantic_topic_explore.py](/Users/tanabeshunji/Documents/llm-logparser/src/llm_logparser/core/analyzer_semantic_topic_explore.py),
semantic artifacts are now expressed primarily in span/message terms.

That contract is the semantic surface other Step 5 consumers are expected to
read. It is intentionally explicit and versioned.

## 4. Semantic Identity

`span_id` is now the primary internal semantic identity.

In the current implementation, `span_id` is derived from ordered
`message_ids`. That means:

- the same ordered `message_ids` produce the same semantic identity
- changing message order changes semantic identity
- `window_id` is no longer the semantic identity anchor

This change matters because semantic nodes are now grounded in the content span
itself rather than the current L1 window label.

`window_id` still exists in parts of the pipeline, but its role is now limited
to compatibility and presentation:

- compatibility with existing Step 5 artifact shapes
- bridge fields in current browse/render flows
- labels that remain convenient for humans and for current window-derived inputs

Future changes should treat `span_id` as semantic truth and `window_id` as an
overlay unless a field is explicitly documented otherwise.

## 5. Candidate Providers

Semantic computation is now provider-driven.

The semantic prototype resolves a candidate provider first, then sends the
resulting pools into the same semantic computation path. In practice this means:

- full-scan mode and SQLite-assisted mode share one semantic orchestration path
- the semantic core is provider-agnostic
- SQLite is only allowed to narrow candidate pools

SQLite is **not**:

- semantic truth
- semantic identity
- semantic output schema
- semantic scoring logic

SQLite still contains provider-specific query logic inside the SQLite candidate
provider implementation, but that coupling is bounded to provider helpers and
is not part of semantic computation itself.

For the same reason, `message_windows.jsonl` is not semantic truth. It is a
deterministic L1 candidate-span membership artifact keyed by ordered
`message_ids`. Semantic consumers may use it to choose spans, but any text or
role sequence needed for semantic work must be reconstructed from canonical
`parsed.jsonl` messages. L3 should operate on explicit reconstructed message
sequences, not on message boundaries re-inferred from a flattened window text
projection. `message_windows.jsonl` is therefore a convenience input to L3,
not the only structural gateway: semantic prototype flows may derive equivalent
default candidate windows directly from canonical `parsed.jsonl`.

## 6. Presentation vs Semantics

Preview formatting must not change semantic outcomes.

The Step 5 preview cleanup established these rules:

- representative selection happens before rendering
- rendering helpers operate only on already-selected records
- truncation, line shaping, and compact display formatting must not change which
  representatives are chosen

Representative windows and rendered excerpts still exist because they remain
useful for browsing and topic display. They are no longer the semantic core of
the contract.

## 7. Semantic Topic Artifacts

The current Step 5 semantic artifact contract is span/message-first.

### `topics.json`

`topics.json` is now version `2.0`.

Its primary semantic grounding is:

- `span_refs`
- `message_refs`
- `span_count`
- `representative_spans`

Those fields express what the topic actually contains and how it is grounded
back to canonical messages.

Window-shaped fields remain only as overlays:

- `window_refs`
- `representative_windows`
- optional `window_id` fields attached to span-oriented rows

Those fields are still useful for browse flows and current compatibility, but
they are not the semantic contract core.

### `topic_membership.jsonl`

`topic_membership.jsonl` is now version `1.0`.

Its semantic membership types are:

- `cluster`
- `span`
- `message`

The old `window` membership contract is no longer the semantic basis of topic
membership.

The important implication is that topic membership is now grounded in:

- `topic_id`
- `span_id`
- `message_id`

and not primarily in `window_id`.

## 8. Migration and Breaking Changes

This contract is intentionally breaking.

Step 5 changed semantic schemas and semantic identity assumptions. The expected
migration strategy is simple:

- keep canonical inputs as the source of truth
- regenerate Step 5 semantic artifacts from the current pipeline
- do not silently reuse old window-centric semantic artifacts as if they were current

Affected artifacts include at least:

- `topics.json`
- `topic_membership.jsonl`

Current readers make this explicit. They reject incompatible legacy semantic
artifacts and instruct the user to regenerate from canonical inputs rather than
silently accepting mixed-version contracts.

## 9. Non-Goals / What This Contract Does Not Mean

This contract does **not** mean:

- window-derived artifacts disappear from the pipeline
- `window_id` disappears from all outputs
- browse/explore views stop showing window-friendly labels
- SQLite stops being useful for narrowing candidates
- semantic scoring or clustering was redesigned

It also does not mean every upstream semantic artifact is already fully
span-native. For example, the semantic prototype still emits window-shaped
neighbor and cluster artifacts for current compatibility even though internal
semantic identity is span-based. That compatibility does not permit L1 to store
semantic window renderings; window-shaped outputs still trace back through
canonical message IDs.

## 10. Practical Rules for Future Changes

When changing the semantic layer, preserve these rules:

- Do not reintroduce `window_id` as semantic identity.
- Do not let candidate-provider details leak into semantic scoring or clustering logic.
- Do not let preview truncation or display formatting affect semantic selection.
- Do not treat `window_refs`, `representative_windows`, or window-friendly labels as semantic truth.
- Do not silently accept incompatible legacy semantic artifacts; require regeneration instead.
- Prefer `span_id` and `message_ids` when adding new semantic references.
- Keep SQLite optional and bounded to candidate narrowing or browse/index use.

If a change makes those rules less clear, it is probably weakening the Step 5
boundary rather than extending it cleanly.
