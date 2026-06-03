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

Current L3 note:

- Phase 0 semantic normalization now exists as a representative-span helper
  signal in semantic topic inspection flows
- it remains non-canonical, non-deterministic, and ephemeral in this phase
- it does not create a new persisted artifact or alter canonical/L1/L2 contracts
- local model choice for this helper remains runtime-specific; any model names
  mentioned in tests or exploratory notes are examples, not required settings

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
L1 does not define semantic meaning: it consumes canonical normalized fields
only and produces deterministic substrates, sidecars, and views.

## Role Boundary Rule

- L1 MUST NOT depend on provider-specific role labels
- canonical normalized roles are the only allowed semantic role representation in L1
- raw roles are strictly non-semantic and non-shared; they may appear only in
  explicit pass-through or display-oriented paths outside L1

Current Layer 1 implementations include:

- `analyze stats`
- `analyze datasheet`
- `analyze timeline`
- `analyze tokens`
- `analyze metrics`
- `analyze token-dictionary`
- human-readable stats/timeline views and machine-readable sidecars

`analyze tokens` writes deterministic `token_stats.json` sidecars from canonical
`parsed.jsonl` using `tiktoken`.

`analyze metrics` writes deterministic `metrics.json` sidecars from
`parsed.jsonl` plus `token_stats.json`, including:

- ratio / token / character / distribution / diversity metrics
- heuristic `safety.refusal`
- heuristic `interaction.revision`
- additive `user_effort` metrics derived from assistant → user timing and text length

`analyze token-dictionary` writes additive L3 auxiliary artifacts under
`l3/token-dictionary/` from canonical `parsed.jsonl` plus optional
`token_stats.json` and `l3/semantic-topics/topics.json`. The primary output is
now `observed_tokens.json`, an observed token index / corpus token statistics
artifact. Legacy `dictionary.json` files remain readable as a backward-compatible
alias. `bundles.json` records corpus-derived cooccurrence bundles. These
token/bundle artifacts are rebuildable higher-layer facts only; they do not
replace canonical or L1 outputs. Seeded `lexical_rules.json` remains a generated
legacy policy-like sidecar for task / reflective / specificity token groups, not
reviewed policy; its built-in seed groups are packaged resource-backed defaults,
not Python-owned reviewed policy. Cross-thread
generic admission anchors and topic-summary scoring token policy are lexical
policy, not corpus token facts, and are owned by the cross-thread lexical
resources instead of token dictionary artifacts. Python fallback lists for these
policies are intentionally minimal compatibility shims. Explicit reviewed project/user
lexical rule files may be provided to `cross-thread-candidates` as additive
layers above the built-in resources; there is no automatic discovery or
promotion. L3 cross-thread candidate selection may also consume
`observed_tokens.json` / legacy `dictionary.json` and `bundles.json` when
present as additive evidence only; missing token-dictionary artifacts fall back
to the pre-existing candidate path. `analyze lexical-rule-candidates` reads
`observed_tokens.json` as observed token
statistics and writes inactive L3 suggestions under `l3/lexical-rules/`; it does
not modify reviewed lexical rule files or promote candidates automatically.

Read-only lexical policy operations are also available outside `analyze`:

```bash
llm-logparser lexical observed list --input artifacts/openai
llm-logparser lexical observed inspect --input artifacts/openai --token "DALL-E"

llm-logparser lexical candidates list --input artifacts/openai
llm-logparser lexical candidates inspect --input artifacts/openai --candidate-id candidate_xxx

llm-logparser lexical policy validate \
  --project-lexical-rules project_lexical_rules.yaml

llm-logparser lexical policy resolve \
  --locale en-US \
  --project-lexical-rules project_lexical_rules.yaml \
  --json
```

These commands are read-only. Observed-token commands show corpus facts from
`observed_tokens.json`, `bundles.json`, and provenance metadata. Candidate
commands show inactive `candidates.jsonl` suggestions and diagnostics. Policy
commands validate explicitly reviewed project/user YAML and resolve the active
built-in + reviewed policy layers into compact diagnostics. They do not write
reviewed rules, promote candidates, classify observed tokens, or change analyzer
behavior.

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
- shared L1 artifacts must use canonical normalized fields rather than raw
  provider-specific labels or structures

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

The refusal, intervention, and revision phrase lists used by deterministic
machine analysis are fixed analyzer rule sets defined in code.

The metrics heuristics are deterministic and local:

- refusal detection: normalized substring match on assistant messages only
- safety intervention detection: normalized substring match on assistant messages using the fixed machine intervention cue set
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
- machine phrase lists are locale-independent so `metrics.json`, `analyze stats --json`, and `analyze datasheet --json` remain identical across locales

Analyzer i18n is intentionally narrow:

- locale affects presentation only: CLI/help/runtime strings, logs, and human-facing renderers
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
  existing locale-independent `metrics.json` when present but still working without it
- structural aggregates use canonical top-level `text` and simple fenced code
  block markers; they are not a Markdown or multimodal parser

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
- FTS-backed canonical message recall through `analyze recall`
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
`analyze recall` is a read-only query path over an existing `analysis.db`; it
does not write query artifacts and reports canonical provider/conversation/message
identity fields rather than SQLite row IDs.

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
llm-logparser analyze recall ...
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
| recall | L2 read-only FTS query |
| semantic-prototype | L3 prototype (experimental) |
| intra-thread-topics | L3 intra-thread segmentation prototype (experimental) |
| semantic-preview | L3 prototype viewer (experimental) |
| semantic-span-proposals | L3 experimental span-derivation sidecar |
| cross-thread-candidates | L3 experimental cross-thread continuity sidecar |
| token-dictionary | L3 auxiliary token/bundle signal sidecar |
| lexical-rule-candidates | L3 inactive lexical-rule candidate diagnostics |
| cross-thread-intent-eval | L4 experimental same-intent evaluation sidecar |
| cross-thread-memory-recall | L4 read-only memory recall presentation layer |
| semantic-normalization | L3 sidecar batch job runner |
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

- L1/L2 commands (`stats`, `timeline`, `tokens`, `metrics`, `sqlite-build`,
  `recall`) stay deterministic and safe for local-first workflows; builder
  commands write rebuildable artifacts, while `recall` is read-only over an
  existing L2 index

- `semantic-prototype` is an experimental bridge into future L3 work: it
  reads deterministic candidate spans from either stored `message_windows.jsonl`
  or canonical `parsed.jsonl`, writes rebuildable embedding, neighbor, and
  minimal cluster artifacts, and does not perform topic labeling
  `message_windows.jsonl` is an L1 deterministic segmentation substrate, not a
  semantic unit. In the current contract, window rows are message-ids-first
  candidate spans containing only deterministic membership and provenance
  fields. When semantic text is needed, L3 reconstructs it from canonical
  `parsed.jsonl` messages using the ordered `message_ids`; no semantic meaning
  is stored in L1 convenience fields.
  `parsed.jsonl` is now a first-class structural input path for the prototype:
  when supplied directly, L3 derives the same default deterministic windows
  from canonical messages without requiring a pre-existing
  `message_windows.jsonl`. For directory inputs, the prototype prefers stored
  `message_windows.jsonl` per thread when present and falls back to sibling
  `parsed.jsonl` otherwise.
  Window generation quality can now be improved upstream with deterministic
  sliding windows: `message_windows.jsonl` still defaults to
  non-overlapping windows, but parse/chain can opt into overlap by setting
  message-window stride smaller than window size.
  It supports a default `deterministic-hash` backend for local plumbing/tests
  and an `ollama` backend for real local embeddings via a local Ollama model.
  All Ollama-backed semantic flows now route through
  `llm_logparser.core.ollama_client.OllamaClient`, replacing duplicated local
  request helpers with a single stdlib-only I/O boundary and consistent error
  handling. Higher-layer code is now also shaped around a small client
  protocol, so the current `OllamaClient` remains the concrete implementation
  without becoming the permanent type-level dependency. It remains isolated
  from canonical parsing and deterministic L1/L2 processing. This is a current
  implementation choice for the present local runtime path, not a permanent
  requirement that Ollama be the only supported runtime. The architectural
  intent is to keep transport concerns centralized now so broader local-runtime
  abstraction can remain possible later, including future backends such as
  `llama.cpp`; users should not be forced onto Ollama as the only long-term
  local inference option. Structured local-LLM JSON response handling is now
  also isolated behind a dedicated L3 helper so semantic flows do not need to
  own ad hoc structured-response parsing logic.
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
  recomputing embeddings or modifying artifacts. Preview text is reconstructed
  from canonical parsed messages keyed by `message_ids`, not read from L1 as a
  semantic source of truth. Current preview formatting operates over explicit
  reconstructed message sequences rather than inferring turns by splitting a
  stored window text projection:
  cluster list view by default, cluster detail via `--cluster-id`, and
  conversation-centric lookup via `--conversation-id`. The older
  conversation-plus-window neighbor preview remains available when
  `--conversation-id` and `--window` are supplied together. `--json` switches
  any of those modes to machine-readable output for downstream tooling.

- `intra-thread-topics` is an experimental thread-local L3 builder for Phase 3
  segmentation. It reads canonical `parsed.jsonl`, reconstructs deterministic
  message order, builds overlapping sliding windows, embeds those windows with
  the existing embedding backend stack, scores adjacent-window continuity from
  embedding cosine similarity plus small deterministic lexical and structural
  continuity signals, and emits contiguous segment sidecars under
  `l3/intra-thread-topics/`:
  `boundaries.jsonl` and `segments.jsonl`. Boundary rows include
  `lexical_similarity`, `structural_continuity`, and `continuity_score`; the
  structural signal is limited to local role/text handoff patterns such as
  user request to assistant answer and empty assistant/tool handoffs. The
  current implementation remains intentionally narrow: fixed threshold only,
  contiguous splits only, and no topic labels, summaries, topic-unit merge
  logic, or cross-thread merge logic. `--report` is a read-only inspection
  mode for existing intra-thread artifacts: it reconstructs canonical message
  previews from sibling `parsed.jsonl` and writes
  `l3/intra-thread-topics/report.md` without recomputing embeddings.

- `intra-thread-topic-summaries` is a deterministic L3 sidecar builder on top
  of existing intra-thread `segments.jsonl`. It reads canonical `parsed.jsonl`,
  reconstructs each segment by `message_ids`, verifies the stored segment
  `text_sha1`, and writes `l3/intra-thread-topics/topic-summaries.jsonl`.
  Default output is heuristic (`source: heuristic`): conservative extracted
  title, truncated normalized excerpt summary, keywords, `conclusion_text: null`,
  and usually `conclusion_status: unknown`. Existing `topic-summaries.jsonl`
  artifacts are skipped unless `--overwrite` is supplied, making provider-root
  runs resumable. `--jobs` adds bounded thread-level parallelism; use
  conservative values such as `--jobs 1` or `--jobs 2` for local Ollama because
  higher values may overload consumer hardware. `--source local-llm` optionally
  uses local Ollama generation. The default tested model is
  `gemma4-Q8_K_XL:latest`; `mistral-nemo:latest` is a viable fallback
  candidate. Current local evaluation does not recommend `gpt-oss-20b`,
  `lfm-instruct`, or `lfm-thinking` for this artifact, but they are not blocked:
  users may pass any local model explicitly with `--model`. Model quality may
  vary, and prompt profiles may be added later. Local rows include `model`,
  `prompt_variant`, and `prompt_hash` provenance. If one segment fails local
  generation or strict output validation, only that segment falls back to the
  heuristic row. Full local LLM generation over large datasets can be slow; the
  default heuristic path remains the lightweight option. Conclusions remain
  provisional. This is input for later cross-thread matching, not final topic
  determination.

- `semantic-topic` is an experimental L4 read-only layer on top of stored L3
  cluster artifacts: it reads `message_windows.jsonl` plus
  `window_clusters.jsonl`, selects representative windows per cluster, and
  reconstructs canonical window text from `parsed.jsonl` before sending
  representative spans to a local Ollama generation model for a short label,
  summary, and keywords. Production keeps the prompt/template fixed from the repository
  prompt-selection experiment under `./tmp`: Prompt B, an 8-window cap per
  cluster, and 300-character normalized window excerpts. Representative
  windows are chosen deterministically by preferring stronger retained
  intra-cluster connectedness first, then larger message/character footprints
  as lightweight tie-breaks. Prompt inputs and representative excerpts are L3
  reconstructions from canonical messages, while `window_id` remains only a
  compatibility/display overlay. It does not write any new artifacts and does
  not alter L3 clustering.

- `semantic-topics` is the formal L3/L4 boundary artifact builder: it reads
  stored `message_windows.jsonl` plus `window_clusters.jsonl` and writes
  provider-scoped artifacts under `l3/semantic-topics/`:
  `topics.json` as the forward topic index and `topic_membership.jsonl` as the
  reverse lookup index. Current membership is intentionally conservative:
  one topic per L3 cluster. Structural fields are always written;
  model-derived fields are added only when `--model` is supplied.
  `topics.json` now emits `schema_version: "2.2"` and
  `topic_membership.jsonl` now emits `schema_version: "1.0"`, with top-level
  `generated_at`, `source_inputs`, and `provenance`. Per-topic records are now
  grounded primarily by `span_refs`, `message_refs`, and
  `representative_spans`; `window_refs` and `representative_windows` remain as
  compatibility/presentation overlays only. `topic_membership.jsonl` now uses
  `membership_type=cluster|span|message`, so semantic membership is no longer
  inferred from L1 window text; semantic excerpts are reconstructed from
  canonical message rows via `message_ids` when needed for prompting or browse
  output. `semantic-topics` also accepts `--normalization-job <job_id>` to
  attach precomputed representative-span semantic normalization from
  `l3/semantic-normalization/jobs/<job_id>/results.jsonl` as an optional L3
  sidecar, with drift checks against current reconstructed text.
  window-based. Topic records still include `cluster_ids` for provenance and
  now include heuristic L3 state fields:
  `state` with canonical values `unresolved|in_progress|done`,
  topic-level `state_confidence`, and per-span `state`, `state_confidence`,
  and `state_signals` on `span_refs` / `representative_spans`.
  The current MVP path is deterministic and rule-based:
  it uses reconstructed span messages, tail-priority conflict resolution, and
  recency only as a modifier. Phrase resources are now loaded from
  locale-specific L3 data files, with explicit `state_locale` selection for
  `semantic-topic` / `semantic-topics` and `en-US` as the fallback. It does
  not use a model-enriched state classifier.
  Structural-only topic labeling is now also deterministic: when `--model` is
  omitted, `semantic-topics` fills `label` with a heuristic phrase derived from
  representative topic text while leaving `summary` empty. Labels remain
  additive convenience metadata and are separate from topic `state`.
  Provenance is execution-oriented: structural-only
  runs keep `labeling_model`, `prompt_variant`, and `prompt_hash` as `null`,
  while model-enriched runs populate them from the actually used local labeling
  prompt. Topic records may also include additive `quality_signals` derived
  from cluster size, conversation count, and retained intra-cluster neighbor
  scores when available. Representative windows in `topics.json` use the same deterministic
  intra-cluster connectedness-first ranking as `semantic-topic`.
  This Step 5 contract change is intentionally breaking: older
  window-centric `topics.json` / `topic_membership.jsonl` artifacts should be
  regenerated from canonical inputs under the current pipeline rather than
  reused through compatibility fallbacks.

  ### Semantic Normalization Join Contract

  `semantic-topics` may optionally attach batch semantic normalization to
  `representative_spans` via `--normalization-job <job_id>`. That bridge uses a
  strict span-oriented join contract.

  `semantic-topics` may also opt in to representative-span refinement from the
  experimental `semantic-span-proposals` sidecar via
  `--refine-representative-spans-from-proposals`. This consults
  `l3/semantic-span-proposals/span_proposals.jsonl` and may replace only the
  `representative_spans` surface when one proposal-backed replacement is
  unambiguous and text-consistent. It does not change cluster membership,
  `topic_id`, `topic_membership.jsonl`, or the current clustering substrate.

  Span identity:

  - `span_id` is the primary join key
  - `span_id` is derived from ordered `message_ids`
  - `window_id` is not semantic identity; it remains a compatibility/display
    overlay and a deterministic fallback input only when ordered `message_ids`
    are unavailable in older or partial inputs
  - changing ordered `message_ids` or their order changes `span_id`

  Text hash contract:

  - `text_sha1` is computed from reconstructed full span text, not from
    truncated excerpts
  - the text source is the canonical ordered message text sequence for the
    span, joined with `"\n\n"` between non-empty message texts
  - producer and consumer must use the same reconstruction contract and the
    same UTF-8 SHA-1 hash bytes
  - `text_sha1` mismatch is treated as input drift, not as a hard job failure

  Join semantics:

  - `span_id` match plus `text_sha1` match: attach `semantic_normalization`
  - `span_id` match plus `text_sha1` mismatch: warn and skip attachment
  - no `span_id` match: leave the representative span unannotated
  - no silent fallback, fuzzy match, or partial attach is allowed

  Provenance semantics:

  - `provenance.normalization` records that a batch normalization job was
    consulted during artifact build; it does not imply that every
    representative span was annotated
  - `matched_representative_span_count`,
    `unmatched_representative_span_count`, and
    `drifted_representative_span_count` are disjoint categories over the
    representative spans evaluated for attachment

  ### Heuristic Topic Labeling (Structural-only mode)

  In structural-only `semantic-topics` runs (no `--model`), topic labels are
  generated deterministically using a lightweight heuristic.

  This is intended to provide stable, reproducible labels without requiring
  any model dependency.

  Label generation follows these steps:

  - Primary source is representative topic excerpts
  - If unavailable, falls back to reconstructed representative window text
  - If still unavailable, falls back to other member window text

  Text is then processed as follows:

  - normalized (lowercased, markdown/code wrappers stripped)
  - tokenized deterministically
  - filtered to remove:
    - stopwords
    - state-related terms
    - short Latin tokens
    - numeric-only tokens

  Remaining tokens are:

  - ranked by frequency and first occurrence
  - rendered in first-occurrence order
  - truncated to up to 4 tokens

  If no stable topic-bearing tokens can be derived, the label falls back to `misc`.

  ### Limitations

  - heuristic is currently English-biased
  - no phrase-level extraction (token-based only)
  - locale-specific stopwords are currently hardcoded
  - does not use `state_locale` or semantic state resources

  ### Future Direction

  - integrate locale-aware stopword/token filtering via `state_locale`
  - optionally introduce deterministic keyword extraction pass
  - optionally allow local-LLM label enrichment as an additive layer
  - add small evaluation fixtures for label quality validation

- `semantic-topic-explore` is the read-only UX layer on top of those artifacts:
  it reads `topics.json`, `topic_membership.jsonl`, and `message_windows.jsonl`
  and builds in-memory indexes for:
  `topic -> members`, `message -> topic`, and `conversation -> topics`.
  It supports a default topic list, topic detail with timeline, reverse lookup
  from `message_id`, and conversation-centric topic grouping. The default list
  now prefers larger topics first, then broader conversation coverage, then
  higher observed intra-cluster scores when those scores exist, and the text
  view surfaces one representative preview plus lightweight quality hints and
  the current heuristic topic state/confidence.
  When span IDs are available, browse labels prefer span-grounded references
  and keep `window_id` only as a compatibility overlay.

- `semantic-normalization` is an independent L3 sidecar batch CLI. It is
  provider-root scoped, discovers deterministic window-backed spans from
  stored `message_windows.jsonl` with sibling `parsed.jsonl` or falls back to
  `parsed.jsonl`-only deterministic window derivation, freezes a resumable
  worklist, and writes job-scoped sidecar artifacts under
  `l3/semantic-normalization/jobs/<job_id>/`. It remains additive only: it
  does not modify canonical parsing outputs, `message_windows.jsonl`, or the
  `semantic-topics` artifact set.
  For inspection, `semantic-normalization summary` reports one job's label
  distribution, mapping-status counts, and failure kinds, while
  `semantic-normalization compare` provides a developer-facing diff summary
  between two jobs under the same provider root using `span_id` overlap and
  label/status change buckets.
  The semantic-normalization prompts are repository-managed files under
  `src/llm_logparser/resources/semantic_normalization_prompts/`. Job config
  provenance records both the prompt hashes and the repository-relative prompt
  paths, so new jobs are inspectable in Git while older jobs that only stored
  hashes remain loadable.

- `semantic-span-proposals` is an experimental L3 sidecar builder for span
  quality evaluation. It derives candidate semantic spans from canonical
  message sequences plus current window-backed inputs, writes
  `l3/semantic-span-proposals/span_proposals.jsonl`, and records whether each
  proposal is a conservative `split`, `merge`, or `keep` relative to the
  current window-backed unit. It remains additive only: `semantic-topics`,
  cluster membership, normalization jobs, and topic IDs do not consume this
  artifact yet.

- `cross-thread-candidates` is an experimental L3 sidecar builder for
  cross-thread continuity inspection. It reads existing
  `l3/semantic-topics/topics.json`, compares representative spans across
  different conversations with a small deterministic evidence stack, and
  writes `l3/cross-thread-candidates/candidates.jsonl` plus `summary.json`.
  This semantic-topics path remains the default. `--unit-source topic-summaries`
  optionally reads provisional
  `thread-*/l3/intra-thread-topics/topic-summaries.jsonl` rows instead, using
  title, summary, keywords, and explicit conclusion text as matching input.
  `--unit-source auto` prefers usable topic summaries when present and falls
  back to semantic-topics otherwise. Missing topic-summary files are coverage
  gaps rather than errors; invalid rows, empty title+summary rows, and
  low-confidence local LLM rows are skipped and counted in `summary.json`.
  Topic-summary mode now requires direct semantic evidence before candidate
  admission. Weak recurrence signals such as dormant gap, task-like signal,
  specificity, timestamp distance, and context shift remain useful
  diagnostics/ranking signals, but they are not sufficient on their own.
  Generic admission anchors and topic-summary scoring token policy are lexical
  policy loaded from the built-in cross-thread lexical resources, not from
  observed token artifacts such as `l3/token-dictionary/observed_tokens.json`.
  Citation and tool residue tokens such
  as `cite` and `turn0search*` are treated as non-semantic markers and cannot
  admit topic-summary candidates by themselves. `summary.json` records compact
  lexical-rule diagnostics for the resolved built-in resource layers, including
  locale chain, package-relative resource paths, resource SHA1 hashes, and
  category counts. Explicit reviewed project/user lexical rule files can be
  loaded with `--project-lexical-rules` and `--user-lexical-rules`; they append
  to built-in rule categories and are reported as reviewed layers. The summary
  does not emit full token lists, and full resolved-policy export is deferred.
  Reviewed rule files are YAML with `schema_version: "0.1"`,
  `owner_scope: "project"` or `"user"`, and additive lists under
  `rules.topic_summary.scoring`. Reviewed `persona_weak_tokens` are project/user
  policy, not OSS common defaults; when explicitly provided, they apply a small
  topic-summary scoring penalty to overlap dominated by standalone persona/name
  terms without hard-suppressing candidate links.
  Topic-summary candidate rows include diagnostic-only
  `evidence.overlap_diagnostics`, which classifies shared overlap into generic,
  persona weak, residue, and specific buckets. These fields are for review only:
  they do not affect candidate admission, score values, thresholds, or
  reason-code generation.
  The analyzer also writes `l3/cross-thread-candidates/narrative.md`, a
  deterministic Markdown review artifact rendered from existing candidates,
  `summary.json`, and topic summaries when available. It is a review/debug
  layer only and does not change scoring, lexical policy, or semantic decisions.
  A compact candidate index table appears near the top for fast scanning.
  Candidate diagnostics include capped token-level hints when available, such as
  shared keywords and display-derived distinctive/persona/address-like overlap
  tokens. Suspicious/high-ratio `overlap_diagnostics` buckets are rendered only
  inside candidate-detail `#### Diagnostics`, never in the candidate index. These
  hints are for manual inspection only. Low-confidence candidates are rendered in
  detail when the low-confidence set is small, otherwise they remain compact to
  avoid noisy reports.
  Topic-summary mode also uses a separate semantic scoring profile after
  admission: title overlap, summary keyphrase overlap, cleaned keyword-field
  overlap, and local-LLM summary provenance are weighted more heavily, while
  recurrence-style signals such as timestamp distance, dictionary overlap, weak
  bundle overlap, and generic anchor overlap remain secondary support.
  Keyword-field and title scoring is specificity-aware: generic UI/system,
  domain, and date tokens such as `link`, `viewing`, `ai`, `company`, `entity`,
  `年`, `月`, and `日` are treated as low-information overlap and do not raise
  rank bands by themselves. Its score bands are calibrated separately from the
  default semantic-topics path: low `<0.45`, medium `0.45-0.7`, and high
  `>=0.7`.
  Heuristic summary rows remain usable but lower-weight, and inferred
  conclusions are not treated as strong evidence. `summary.json` records
  `topic_summary_admission_filtered_count` and filter reasons for pairs removed
  by this precision gate. The recommended comparison flow is: generate
  intra-thread topic summaries, run
  `cross-thread-candidates --unit-source topic-summaries`, then compare against
  the default semantic-topics output.
  In addition to the original similarity-driven route, it now also admits a
  narrow weak-recurrence route for structurally anchored pairs that are
  separated by a meaningful gap, so low-similarity revisits can still reach
  L4 for classification. `top_per_source` is applied per admission route, so a
  source may emit more than that many links when both routes contribute. It
  prefers structured anchor overlap plus task-like span signals; generic or
  reflective distant overlaps are intentionally filtered more aggressively.
  The structural route now derives a deterministic selective-context
  `task_nucleus_text` from each representative span by splitting fragments,
  scoring them for task-bearing relevance, and retaining only the highest-signal
  task clauses. That nucleus becomes the primary comparison input for
  cross-thread matching, while the original excerpt remains available as the
  fallback/raw traceability view. Broad explanation/comparison framing is
  downweighted, meta-structural prompt/schema/formatting fragments are
  penalized, and concrete action/object/constraint fragments remain available
  for matching. On top of that selective-context layer, dictionary/bundle
  evidence is now evaluated with density-aware nucleus overlap rather than
  mostly boolean support, so concentrated shared task nuclei are preferred
  over broad technical overlap. Repeated prompt residue and generation-wrapper
  boilerplate such as image-return control strings are explicitly suppressed,
  and repeated system/tool instruction residue such as redaction notices,
  file-search/tool-routing directives, and similar control text are suppressed
  as well, so dense repeated wrapper text does not appear as strong recurrence
  evidence.
  Cross-thread lexical behavior is now locale-aware: the analyzer loads
  deterministic lexical resources from `src/llm_logparser/resources/cross_thread/`
  with fallback `exact locale -> language default -> en-US`, so new locales can
  extend residue/task markers without changing the core scoring code.
  Weak-route evidence is recorded as neutral structural signals such as
  anchor overlap, dormant gap, task-like signal, and context shift rather than
  as an L3 recurrence label. It
  emits reviewable candidate links only; it does not merge topics, rewrite
  membership, or change `topic_id`. Current candidate rows also expose
  recurrence-oriented instrumentation signals such as `volume_gap`,
  `temporal_gap_seconds`, `continuity_mask`, `dormancy_score`,
  `specificity_score`, and `local_context_delta`. These fields are additive
  inspection metadata only: they do not replace the existing scoring model or
  silently change canonical/L1/L2 behavior.

- `lexical-rule-candidates` is an inactive L3 diagnostic builder for Roadmap 6.
  Phase 1 reads `l3/token-dictionary/observed_tokens.json` (or legacy
  `dictionary.json`) and suggests inactive `generic_scoring_token` candidates
  from high-frequency / high-spread observed token statistics. It also suggests
  inactive `persona_weak_token` candidates for recurring
  persona/name/address-like overlap that should be reviewed separately from
  generic scoring tokens, and inactive `distinctive_allow_token` candidates for
  domain/project/topic tokens that should be protected from generic weakening.
  It writes `l3/lexical-rules/candidates.jsonl` and
  `diagnostics.json`. Phase 1 applies conservative token-shape filtering for
  URL/path-like tokens, numeric/date-like residue, hashes/IDs, and overly long
  identifier-like tokens before emitting review candidates. When
  `thread-*/l3/intra-thread-topics/topic-summaries.jsonl` exists, the command
  adds optional title/summary/keyword/conclusion occurrence counts and short
  sample references to improve manual review. Latin token evidence matching is
  boundary-aware; CJK evidence remains substring-based. Missing topic summaries
  are not errors. Candidate scores are normalized review-priority scores for
  this inactive diagnostic artifact only; they do not affect cross-thread
  candidate scoring directly. Candidate-generation heuristics are loaded from a
  built-in, versioned resource and summarized in `diagnostics.json`; no
  project/user override for candidate generation exists yet. Candidate rows
  always use `status: inactive` and `activation_state: requires_review`. The
  command also writes
  `l3/lexical-rules/review.md` as a human-readable review aid with copyable YAML
  snippets; users must manually copy accepted rules into reviewed lexical rule
  files. The review warns that names/personas should generally become reviewed
  project/user `persona_weak_tokens`, not generic tokens. It does not write
  `reviewed.yaml`, does not modify reviewed project/user rule files, and does
  not activate or promote anything automatically. Existing built-in and
  explicitly provided reviewed project/user lexical rules are treated as already
  active policy and are not re-suggested. `observed_tokens.json` is the primary
  observed token index / corpus token statistics artifact, legacy
  `dictionary.json` remains readable for compatibility, and `bundles.json`
  remains optional cooccurrence evidence only.

- `lexical observed list` and `lexical observed inspect` are read-only views of
  observed corpus facts. They can use `observed_tokens.json`, `bundles.json`,
  and `provenance.json`, with compatibility fallback to legacy
  `dictionary.json`. They do not classify tokens or apply active lexical policy.

- `lexical candidates list` and `lexical candidates inspect` are read-only
  views of inactive lexical-rule candidate artifacts. They expose candidate
  type, suggested rule path, review score, evidence summaries, and compact
  diagnostics for human review only. They do not promote, reject, edit, or
  activate rules.

- `lexical policy validate` and `lexical policy resolve` are read-only policy
  inspection commands. Reviewed project/user YAML is the active editable policy
  surface, while `l3/token-dictionary/lexical_rules.json` remains generated
  seed / legacy policy-like data and should not be edited or treated as
  reviewed policy. The resolve command emits compact provenance, source
  paths/hashes, locale chain, and category counts; it does not emit full token
  lists.

- `user_lexical_profile` is reserved for a future provider-crossing user-level
  lexical memory contract. It is documented as a stub only and is not wired into
  scoring. Reviewed project/user lexical YAML remains the active editable policy
  surface today.

- `cross-thread-intent-eval` is an experimental L4 sidecar builder on top of
  the stored L3 cross-thread candidates. It reads
  `l3/cross-thread-candidates/candidates.jsonl`, evaluates whether each
  already-emitted pair expresses the same underlying intent / event / task
  continuation through a local Ollama model, and writes
  `l4/cross-thread-intent-eval/evaluations.jsonl` plus `summary.json`. It does
  not change L3 candidate generation, thresholds, or ranking.

- `cross-thread-memory-recall` is a read-only presentation layer on top of the
  stored L4 intent evaluations. It reads
  `l4/cross-thread-intent-eval/evaluations.jsonl`, keeps only `same_intent=yes`
  rows with medium-or-higher confidence, groups them by source span, and
  renders a short human-readable "have I talked about this before?" view. It
  does not write or mutate any L3/L4 artifacts.

Current limitations remain explicit:

- semantic clusters are not canonical topics
- topic membership is currently `1 topic = 1 L3 cluster`; no cross-cluster
  merge logic is applied yet
- topic state is heuristic rather than canonical or model-enriched
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
- `--cluster-id`: build artifacts for one cluster only; keep this explicit at
  runtime because it changes which exact artifact slice is built
- `--min-cluster-size`: skip very small clusters before artifact generation
- `--cross-thread-only` / `--no-cross-thread-only`: limit artifact generation
  to multi-conversation clusters or explicitly turn that config-backed default
  back off at the CLI
- `--base-url` / `--timeout-seconds`: control the local Ollama endpoint when
  `--model` is used
- `--input`: must be passed explicitly at runtime; `semantic-topics` does not
  read its provider-root target from config for safety
- `--normalization-job`: must be passed explicitly at runtime; `semantic-topics`
  does not read batch-job selectors from config for safety
- `--strict-normalization` / `--no-strict-normalization`: allow config-backed
  consistency policy while preserving explicit CLI override in either
  direction

`semantic-topics` config behavior is intentionally split:

- stable runtime knobs such as `model`, `min_cluster_size`,
  `cross_thread_only`, `base_url`, `state_locale`,
  `expected_taxonomy_version`, `strict_normalization`, and
  `timeout_seconds` may come from `config.yaml`
- target selectors such as `--input`, `--cluster-id`, and
  `--normalization-job` must stay explicit at runtime because they choose the
  exact provider root, cluster slice, or batch sidecar to consume

Key `semantic-topic-explore` flags:

- `--topic-id`: show one topic in detail, including a chronological timeline
  over its windows
- `--message-id`: reverse lookup one message into one or more topics
- `--conversation-id`: show all topics that appear in one conversation
- `--hide-single-window`: suppress browse-time singleton topics when
  `quality_signals.single_window` is available
- `--min-window-count`: suppress browse-time topics smaller than the requested
  window count
- `--min-conversation-count`: suppress browse-time topics with narrower
  conversation coverage
- `--json`: emit structured machine-readable output instead of pretty text

Practical browsing guidance:

- Default browse behavior is intentionally inclusive. It is useful for full
  inspection, but on real data it can be noisy because many topics are
  single-window fragments.
- Use `--hide-single-window` first for everyday browsing. In the current
  validated subset, it removed most browse noise without losing the observed
  cross-thread topics.
- Use `--min-conversation-count 2` when the goal is cross-thread continuity
  rather than topic exhaustiveness. This is the clearest “show me ongoing work
  across conversations” mode.
- Use `--min-window-count 3` for a stricter deep-inspection pass when you want
  to focus on somewhat larger topic groups.
- Be careful with over-filtering. In the current validated subset,
  `--min-window-count 5` was already too aggressive for general browsing and
  removed too many smaller useful topics. These recommendations come from one
  real-data subset and may evolve as more corpus validation is done.

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
