# Hermes-Inspired Review Workflow Roadmap

Date: 2026-06-03

## 1. Purpose

This planning document records how selected architecture patterns from
NousResearch's Hermes Agent could inform future `llm-logparser` review and
recall workflows.

Hermes Agent is used here only as a reference for reusable design patterns. It
is not an integration target, dependency, runtime provider, or source of code to
port. The goal is to borrow useful workflow logic while preserving
`llm-logparser`'s existing principles:

- canonical-first architecture
- local-first operation
- deterministic and rebuildable lower-layer artifacts
- explicit provenance
- human-reviewed policy
- no hidden state
- no autonomous mutation
- clear separation between parse, L1, L2, L3, and L4

The main Hermes-inspired ideas worth adapting are review queues, anchored
evidence recall, lifecycle diagnostics, explicit policy maintenance surfaces,
and report-first maintenance workflows. Those ideas should be translated into
`llm-logparser` artifacts and commands that remain inspectable and safe to
delete and rebuild.

## 2. Current llm-logparser stage model

The current architecture is layered:

```text
raw provider export
  -> parse
  -> canonical parsed.jsonl
  -> L1 deterministic sidecars/views
  -> L2 optional SQLite index
  -> L3 semantic / lexical candidate layers
  -> L4 future memory / LLM-derived layers
```

### Parse

The parse layer expands provider-specific exports and normalizes them into the
canonical JSONL record stream. It owns provider adaptation, canonical message
records, thread records, and stable structural normalization. Its output,
`parsed.jsonl`, is the source of truth for all downstream stages.

Parse must not perform semantic interpretation, topic inference, recurrence
detection, lexical policy generation, memory extraction, or summarization.

### L1 deterministic sidecars

L1 owns deterministic analysis and helper artifacts derived from canonical
message rows. Current examples include `thread_stats.json`,
`message_windows.jsonl`, `token_stats.json`, `metrics.json`, timeline views,
datasheet views, and token-dictionary facts.

L1 artifacts may be materialized during parse for performance or convenience,
but conceptually they remain deterministic lower-layer artifacts. They are
rebuildable and must not become semantic truth.

### L2 optional SQLite index

L2 owns `analysis.db`, a deterministic, rebuildable query accelerator derived
from canonical and lower-layer artifacts. It can support cross-thread query,
aggregation, candidate narrowing, and future recall/search workflows.

SQLite is optional. Absence of `analysis.db` must not make canonical, L1, or
full-scan L3 workflows invalid.

### L3 semantic / lexical candidate layers

L3 owns optional semantic and lexical interpretation artifacts. Current
examples include semantic prototype neighbors/clusters, semantic topics,
cross-thread candidates, intra-thread topic segments/summaries, token-derived
lexical-rule candidates, and deterministic review reports such as
`l3/cross-thread-candidates/narrative.md`.

L3 artifacts may be heuristic, model-assisted, or experimental, but they remain
derived. They must preserve source references, should expose diagnostics, and
must not rewrite canonical, L1, or L2 artifacts.

### L4 memory / LLM-derived layers

L4 owns future higher-level semantic condensation, same-intent evaluation,
memory recall views, decision extraction, next-action extraction, and other
local-LLM or API-derived analysis. Current experimental examples include
`l4/cross-thread-intent-eval/evaluations.jsonl` and read-only
`cross-thread-memory-recall`.

L4 outputs are non-authoritative, reviewable, provenance-bearing derived
artifacts. They may summarize or classify, but they must never replace source
evidence.

## 3. Proposed stage-by-stage changes

### Parse

Parse should remain unchanged.

`parsed.jsonl` must remain canonical. The parse layer must continue to preserve
structure before interpretation and must not gain Hermes-style memory,
learning, policy promotion, topic inference, or background review behavior.

Future review and recall workflows should consume parse output, not extend
parse responsibilities.

### L1

L1 should remain deterministic and rebuildable. No semantic policy should be
activated here.

Potential additions:

- deterministic helper fields or reports that make later review easier
- deterministic span/window coverage diagnostics
- deterministic links from `message_windows.jsonl` to canonical message IDs
  where current contracts already support that identity

L1 should not write review decisions, accepted lexical rules, topic lifecycle
states, or memory summaries.

### L2

L2 should be extended with FTS-based recall/search over canonical message text.

Planned capabilities:

- FTS5 message index built from canonical `parsed.jsonl` message text
- role filters based on canonical normalized roles
- provider, conversation, thread, and timestamp filters
- optional CJK/substring search strategy where SQLite support and project
  dependencies allow it
- anchored evidence windows around a matched message or span
- bookends from the beginning and end of matched conversations
- optional lineage/thread dedupe if future provider exports expose lineage or
  continuation metadata

The index should remain optional and non-canonical:

- every indexed message must be reconstructible from `parsed.jsonl`
- recall output should carry canonical references, not SQLite row IDs as
  semantic identity
- deleting `analysis.db` must not delete knowledge or policy
- L3 full-scan modes must remain valid when L2 is absent

Hermes' useful pattern here is not agent memory. It is anchored transcript
search that returns actual message evidence without model summarization.

### L3

L3 should gain a unified review workflow above existing candidate artifacts.

Planned additions:

- review queue artifacts that can aggregate or reference existing candidate
  sources
- lexical-rule candidates from `l3/lexical-rules/candidates.jsonl`
- topic merge candidates from cross-thread or future cross-cluster evidence
- suppression diagnostics explaining why a candidate was weakened or filtered
- recurrence candidates for distant or resurfaced related spans
- stale/resurfaced topic diagnostics derived from `topics.json`,
  `topic_membership.jsonl`, and cross-thread evidence
- policy effectiveness instrumentation showing how reviewed lexical policy
  changes candidate admission, scoring, suppression, or ranking

Relationship to current artifacts:

- `l3/lexical-rules/candidates.jsonl` remains the inactive lexical suggestion
  source.
- `l3/cross-thread-candidates/candidates.jsonl` remains the candidate-pair
  source for cross-thread continuity.
- `l3/cross-thread-candidates/narrative.md` remains a deterministic
  human-readable review/debug artifact.
- A future `l3/review-queue/` should not replace these sources immediately. It
  should first reference and normalize them into one review surface.
- Existing candidate commands must not promote, reject, or activate policy
  automatically.

The review queue should encode status, evidence, proposed changes, and
diagnostics. It should not be treated as accepted policy.

### L4

L4 can later add topic memory cards or semantic condensation artifacts.

These artifacts should be:

- derived from L3 topics, candidates, and canonical reconstructed messages
- source-referenced by provider, conversation, span, message IDs, and text hash
- reviewable by humans
- hash-backed to detect drift
- explicit about model, prompt, config, and source inputs
- non-authoritative

Topic memory cards may summarize repeated work, decisions, open questions, and
next actions, but source evidence remains canonical. A memory card is a compact
view, not truth.

## 4. Proposed artifacts

### `l3/review-queue/candidates.jsonl`

- Producer command: future `analyze review-candidates`
- Consumer: reviewers, future read-only inspection commands, possible GUI
- Deterministic: deterministic when generated only from deterministic inputs;
  may include model-derived candidates only when provenance marks that source
- Canonical: no
- Relationship: normalized review surface over existing lexical, cross-thread,
  recurrence, suppression, and topic lifecycle candidate artifacts
- Expected fields:
  - `record_type`
  - `schema_version`
  - `candidate_id`
  - `candidate_type`
  - `status`
  - `activation_state`
  - `source_artifact`
  - `source_command`
  - `source_config_hash`
  - `provider_id`
  - `scope`
  - `evidence_refs`
  - `diagnostics`
  - `proposed_change`
  - `risk_flags`
  - `review_notes`

### `l3/review-queue/report.json`

- Producer command: future `analyze review-candidates`
- Consumer: CLI/GUI review summaries and tests
- Deterministic: yes when inputs are deterministic
- Canonical: no
- Relationship: compact aggregate over `l3/review-queue/candidates.jsonl`
- Expected fields:
  - `artifact_type`
  - `schema_version`
  - `source_inputs`
  - `candidate_counts_by_type`
  - `candidate_counts_by_status`
  - `risk_counts`
  - `coverage`
  - `policy_layers_consulted`
  - `warnings`

### `l3/review-queue/report.md`

- Producer command: future `analyze review-candidates`
- Consumer: human reviewers
- Deterministic: yes when generated from deterministic inputs
- Canonical: no
- Relationship: Markdown rendering of the review queue and report summary
- Expected sections:
  - candidate index
  - lexical suggestions
  - topic merge suggestions
  - recurrence/stale/resurfaced topics
  - suppression diagnostics
  - high-risk or low-confidence candidates
  - copyable reviewed-policy snippets where appropriate

### `l3/diagnostics/policy_effectiveness.json`

- Producer command: future `analyze policy-effectiveness`
- Consumer: reviewers validating reviewed lexical/topic policy
- Deterministic: yes when comparing deterministic runs
- Canonical: no
- Relationship: compares candidate behavior with and without selected reviewed
  policy files
- Expected fields:
  - `artifact_type`
  - `schema_version`
  - `baseline_config`
  - `policy_config`
  - `policy_layers`
  - `candidate_count_delta`
  - `admission_delta`
  - `score_delta_summary`
  - `suppression_delta`
  - `rank_delta_examples`
  - `warnings`

### `l3/diagnostics/suppression_explanations.jsonl`

- Producer command: future review/candidate diagnostics commands
- Consumer: reviewers and tests
- Deterministic: yes for deterministic scoring paths
- Canonical: no
- Relationship: expands current overlap and residue diagnostics into a common
  inspection surface
- Expected fields:
  - `record_type`
  - `schema_version`
  - `subject_id`
  - `subject_type`
  - `suppression_type`
  - `matched_policy_layer`
  - `matched_rule_ref`
  - `evidence_tokens`
  - `effect`
  - `explanation`

### `l3/diagnostics/topic_lifecycle_report.json`

- Producer command: future `analyze stale-topics`
- Consumer: reviewers, future GUI, possible L4 topic memory card generation
- Deterministic: yes if based on stored topic timestamps and deterministic
  state heuristics
- Canonical: no
- Relationship: extends current topic state fields with maintenance-oriented
  diagnostics
- Expected fields:
  - `artifact_type`
  - `schema_version`
  - `source_topics`
  - `topic_counts_by_state`
  - `stale_topics`
  - `resurfaced_topics`
  - `recurrence_candidates`
  - `state_conflicts`
  - `warnings`

### `l2/recall/evidence_windows.jsonl` or read-only recall output

- Producer command: future `analyze recall` when writing is requested
- Consumer: reviewers, L3 diagnostics, future GUI
- Deterministic: yes for identical index/input/query/settings
- Canonical: no
- Relationship: query-specific evidence view backed by canonical message refs
- Expected fields:
  - `record_type`
  - `schema_version`
  - `query`
  - `match_id`
  - `provider_id`
  - `conversation_id`
  - `anchor_message_id`
  - `anchor_span_id`
  - `role`
  - `rank`
  - `score`
  - `window_before`
  - `window_after`
  - `bookend_start`
  - `bookend_end`
  - `message_refs`

Query-specific recall artifacts may be useful for review runs, but the default
`recall` command should probably be read-only terminal/JSON output to avoid
query artifact churn.

### `l4/topic-memory-cards/cards.jsonl`

- Producer command: future `analyze topic-memory-cards`
- Consumer: future L4 recall, review UI, narrative diagnostics
- Deterministic: only for heuristic mode; model-derived mode is
  non-deterministic unless model/runtime guarantees are explicitly constrained
- Canonical: no
- Relationship: L4-prep semantic condensation over L3 topics and evidence
- Expected fields:
  - `record_type`
  - `schema_version`
  - `card_id`
  - `topic_id`
  - `state`
  - `summary`
  - `decisions`
  - `open_questions`
  - `next_actions`
  - `source_span_refs`
  - `source_message_refs`
  - `text_sha1`
  - `model`
  - `prompt_hash`
  - `source_inputs`
  - `review_state`

## 5. Proposed commands

The commands below are proposals only.

### `llm-logparser analyze search-index --input <artifact-root> --provider <provider>`

- Purpose: build or refresh the L2 FTS message index
- Inputs: provider artifact root, canonical `parsed.jsonl`, existing
  `analysis.db` when refreshing
- Outputs: `analysis.db` with message FTS tables and related metadata
- Stage ownership: L2
- Writes artifacts: yes
- Read-only: no

This may instead be implemented as an extension of `analyze sqlite-build` if
that keeps the CLI simpler.

### `llm-logparser analyze recall --input <artifact-root> --query "<text>"`

- Purpose: search canonical message text through the optional L2 index and
  render anchored evidence
- Inputs: provider artifact root, `analysis.db`, query, optional filters
- Outputs: terminal text or JSON by default; optional evidence-window artifact
  only when an explicit output flag is provided
- Stage ownership: L2 presentation/read path
- Writes artifacts: no by default
- Read-only: yes by default

Expected filters include role, provider, conversation, timestamp range, limit,
sort mode, and possibly source/lifecycle filters once those exist.

### `llm-logparser analyze review-candidates --input <provider-root>`

- Purpose: collect existing lexical, cross-thread, recurrence, suppression, and
  topic lifecycle suggestions into one review queue
- Inputs: `l3/lexical-rules/candidates.jsonl`,
  `l3/cross-thread-candidates/candidates.jsonl`, `topics.json`,
  `topic_membership.jsonl`, optional diagnostics
- Outputs: `l3/review-queue/candidates.jsonl`, `report.json`, `report.md`
- Stage ownership: L3
- Writes artifacts: yes
- Read-only: no, but writes only derived review artifacts

This command must not promote candidates or edit reviewed policy files.

### `llm-logparser analyze stale-topics --input <provider-root>`

- Purpose: identify stale, resurfaced, conflicting, or recurrence-prone topics
- Inputs: L3 semantic topics, membership, cross-thread candidates, timestamps
- Outputs: `l3/diagnostics/topic_lifecycle_report.json` and optional Markdown
  report
- Stage ownership: L3
- Writes artifacts: yes
- Read-only: no, but writes diagnostics only

### `llm-logparser analyze policy-effectiveness --input <provider-root>`

- Purpose: compare candidate behavior with and without selected reviewed
  lexical/topic policy
- Inputs: candidate artifacts, built-in policy resources, explicit reviewed
  project/user YAML
- Outputs: `l3/diagnostics/policy_effectiveness.json` and optional Markdown
  report
- Stage ownership: L3
- Writes artifacts: yes
- Read-only: no, but writes diagnostics only

This should make policy impact visible before any reviewer chooses to keep a
rule.

### `llm-logparser analyze topic-memory-cards --input <provider-root>`

- Purpose: generate future L4 topic memory cards from reviewed or selected L3
  topics and evidence
- Inputs: `topics.json`, `topic_membership.jsonl`, canonical reconstructed
  messages, optional L4 evaluations
- Outputs: `l4/topic-memory-cards/cards.jsonl` plus summary diagnostics
- Stage ownership: L4
- Writes artifacts: yes
- Read-only: no

This should remain future work until the review queue and lifecycle diagnostics
are stable.

## 6. Schema planning

These shapes are intentionally high-level. They are not final implementation
schemas.

### Review queue candidate row

```json
{
  "record_type": "review_candidate",
  "schema_version": "0.1",
  "candidate_id": "candidate_xxx",
  "candidate_type": "lexical_rule|topic_merge|suppression|recurrence|topic_state|memory_card",
  "status": "inactive|candidate|accepted|rejected|suppressed|stale|retired",
  "activation_state": "requires_review",
  "scope": "provider|project|user",
  "source_artifact": "l3/lexical-rules/candidates.jsonl",
  "source_command": "analyze lexical-rule-candidates",
  "source_config_hash": "sha1...",
  "evidence_refs": [],
  "diagnostics": {},
  "proposed_change": {},
  "risk_flags": [],
  "review_notes": null
}
```

Status fields should describe review workflow only. Accepted policy should live
in explicit reviewed YAML or future reviewed topic-policy resources, not in the
candidate queue itself.

### Anchored evidence window row

```json
{
  "record_type": "anchored_evidence_window",
  "schema_version": "0.1",
  "query": "search text",
  "match_id": "match_xxx",
  "provider_id": "openai",
  "conversation_id": "conversation_xxx",
  "anchor_message_id": "message_xxx",
  "anchor_span_id": null,
  "rank": 1,
  "score": 0.0,
  "filters": {},
  "message_refs": [],
  "bookend_start": [],
  "bookend_end": [],
  "text_sha1": "sha1..."
}
```

The row should return canonical evidence references and display text, not treat
SQLite row IDs as semantic identity.

### Policy effectiveness diagnostics

```json
{
  "artifact_type": "policy_effectiveness",
  "schema_version": "0.1",
  "baseline": {
    "policy_layers": []
  },
  "comparison": {
    "policy_layers": []
  },
  "candidate_count_delta": {},
  "admission_delta": {},
  "score_delta_summary": {},
  "suppression_delta": {},
  "rank_delta_examples": [],
  "warnings": []
}
```

The goal is explanation, not automatic optimization.

### Topic lifecycle diagnostics

```json
{
  "artifact_type": "topic_lifecycle_report",
  "schema_version": "0.1",
  "source_topics": "l3/semantic-topics/topics.json",
  "topic_counts_by_state": {},
  "stale_topics": [],
  "resurfaced_topics": [],
  "recurrence_candidates": [],
  "state_conflicts": [],
  "warnings": []
}
```

Lifecycle rows should distinguish evidence from interpretation. A resurfaced
topic candidate is not an automatic topic merge.

### Topic memory card

```json
{
  "record_type": "topic_memory_card",
  "schema_version": "0.1",
  "card_id": "card_xxx",
  "topic_id": "topic_xxx",
  "review_state": "unreviewed|reviewed|deprecated",
  "state": "unresolved|in_progress|done",
  "summary": "",
  "decisions": [],
  "open_questions": [],
  "next_actions": [],
  "source_span_refs": [],
  "source_message_refs": [],
  "text_sha1": "sha1...",
  "provenance": {
    "producer_layer": "L4",
    "model": null,
    "prompt_hash": null,
    "source_inputs": []
  }
}
```

Cards should be invalidated or skipped when source span text hashes drift.

## 7. Documentation updates required

After implementation, update these documents:

- `docs/analysis-artifacts.md`: add L2 FTS recall/search artifacts, review
  queue placement, and topic memory card placement.
- `docs/analyzer.md`: add command documentation for search/recall,
  review-candidates, stale-topics, policy-effectiveness, and future
  topic-memory-cards.
- `docs/semantic-contract.md`: define how review queue candidates relate to
  `span_id`, `message_ids`, `text_sha1`, and L4 span-pair semantics.
- `docs/semantic-topic-tracking.md`: add lifecycle diagnostics, stale/resurfaced
  topic reports, and topic memory cards as future L4-prep.
- `docs/lexical-policy.md`: document how lexical-rule candidates flow into a
  broader review queue and how policy effectiveness diagnostics compare
  reviewed policy layers.
- `docs/README.md` or top-level `README.md`: mention recall/search and review
  queue workflows once user-facing commands exist.
- `docs/config-guide.md`: document any profile-backed defaults for recall,
  FTS, policy files, or review commands. Keep explicit input paths for
  high-impact semantic commands where current safety rules require them.
- `docs/test-strategy.md`: add schema, determinism, SQLite/FTS, recall, review
  queue, and no-autonomous-mutation test expectations.
- `docs/roadmap.md`: update current focus if the project moves from validation
  into review workflow implementation.

## 8. Test strategy

Future implementation should add tests before broadening behavior.

Required test categories:

- artifact schema tests for every new JSON/JSONL artifact
- deterministic output tests for review reports and diagnostics
- SQLite/FTS rebuild tests proving `analysis.db` can be deleted and recreated
- recall output tests for role/provider/conversation filters, ranking, anchored
  windows, and bookends
- CJK/substring behavior tests if trigram or fallback substring search is
  implemented
- review queue generation tests covering lexical candidates, cross-thread
  candidates, suppression diagnostics, and missing optional inputs
- policy effectiveness tests comparing baseline and reviewed-policy runs
- topic lifecycle diagnostics tests for stale, resurfaced, conflicting, and
  unchanged topics
- topic memory card drift tests using `text_sha1`
- no-autonomous-mutation tests proving commands do not edit reviewed policy,
  parse output, L1 artifacts, or canonical topic membership without explicit
  user-requested commands
- backward compatibility tests proving existing artifacts and commands remain
  readable or fail with clear regeneration guidance

Golden artifacts should remain small, diff-friendly, and free of private user
conversation content.

## 9. Non-goals and anti-patterns

Do not adopt these Hermes Agent behaviors:

- autonomous memory mutation
- autonomous policy activation
- live prompt memory injection
- cloud memory providers as defaults
- summaries replacing canonical text
- background jobs that mutate policy without explicit user action
- generic plugin architecture unless a concrete long-term need justifies it
- hidden state outside artifact roots or explicit user configuration
- model-written reviewed policy without human review
- scheduled jobs that accept, reject, or retire policy automatically

Review workflows may suggest. They must not silently decide.

## 10. Suggested implementation order

1. Documentation/planning only.
   - Land this roadmap.
   - Confirm stage boundaries and artifact names before code work.

2. Extend L2 with FTS recall/search.
   - Prefer extending `analyze sqlite-build` unless a separate
     `search-index` command is clearer.
   - Add canonical message text indexing and metadata needed for filters.

3. Add read-only anchored recall.
   - Implement `analyze recall` as terminal/JSON output first.
   - Include anchored windows and bookends.
   - Avoid writing query artifacts by default.

4. Add the L3 review queue artifact.
   - Start by aggregating existing `lexical-rule-candidates` and
     `cross-thread-candidates`.
   - Keep it inactive and review-only.

5. Add policy effectiveness diagnostics.
   - Compare baseline and reviewed-policy candidate behavior.
   - Keep output explanatory, not prescriptive.

6. Add topic lifecycle diagnostics.
   - Report stale, resurfaced, conflicting, and recurring topics.
   - Do not merge topics automatically.

7. Add suppression explanation artifacts.
   - Normalize existing overlap/residue/persona diagnostics into a reusable
     review surface.

8. Investigate L4 topic memory cards.
   - Start with heuristic/deterministic cards if possible.
   - Add model-derived fields only with explicit provenance and drift checks.

9. Consider scheduled local report refresh only after the commands above are
   stable.
   - Scheduled jobs should produce reports, not mutate reviewed policy.

## 11. Open questions

- Should FTS be added to the existing `analysis.db` schema by default, or only
  behind an explicit `--with-fts` / `--search-index` option?
- Should query-specific recall evidence ever be written to disk by default, or
  only when `--out` is provided?
- What is the minimum stable identity for review candidates that aggregate
  multiple source artifacts?
- Should `l3/review-queue/candidates.jsonl` duplicate source candidate fields
  or store compact references plus normalized diagnostics?
- Which candidate statuses are workflow state, and which belong only in
  reviewed policy files?
- Where should accepted reviewed topic policy live if topic merge/suppression
  policy becomes explicit?
- How should policy effectiveness diagnostics avoid leaking sensitive token
  lists while remaining useful?
- What lifecycle thresholds should define stale and resurfaced topics, and
  should they be configurable?
- Should topic memory cards require a reviewed topic state before generation?
- What should invalidate a topic memory card besides `text_sha1` drift?
- How should local model provenance be represented consistently across
  existing L4 evaluations and future memory cards?
- Is a scheduler needed at all, or are explicit CLI refresh commands sufficient
  for the foreseeable future?
