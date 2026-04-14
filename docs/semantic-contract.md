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
- `window_id` is only a compatibility/presentation overlay unless ordered
  `message_ids` are unavailable, in which case it may appear only in a
  deterministic fallback identity path for older or partial rows

This change matters because semantic nodes are now grounded in the content span
itself rather than the current L1 window label.

`window_id` still exists in parts of the pipeline, but its role is now limited
to compatibility and presentation:

- compatibility with existing Step 5 artifact shapes
- bridge fields in current browse/render flows
- labels that remain convenient for humans and for current window-derived inputs

Future changes should treat `span_id` as semantic truth and `window_id` as an
overlay unless a field is explicitly documented otherwise.

### Span Definition

A **span** is defined by:

- `provider_id`
- `conversation_id`
- ordered `message_ids`

`span_id` is derived from that ordered message sequence.

This means:

- identical ordered message sequences produce the same `span_id`
- changing message order changes `span_id`
- changing message membership changes `span_id`

A span is therefore the semantic identity unit used across current L3/L4 join
surfaces. When the semantic layer refers to "the same span", it means the same
ordered canonical message sequence, not the same `window_id`.
Spans are reconstructible from canonical parsed.jsonl using their ordered message_ids.

### Window Definition

A **window** is a deterministic segmentation unit produced by
`message_windows.jsonl`.

Its current roles are:

- candidate generation
- embedding
- neighbor scoring
- clustering

`window_id` is not semantic identity. It is a deterministic computational label
attached to one current segmentation output. It should be treated as
provenance, compatibility, and computational substrate rather than semantic
truth.

### Span and Window Relationship

In the current default pipeline, spans are often derived from selected windows.
That is the present implementation path for many L3 operations, especially when
representative spans are selected from current window-backed cluster members.

However, that implementation path does not redefine the semantic contract:

- the semantic definition of a span is still ordered canonical `message_ids`
- a window is still only one deterministic way to surface candidate message
  sequences
- `window_id` remains an overlay even when a span currently originates from one
  selected window

Spans may diverge from single windows when representative-span refinement is
enabled. In that mode, a span may be a conservative split or merge over one or
more windows while preserving the same span-first identity rule.

Window configuration affects candidate availability, not semantic identity:

- changing window size or stride changes which candidate windows are available
- that may change which spans are surfaced or selected in the current pipeline
- it does not change the definition of span identity itself

### Similarity Interpretation

The current pipeline uses similarity at more than one practical distance.

Near-window similarity inside the same thread is primarily a conversation
continuity signal. It is useful for identifying local semantic proximity,
constructing neighbor links, and forming cluster structure over nearby or
related segments.

Similarity between nearby windows within the same thread is weaker evidence of
topic recurrence than similarity observed across distant spans or across threads.

Distant or cross-thread similarity is stronger evidence of topic recurrence. It
suggests that similar work, state, or discussion has reappeared outside a
single local conversation flow.

Window-level similarity alone does not define a topic. Topics remain higher
level semantic artifacts built from retained structure and interpreted spans,
not from a single window-window score in isolation.

### L4 Input Semantics

L4 operates on **span pairs**, not on windows.

The current contract is:

- L3 may use window-backed similarity and clustering to surface candidate spans
- cross-thread candidate generation emits source-span to target-span pairs
- candidate rows may also carry deterministic recurrence-oriented inspection
  signals such as dormancy, specificity, continuity masking, and local-context
  delta; these are additive instrumentation fields, not semantic identity
- L4 evaluates whether those two spans express the same underlying intent,
  event, or task continuation

Window similarity is therefore proposal evidence only. The L4 question is not
"are these two windows similar?" but "do these two spans represent the same
intent?"

### Semantic Normalization Join Contract

When `semantic-topics` consumes a semantic-normalization batch job, the join
contract is:

- primary key: `span_id`
- validation key: `text_sha1`
- payload target: `representative_spans[*].semantic_normalization`

`text_sha1` is computed from reconstructed full span text, not from excerpts.
The hashed text is the canonical ordered message text sequence for the span,
joined with `"\n\n"` between non-empty message texts and encoded as UTF-8
before SHA-1 hashing.

Producer and consumer must use the same reconstruction contract. A
`text_sha1` mismatch is treated as drift:

- warn
- skip attachment for that representative span
- keep the representative span otherwise unchanged

Missing `span_id` matches are not errors. They leave the representative span
unannotated.

The contract does not allow:

- fuzzy matching
- fallback joins on `window_id`
- partial attachment after drift

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

`topics.json` is now version `2.2`.

Its primary semantic grounding is:

- `span_refs`
- `message_refs`
- `span_count`
- `representative_spans`

Those fields express what the topic actually contains and how it is grounded
back to canonical messages.

State is now also part of the L3 contract, but it remains interpretive rather
than canonical. The current MVP state engine is deterministic and rule-based:

- topic `state` uses only the canonical values `unresolved`, `in_progress`,
  and `done`
- topic `state_confidence` is an aggregated heuristic confidence
- each `span_ref` / `representative_span` carries its own `state`,
  `state_confidence`, and diagnostic `state_signals`
- classification runs on reconstructed ordered span messages, not on L1 window
  text projections
- phrase resources are locale-aware L3 data inputs selected via explicit
  `state_locale` when needed, with `en-US` fallback
- topic aggregation is conservative: any `in_progress` span keeps the topic
  `in_progress`; otherwise any `unresolved` span keeps it `unresolved`

Window-shaped fields remain only as overlays:

- `window_refs`
- `representative_windows`
- optional `window_id` fields attached to span-oriented rows

Those fields are still useful for browse flows and current compatibility, but
they are not the semantic contract core.

If batch semantic normalization is consumed, `provenance.normalization`
documents that the batch job was consulted during artifact build. It does not
mean every representative span attached successfully. The counts
`matched_representative_span_count`,
`unmatched_representative_span_count`, and
`drifted_representative_span_count` are disjoint categories over evaluated
representative spans.

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
