# Analyzer Architecture

The `analyze` subsystem provides analysis capabilities on top of the
canonical JSONL conversation format produced by the parser.

Its purpose is **not** to replace the parser or exporter, but to build
additional insight layers on top of the normalized conversation data.

The canonical source of truth remains `parsed.jsonl`. Analyzer outputs are
derived layers built above that canonical base. Deterministic layers remain
the stable foundation; optional higher layers may add capability, but they do
not replace canonical or deterministic artifacts.

The design follows a **layered analysis model**, where each layer adds
capability while preserving deterministic and local-first behavior
whenever possible.

---

# Philosophy

The analyzer is designed around three core principles:

- **Canonical input**  
  All analysis operates on normalized JSONL threads produced by the parser.
  `parsed.jsonl` remains the canonical source of truth.

- **Deterministic first**  
  Analyses that do not require AI models should remain deterministic,
  reproducible, and rebuildable from canonical data.

- **Progressive capability**  
  More powerful analysis layers exist above deterministic ones but remain
  optional, additive, and isolated from users who do not use them.

This allows users to run the analyzer in environments ranging from:

- fully local after dependencies/resources are available
- local-LLM only
- hybrid (local + API)
- API-driven workflows

---

# Layered Analysis Model

The analyzer is structured into four conceptual layers.

```
L1  Deterministic analysis over canonical parsed.jsonl
L2  Deterministic / rebuildable SQLite analysis index
L3  Optional semantic topic tracking layer
L4  Optional external/API LLM layer
```

Each layer builds on the same canonical JSONL conversation structure.

Architecture boundary:

- command level:
  deterministic commands and optional higher-layer commands remain separate
- artifact/output-layer level:
  canonical data, deterministic sidecars/views, optional SQLite indexes, and
  future model-derived or GUI-oriented outputs remain separate artifacts

This separation matters because `analysis.db` is an optional analysis index,
not canonical storage. Higher layers may be model-derived and non-deterministic,
but they must remain opt-in additive layers on top of the deterministic base.

Users who only rely on `parsed.jsonl`, `token_stats.json`, `metrics.json`, or
the deterministic views should not be affected by whether L2, L3, L4, or any
future GUI-oriented layer is used.

---

# Layer 1 — Deterministic Text Analysis

This layer performs analysis using only the normalized JSONL data.

No databases, no AI models.

These analyses are fast, reproducible, and safe to run in any environment.
Their outputs are deterministic artifacts or views derived from canonical data.

Current Layer 1 implementations include:

- `analyze stats`
- `analyze datasheet`
- `analyze timeline`
- `analyze tokens`
- `analyze metrics`
- human-readable stats/timeline views and machine-readable sidecars

`analyze tokens` writes deterministic `token_stats.json` sidecars from canonical
`parsed.jsonl` using `tiktoken`.

`analyze metrics` writes deterministic `metrics.json` sidecars from
`parsed.jsonl` plus `token_stats.json`, including:

- ratio / token / character / distribution / diversity metrics
- heuristic `safety.refusal`
- heuristic `interaction.revision`
- additive `user_effort` metrics derived from assistant → user timing and text length

## Analyze Pipeline

The analyze subcommands sit on top of canonical `parsed.jsonl`:

```text
parsed.jsonl (canonical)
    -> analyze tokens     -> token_stats.json
    -> analyze metrics    -> metrics.json
    -> analyze stats      -> aggregation / exploration
    -> analyze datasheet  -> report layer (Markdown or JSON)
```

In this model:

- `parsed.jsonl` is canonical
- Layer 1 outputs are deterministic and rebuildable
- Layer 2 adds an optional deterministic SQLite index
- Layer 3 and Layer 4 outputs, when added, remain separate higher-layer artifacts
- higher layers do not replace canonical data or deterministic sidecars/views

Recommended workflow when you want the full analyze stack:

1. parse the provider export into canonical `parsed.jsonl`
2. run `analyze tokens` to write `token_stats.json`
3. run `analyze metrics` to write `metrics.json`
4. use `analyze stats` for exploratory aggregation
5. use `analyze datasheet` for appendix-ready reporting

`analyze stats`, `analyze datasheet`, and `analyze timeline` can run directly on
canonical `parsed.jsonl`. They do not require sidecars.

Data source policy:

- canonical correctness remains anchored to `parsed.jsonl`
- `analyze metrics` requires sibling `token_stats.json`
- `analyze stats` computes from canonical `parsed.jsonl`
- `analyze datasheet` may opportunistically reuse existing `thread_stats.json`
  and `metrics.json` sidecar artifacts when present
- when those sidecars are missing or unusable, `analyze datasheet` falls back
  deterministically to canonical `parsed.jsonl`
- `analyze stats` research-oriented safety aggregates may also reuse
  `metrics.json` when present, but still work without it

CLI consistency note:

- `analyze stats` and `analyze timeline` are presentation commands: they render terminal output, support `--json`, and can write the rendered result via `--out`
- `analyze datasheet` is also a presentation command: it renders Markdown by
  default, supports `--json`, and can write the rendered result via `--out`
- `analyze tokens` and `analyze metrics` are sidecar builders: they write per-thread JSON artifacts next to each `parsed.jsonl` and use `--skip-existing` instead of presentation flags
- `analyze sqlite-build` writes a single `analysis.db` index artifact and uses `--overwrite` for rebuild control
- future L3/L4 commands should remain separate from deterministic L1/L2 builders and presentation commands

## When To Use Which Command

Use:

- `analyze stats` for aggregation and exploratory summaries
- `analyze metrics` for deterministic per-thread `metrics.json`
- `analyze datasheet` for concise appendix-ready Markdown or JSON
- `analyze timeline` for time-bucketed activity summaries
- `analyze sqlite-build` for an optional SQLite index

Config boundary note:

- `analyze` is centered on explicit canonical inputs and deterministic artifact or view generation
- it shares the project-wide locale resolution path with the other runtime commands
- broader profile-backed command defaults are not fully applied across `analyze` subcommands the same way they are for `parse`, `export`, `chain`, and `extract` today
- this is a current design boundary, not a statement that analyze results depend on profile defaults for correctness

Incremental sidecar policy:

- default behavior: existing `token_stats.json` and `metrics.json` sidecars are rebuilt and overwritten
- `--skip-existing`: leave an existing sidecar untouched and only build missing sidecars
- `--dry-run`: preview detected threads plus planned create/rebuild/skip counts without writing files
- `analyze metrics --skip-existing` still requires pre-existing `token_stats.json` for any thread whose `metrics.json` is missing

The refusal and revision phrase lists are locale-backed and loaded from
`src/llm_logparser/i18n/{locale}.yaml`, with fallback to `en-US` when a key is missing.

The metrics heuristics are deterministic and local:

- refusal detection: normalized substring match on assistant messages only
- safety intervention detection: normalized substring match on assistant messages using locale YAML caveat indicators
- revision detection: normalized cue match or similarity match between consecutive user messages
- short user messages are ignored for revision counting to reduce false positives
- revision subtypes use cue precedence: correction, then clarification, then generic retry
- `safety.refusal`: existing refusal-only heuristic and rates over assistant messages
- `safety.intervention_count`: broader message-based count of assistant messages matching refusal or caveat indicators
- `safety.trigger_types`: subtype totals for `refusal` and `caveat`; one message may increment both subtype counters while `intervention_count` increments once
- `user_effort.rapid_revisions`: count assistant → next user transitions under `60` seconds
- `user_effort.response_length_ratio`: `total_assistant_characters / total_user_characters`, or `null` when user characters are `0`
- `user_effort.negative_deltas`: count assistant → next user transitions whose timestamp delta is negative
- `user_effort.human_read_time`: assistant → next user deltas, excluding gaps over `3600` seconds from summary stats, excluding negative deltas entirely, and counting long gaps separately as `excluded_long_gaps`
- phrase lists come from locale YAML resources and remain auditable/editable on disk

Analyzer i18n is intentionally narrow:

- locale-backed YAML resources only affect heuristic inputs such as refusal and revision cues
- the human-readable text renderers for `analyze stats`, `analyze datasheet`,
  and `analyze timeline` are intentionally English-only
- structured JSON output and schema keys remain English for tooling stability

This is a best-effort design boundary, not a missing translation pass.

Typical metrics include:

- message counts
- character counts
- token counts
- role distribution
- thread length
- conversation duration
- inter-message timing
- refusal heuristics
- revision heuristics
- research-oriented `analyze stats` aggregates for pacing, turn-taking, safety,
  and lightweight message structure
- appendix-ready `analyze datasheet` summaries built from the same deterministic
  research-oriented aggregates

Example outputs:

```
messages: 42
user_messages: 21
assistant_messages: 21
characters_total: 18345
conversation_duration: 2h14m
```

The additive `analyze stats` research summary is intentionally lightweight:

- temporal aggregates use canonical thread timestamp spans and exclude threads
  without valid timestamps from duration summaries
- turn-taking aggregates summarize per-thread `characters_user / characters_assistant`
  and exclude zero assistant denominators
- safety aggregates count threads with refusal or intervention signals, reusing
  existing `metrics.json` when present but still working without it
- structural aggregates use simple local heuristics such as `content.parts`
  length and fenced code block markers; they are not a Markdown or multimodal parser

`analyze datasheet` builds on those same deterministic concepts, but presents
them as a stable report layer:

- Markdown by default, JSON with `--json`
- appendix-ready section structure
- opportunistic sidecar artifact reuse
- canonical `parsed.jsonl` fallback when sidecars are missing

This layer should remain:

- deterministic
- fast
- dependency-light

Tokenizer caveat:

- `tiktoken` may perform a one-time network fetch on first use to download
  encoding assets
- later runs use the local cache

---

# Layer 2 — Indexed Analysis (SQLite)

Some analyses require efficient querying across many conversations.

For these cases, the analyzer can optionally load conversation data
into a local SQLite database.

The database acts as an **analysis index**, not as the canonical storage.

Canonical data remains `parsed.jsonl`.

Layer 2 is therefore:

- optional
- deterministic and rebuildable
- derived from canonical or deterministic lower-layer artifacts
- safe to delete and rebuild
- isolated from users who do not need indexed querying

It does not replace `parsed.jsonl`, `token_stats.json`, `metrics.json`, or the
deterministic view/report commands. It accelerates query workloads on top of
them.

SQLite enables:

- cross-thread searches
- date range queries
- role-based filtering
- provider statistics
- aggregation across conversations
- fast exploratory queries

Example queries:

```
messages per day
average conversation length
assistant/user ratio
top threads by size
```

`analyze sqlite-build` writes `analysis.db` as a separate artifact. If the
index is absent, canonical and deterministic analyzer workflows still work.

---

# Layer 3 — Semantic Topic Tracking

This layer introduces **semantic topic tracking** across canonical messages and
derived lower-layer artifacts.

Its purpose is to recover continuity of work even when related discussion is:

- fragmented across multiple threads
- revisited over time
- mixed with unrelated topics in the same thread
- weakly represented by provider-native thread structure

Typical tasks include:

- grouping semantically related messages across threads
- tracking topic lifecycle over time
- surfacing decisions made
- identifying open questions
- extracting next actions
- generating topic-level summaries or timelines

Typical implementation building blocks may include:

- local embedding models
- sentence-transformer-style similarity pipelines
- graph-based clustering
- `DBSCAN` or `HDBSCAN`
- optional local summarization backends

Because this layer can be implemented with local embeddings or local models, it
remains compatible with privacy-sensitive and offline-first workflows.

Layer 3 remains an optional higher layer. Its outputs are additive and may be
model-derived or non-deterministic. They must not replace canonical
`parsed.jsonl`, deterministic Layer 1 artifacts, or the optional deterministic
Layer 2 SQLite index.

Future local GUI-oriented summaries, annotations, or caches should follow the
same rule: additive artifacts on top of the deterministic base, not
redefinitions of it.

See [Semantic Topic Tracking (L3)](semantic-topic-tracking.md) for the
layer-specific design.

---

# Layer 4 — Frontier LLM Analysis (API)

The highest layer allows the analyzer to call external LLM APIs.

This layer is intentionally flexible.

It may support:

- freeform prompts
- predefined analysis templates
- deep summarization
- comparative analysis across threads
- writing assistance
- research-style analysis

Example usage:

```
analyze llm --prompt "What patterns exist in these conversations?"
```

or

```
analyze llm --template architecture_review
```

Because this layer depends on external APIs, it is optional and
disabled by default in privacy-sensitive environments.

Layer 4 is also an additive higher layer. Outputs from external/API models may
be useful, but they are not canonical and may be non-deterministic. They must
remain opt-in and isolated from deterministic artifacts and from users who do
not use them.

---

# Future Higher-Layer Artifact Boundaries

Future higher-layer artifacts should remain clearly separated from the
deterministic L1/L2 base.

Intended future artifact classes:

- L3 artifacts: semantic topic tracking outputs such as topic clusters,
  timelines, summaries, embeddings, or other local semantic artifacts
- L4 artifacts: external/API-derived outputs
- GUI-oriented caches or indexes: display-optimization or UI-support artifacts,
  not canonical state

These are future additive layers on top of canonical and deterministic
artifacts. They are not canonical, are not deterministic by default, and
should not be mixed into the deterministic artifact layer by default.

Minimum provenance metadata expectation:

Future model-derived artifacts should carry enough provenance metadata to
explain how they were produced. The exact field names are not yet frozen, but
the metadata should cover at least:

- `producer_layer`
- `model`
- `provider`
- `prompt_template` or `template_id`
- `created_at`
- `source_inputs`
- `schema_version`
- `reproducibility_note`

This is not a final schema contract. It is a design rule that future
model-derived outputs must remain attributable, auditable, and clearly separate
from deterministic artifacts.

Intended layout guidance:

The layout below is guidance for isolation, not a locked implementation
contract:

```text
thread-<conversation_id>/
    parsed.jsonl
    token_stats.json
    metrics.json

<provider>/
    analysis.db
    l3/
      semantic-topics/  # current cross-thread semantic topic artifacts
    l4/   # future API-derived outputs
    gui/  # future GUI-oriented cache or index artifacts
```

In this model:

- the thread directory root remains for deterministic artifacts
- provider-root `l3/semantic-topics/` is the current place for formal
  cross-thread semantic topic artifacts
- `l4/` is the intended place for future API-derived outputs
- provider-root `gui/` is the intended place for GUI-specific cache/index data
- `analysis.db` remains Layer 2, not a catch-all store for future higher layers
- users who stay on the deterministic path should not be affected by whether
  these higher-layer artifacts ever exist

---

# CLI Concept

The analyzer is exposed via the `analyze` command.

Current implemented CLI modes:

```
llm-logparser analyze stats ...
llm-logparser analyze timeline ...
llm-logparser analyze tokens ...
llm-logparser analyze metrics ...
llm-logparser analyze sqlite-build ...
llm-logparser analyze semantic-prototype ...
llm-logparser analyze semantic-preview ...
llm-logparser analyze semantic-topics ...
llm-logparser analyze semantic-topic ...
llm-logparser analyze semantic-topic-explore ...
```

Conceptual future modes:

```
llm-logparser analyze topic-summary ...
llm-logparser analyze topic-timeline ...
llm-logparser analyze llm ...
```

Possible modes:

| Mode | Layer |
|-----|------|
| stats | L1 |
| timeline | L1 |
| tokens | L1 |
| metrics | L1 |
| sqlite-build | L2 |
| semantic-prototype | L3 prototype (experimental) |
| semantic-preview | L3 prototype viewer (experimental) |
| semantic-topics | L3 topic artifact builder (experimental) |
| semantic-topic | L3/L4 topic renderer (experimental) |
| semantic-topic-explore | L3/L4 topic navigation UX (experimental) |
| topic-summary | L3 (conceptual / future) |
| topic-timeline | L3 (conceptual / future) |
| llm | L4 (conceptual / future) |

Command boundary rule:

- L1 commands operate on canonical data and produce deterministic views or sidecars
- L2 commands build optional deterministic index artifacts
- L3/L4 commands, when implemented, should remain explicitly higher-layer and opt-in

## CLI Design Direction (v1.4)

The `analyze` CLI is designed to preserve a clear separation between
deterministic and model-derived capabilities.

- L1/L2 commands (`stats`, `timeline`, `tokens`, `metrics`, `sqlite-build`)
  are deterministic, rebuildable, and safe for local-first workflows

- `semantic-prototype` is an experimental bridge into future L3 work: it
  reads deterministic `message_windows.jsonl`, writes rebuildable embedding,
  neighbor, and minimal cluster artifacts, and does not perform topic labeling
  Window generation quality can now be improved upstream with deterministic
  sliding windows: `message_windows.jsonl` still defaults to
  non-overlapping windows, but parse/chain can opt into overlap by setting
  message-window stride smaller than window size.
  It supports a default `deterministic-hash` backend for local plumbing/tests
  and an `ollama` backend for real local embeddings via a local Ollama model.
  For Ollama-backed runs, oversized window text is chunked automatically with a
  deterministic UTF-8 byte budget and chunk embeddings are aggregated back into
  one final embedding per window.
  Neighbor construction supports `--min-score` thresholding so weak links are
  not emitted unconditionally.
  When `--sqlite-db` is provided, candidate generation uses L2
  `analysis.db` filters (`ts_start`, candidate window size, thread-level
  assistant ratio, and same-thread policy) to build local candidate pools
  before cosine scoring.
  Similarity inside those narrowed pools is now evaluated symmetrically via
  pool-local all-pairs comparison, which improves reciprocity opportunities for
  the existing mutual-only clustering rule without reintroducing corpus-wide
  dense comparison.
  Long-running phases emit lightweight progress logs while windows load,
  embeddings generate, neighbors build, clusters build, and artifacts are
  written.
  Cluster construction is intentionally minimal: it converts retained mutual
  neighbor links into undirected edges, suppresses same-thread mutual edges
  when the paired windows share more than one source message, applies a
  stricter cross-thread gate at the runtime P75 of cross-thread mutual scores,
  and writes connected-component membership to `window_clusters.jsonl`. The
  same-thread overlap cap and the cross-thread P75 rule were both selected
  from the real artifact corpus: the first reduced sliding-window chaining
  without the extra fragmentation of a stricter zero-overlap rule, and the
  second reduced broad cross-thread components while retaining more mutual
  structure than stricter cross-thread thresholds. If older callers provide
  neighbor rows without usable scores, the cross-thread gate falls back to the
  legacy mutual-only behavior for those edges instead of failing.
  Backend/model selection is config- or CLI-owned: `backend` selects the
  runtime binding, `model` selects the embedding model identifier, and
  embedding chunking settings can be declared explicitly in config. Code keeps
  conservative fallback settings and a small compatibility shim for older
  Ollama model IDs, but model recommendations are documented rather than being
  source-of-truth Python presets.

- `semantic-preview` is a read-only companion to `semantic-prototype`: it
  reads stored `message_windows.jsonl`, `window_clusters.jsonl`, and optional
  `window_neighbors.jsonl` and provides three inspection modes without
  recomputing embeddings or modifying artifacts:
  cluster list view by default, cluster detail via `--cluster-id`, and
  conversation-centric lookup via `--conversation-id`. The older
  conversation-plus-window neighbor preview remains available when
  `--conversation-id` and `--window` are supplied together. `--json` switches
  any of those modes to machine-readable output for downstream tooling.

- `semantic-topic` is an experimental L4 read-only layer on top of stored L3
  cluster artifacts: it reads `message_windows.jsonl` plus
  `window_clusters.jsonl`, selects representative windows per cluster, and
  sends them to a local Ollama generation model for a short label, summary,
  and keywords. Production keeps the prompt/template fixed from the repository
  prompt-selection experiment under `./tmp`: Prompt B, an 8-window cap per
  cluster, and 300-character normalized window excerpts. Representative
  windows are chosen deterministically by preferring stronger retained
  intra-cluster connectedness first, then larger message/character footprints
  as lightweight tie-breaks. It does not write any new artifacts and does not
  alter L3 clustering.

- `semantic-topics` is the formal L3/L4 boundary artifact builder: it reads
  stored `message_windows.jsonl` plus `window_clusters.jsonl` and writes
  provider-scoped artifacts under `l3/semantic-topics/`:
  `topics.json` as the forward topic index and `topic_membership.jsonl` as the
  reverse lookup index. Current membership is intentionally conservative:
  one topic per L3 cluster (`cluster-is-topic-v1`). Structural fields are
  always written; model-derived fields are added only when `--model` is
  supplied. `topics.json` now emits `schema_version: "1.0"` with top-level
  `generated_at`, `source_inputs`, and `provenance`. Per-topic records keep
  `cluster_ids` as the primary anchor, derive `window_refs` and `message_refs`
  from cluster membership, and include `state`, which is currently emitted as
  `null` for every topic. Provenance is execution-oriented: structural-only
  runs keep `labeling_model`, `prompt_variant`, and `prompt_hash` as `null`,
  while model-enriched runs populate them from the actually used local labeling
  prompt. Topic records may also include additive `quality_signals` derived
  from cluster size, conversation count, and retained intra-cluster neighbor
  scores when available. Representative windows in `topics.json` use the same deterministic
  intra-cluster connectedness-first ranking as `semantic-topic`.

- `semantic-topic-explore` is the read-only UX layer on top of those artifacts:
  it reads `topics.json`, `topic_membership.jsonl`, and `message_windows.jsonl`
  and builds in-memory indexes for:
  `topic -> members`, `message -> topic`, and `conversation -> topics`.
  It supports a default topic list, topic detail with timeline, reverse lookup
  from `message_id`, and conversation-centric topic grouping.

Current limitations remain explicit:

- semantic clusters are not canonical topics
- topic membership is currently `1 topic = 1 L3 cluster`; no cross-cluster
  merge logic is applied yet
- no lifecycle state is inferred
- no cross-cluster topic reasoning is performed

Key `semantic-prototype` flags:

- `--min-score`: filters out neighbors whose cosine similarity is below the
  threshold; `top_k` still applies after filtering. The current default is
  `0.62`, promoted from repeated real-data subset validation as the best
  current tradeoff between broad cross-thread noise and extra fragmentation.
- `--sqlite-db`: enables SQLite-assisted candidate generation using
  `analysis.db`; omitting it preserves the full embedded-window fallback path
- `--candidate-window-days`: bounds candidate retrieval by `ts_start` around
  each target window
- `--candidate-min-chars`: excludes short candidate windows before scoring
- `--candidate-min-assistant-ratio`: excludes source threads whose
  `assistant_messages / message_count` ratio is below the threshold
- `--candidate-same-thread`: controls whether same-thread candidates are
  allowed, preferred on tie-breaks, restricted to only same-thread windows, or
  excluded

Key `semantic-preview` flags:

- `--top-clusters`: limits the default cluster-list view
- `--min-cluster-size`: filters small clusters out of list and conversation
  views
- `--cross-thread-only`: hides single-conversation clusters in list and
  conversation views
- `--cluster-id`: switches to detailed inspection for one cluster
- `--conversation-id`: switches to conversation-centric inspection; when paired
  with `--window`, switches to the older single-window neighbor preview
- `--json`: emits structured machine-readable output instead of pretty text

Key `semantic-topic` flags:

- `--model`: required local Ollama generation model
- `--cluster-id`: generate a topic for one cluster only
- `--top-clusters`: limit the number of clusters processed when not targeting a
  single cluster
- `--min-cluster-size`: skip very small clusters before prompting the local
  model
- `--cross-thread-only`: limit topic generation to clusters spanning multiple
  conversations
- `--json`: emit structured machine-readable output instead of pretty text

Key `semantic-topics` flags:

- `--model`: optional local Ollama generation model; omit it to write
  structural-only artifacts
- `--cluster-id`: build artifacts for one cluster only
- `--min-cluster-size`: skip very small clusters before artifact generation
- `--cross-thread-only`: limit artifact generation to multi-conversation
  clusters
- `--base-url` / `--timeout-seconds`: control the local Ollama endpoint when
  `--model` is used

Key `semantic-topic-explore` flags:

- `--topic-id`: show one topic in detail, including a chronological timeline
  over its windows
- `--message-id`: reverse lookup one message into one or more topics
- `--conversation-id`: show all topics that appear in one conversation
- `--json`: emit structured machine-readable output instead of pretty text

Parse-time windowing controls:

- `parse.message_windows.size` / `chain.message_windows.size`: number of
  canonical messages per emitted message window
- `parse.message_windows.stride` / `chain.message_windows.stride`: advance step
  between emitted windows; omitting it preserves the legacy non-overlapping
  behavior by reusing the window size

- broader lifecycle-oriented follow-ups (`topic-summary`, `topic-timeline`,
  `llm`) remain conceptual future extensions beyond the current
  `semantic-topics` artifact builder and `semantic-topic` renderer

Design constraints:

- deterministic commands must not depend on model-derived layers
- model-derived commands must not alter or redefine deterministic artifacts
- command naming should reflect layer boundaries and user expectations

This ensures that future expansion of the CLI does not break:

- reproducibility
- local-only usage
- existing user workflows

The CLI structure is intentionally layered to align with the analyzer’s
"canonical-first + additive layers" philosophy.

---

# Relationship to Parser

The analyzer **never parses provider exports directly**.

Its input is always the normalized JSONL format produced by the parser.

```
provider export
        ↓
parser
        ↓
canonical JSONL
        ↓
analyzer
```

This separation keeps responsibilities clean:

| Component | Responsibility |
|----------|----------------|
| parser | normalization |
| exporter | presentation |
| analyzer | insight |

---

# Future Directions

Potential future expansions include:

- cost estimation
- conversation similarity search
- semantic indexing
- embeddings-based analysis
- dataset generation
- research tooling

These features will remain layered on top of the canonical JSONL model.
Any future GUI-oriented data products should follow the same additive model:
they may consume canonical or deterministic artifacts, but they must not
replace them.

---

# Summary

The analyzer transforms normalized conversation logs into
useful insights through a progressive analysis stack:

```
parsed.jsonl → deterministic analysis → optional index → optional local AI → optional frontier AI
```

This design preserves the core philosophy of the project:

- deterministic where possible
- powerful where needed
- always built on canonical normalized data
- higher layers remain additive and opt-in
- users who do not use higher layers remain on the same deterministic foundation
