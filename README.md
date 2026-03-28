# llm-logparser

[![PyPI version](https://img.shields.io/pypi/v/llm-logparser)](https://pypi.org/project/llm-logparser/)[![Python versions](https://img.shields.io/pypi/pyversions/llm-logparser)](https://pypi.org/project/llm-logparser/)[![License](https://img.shields.io/github/license/Syun-tnb/llm-logparser)](LICENSE)[![GitHub Sponsors](https://img.shields.io/github/sponsors/Syun-tnb)](https://github.com/sponsors/Syun-tnb)

A local-first CLI that turns raw LLM chat exports into structured data you can
read, search, and analyze — without sending anything to the cloud.

**What it does:**

1. **Parse** your ChatGPT (or other LLM) export into clean, structured data
2. **Export** conversations as readable Markdown files
3. **Analyze** your conversations: message counts, token usage, safety metrics, timelines, and more

Experimental higher-layer analysis is also available as an additive, local-first
preview path: window embeddings, semantic neighbors, a read-only
`semantic-preview` renderer, a formal `semantic-topics` artifact builder, and
an experimental `semantic-topic` renderer that asks a local Ollama model for
cluster labels and summaries. These semantic features are non-canonical,
rebuildable, and still intentionally conservative in scope.

Everything runs locally. No cloud. No telemetry. Your data stays on your machine.

> Current parse/import adapters: OpenAI ChatGPT, Anthropic Claude, xAI Grok, Mistral Le Chat, and Google Gemini My Activity.

---

## Installation

> **Most users:** `pip install llm-logparser` is all you need.

Install from PyPI with either `pip` or `uv`:

```bash
pip install llm-logparser
```

```bash
uv pip install llm-logparser
```

If you are working from a repository checkout instead of an installed package:

```bash
uv sync
uv sync --extra dev
```

Command alias:

`llp` is a convenience alias for `llm-logparser`. All commands work the same way under either executable.

Examples:

```bash
llp parse ...
llm-logparser analyze stats ...
```

If you cloned the repository, you can run commands with `uv run ...`. If you installed the package, run `llm-logparser ...` directly.

---

## Quick Start

The fastest way to go from a raw export to readable output:

```bash
llm-logparser chain \
  --provider openai \
  --input path/to/conversations.json \
  --outdir artifacts \
  --timezone Asia/Tokyo
```

This does two things:
1. **Parses** your raw export into structured per-conversation data
2. **Exports** each conversation as a Markdown file

When it finishes, you'll see a directory like this:

```text
artifacts/output/openai/
  manifest.json                        ← index of all parsed conversations
  thread-abc123/
    parsed.jsonl                       ← structured conversation data
    thread_stats.json                  ← message counts, timestamps, etc.
    message_windows.jsonl              ← grouped message segments
    thread-abc123__gpt-4o.md           ← readable Markdown transcript
```

Open any `.md` file to read your conversations. That's it — you're done with
the basics.

**Want to go further?** See [First Steps After Setup](#first-steps-after-setup)
to learn what the analyze commands can tell you about your conversations.

---

## First Steps After Setup

After running `chain` (or `parse`), here is the recommended path to get the
most from your data. Each step builds on the previous one.

### Step 1 — Browse your conversations

Open the generated `.md` files. They are standard Markdown with timestamps,
role labels, and preserved formatting.

### Step 2 — Get a quick summary

```bash
llm-logparser analyze stats --input artifacts/output/openai
```

This prints a summary of your conversations: how many threads, total messages,
character counts, and time spans. Add `--json` for machine-readable output.

### Step 3 — Count tokens

```bash
llm-logparser analyze tokens --input artifacts/output/openai
```

This writes a `token_stats.json` file next to each conversation with per-message
token counts. Useful if you want to understand cost or context-window usage.

### Step 4 — Build full metrics

```bash
llm-logparser analyze metrics --input artifacts/output/openai
```

This writes a `metrics.json` file next to each conversation with safety signals,
interaction patterns, and character/token ratios. **Requires Step 3 first.**

> **You can stop at any step.** Each one is independently useful. Steps 3–4
> produce files that sit alongside your parsed data and can be rebuilt at any time.

---

## What Do Analyzers Actually Output?

Each analyze command produces a different kind of output. Here is what you
actually get and what questions each one answers.

---

### `analyze stats` → Terminal summary (or JSON)

**What it contains:** Aggregated counts and distributions across your conversations.

**Questions it answers:**
- How many conversations do I have? How many messages total?
- What's my longest conversation?
- What's the user vs assistant message ratio?

**Example output (text):**
```text
threads:    12
messages:   847
  user:     421
  assistant: 426
characters: 312,504
timespan:   2025-01-15 — 2025-03-20
```

Add `--json` for structured output. Add `--per-thread` for per-conversation
breakdown.

---

### `analyze tokens` → `token_stats.json` (per conversation)

**What it contains:** Token counts for every message, broken down by role, plus
tokenizer metadata.

**Questions it answers:**
- How many tokens did this conversation use?
- Which messages are the most expensive?
- What's the user vs assistant token split?

**Example fields:**
```json
{
  "summary": { "total_tokens": 4821, "total_messages": 42 },
  "by_role": { "user": { "tokens": 1200 }, "assistant": { "tokens": 3621 } },
  "messages": [{ "message_id": "m1", "role": "user", "token_count": 28 }]
}
```

---

### `analyze metrics` → `metrics.json` (per conversation)

**What it contains:** Derived metrics including character/token ratios,
vocabulary diversity, safety signals, and interaction patterns.

**Questions it answers:**
- Did the assistant refuse any requests? How often?
- Did the user revise or correct themselves?
- What's the assistant-to-user character ratio?
- How quickly did the user respond after assistant messages?

**Example fields:**
```json
{
  "safety": { "refusal_count": 1, "intervention_count": 2 },
  "interaction": { "revision_count": 3, "correction_count": 1 },
  "user_effort": { "rapid_revisions": 2, "response_length_ratio": 3.4 },
  "diversity": { "type_token_ratio": 0.62 }
}
```

**Requires `token_stats.json`** — run `analyze tokens` first.

---

### `analyze datasheet` → Report (Markdown or JSON)

**What it contains:** A concise, appendix-ready dataset summary covering
composition, temporal span, and key statistics.

**Questions it answers:**
- What does this dataset look like as a whole?
- What would I put in a research paper appendix?

**Output:** Markdown by default. Add `--json` for structured data.

---

### `analyze timeline` → Time-bucketed activity (text or JSON)

**What it contains:** Message counts grouped by time period (hour, day, week, or
month).

**Questions it answers:**
- When was I most active?
- Are there gaps in my usage?

**Example output:**
```text
2025-01-15:  48 messages
2025-01-16:  12 messages
2025-01-17:   0 messages
2025-01-18:  93 messages
```

---

### `analyze sqlite-build` → `analysis.db` (optional)

**What it contains:** A SQLite database combining thread stats, messages, and
message windows into queryable tables.

**Questions it answers:**
- Which conversations mention a specific topic?
- What are all assistant messages in a date range?
- Cross-conversation aggregation via SQL

**You can skip this entirely.** It is an optional acceleration layer for users
with large datasets who want to run SQL queries. The database is fully
rebuildable from your parsed data at any time.

```bash
llm-logparser analyze sqlite-build \
  --input artifacts/output \
  --provider openai
```

---

## When To Use What

| Command | When to use it |
|---------|---------------|
| `analyze stats` | Get a quick summary of your conversations |
| `analyze tokens` | Count tokens per message (for cost/context analysis) |
| `analyze metrics` | Understand safety, interaction, and effort patterns |
| `analyze datasheet` | Generate a research-ready dataset summary |
| `analyze timeline` | See when you were most active |
| `analyze sqlite-build` | Query large datasets with SQL (optional) |
| `analyze semantic-prototype` | Build experimental window embeddings, thresholded semantic neighbors, and minimal semantic clusters |
| `analyze semantic-preview` | Inspect stored semantic clusters, conversations, and windows in terminal |
| `analyze semantic-topics` | Build formal topic artifacts and explicit reverse-lookup membership rows from semantic clusters |
| `analyze semantic-topic-explore` | Browse topic lists, topic timelines, reverse message lookup, and conversation/topic coverage |
| `analyze semantic-topic` | Generate experimental topic labels and summaries from stored semantic clusters with a local Ollama model |

---

## CLI Reference

The sections below use `uv run` because they are also convenient when working from a source checkout. If you installed from PyPI, remove the `uv run` prefix.

### Parse

Normalize raw provider exports into canonical thread artifacts:

```bash
uv run llm-logparser parse \
  --provider openai \
  --input <file> \
  --outdir artifacts \
  [--message-window-size <N>] \
  [--message-window-stride <N>] \
  [--dry-run] [--fail-fast] \
  [--validate-schema]
```

`message_windows.jsonl` is generated during `parse`. The default remains
non-overlapping windows because omitted stride falls back to the window size.
Use `--message-window-stride` smaller than `--message-window-size` to opt into
deterministic overlapping/sliding windows.

### Export

Render canonical `parsed.jsonl` into Markdown:

```bash
uv run llm-logparser export \
  --input parsed.jsonl \
  [--out <md>] \
  [--split auto|size=N|count=N] \
  [--timezone <IANA>] \
  [--formatting none|light]
```

### Extract

Extract a single conversation as Gemini-compatible JSON, with PII masking:

```bash
uv run llm-logparser extract \
  --provider openai \
  --input <file> \
  --conversation-id <id> \
  --outdir artifacts \
  [--dry-run]
```

### Chain

Parse and export in a single command. If you already ran `parse` separately and only want to export, use `export` directly. `chain` is a convenience for the common parse → export workflow.

```bash
uv run llm-logparser chain \
  --provider openai \
  --input <raw> \
  --outdir artifacts \
  [--validate-schema] \
  [other export options...]
```

Useful options:

```text
--parsed-root       reuse existing parsed threads
--export-outdir     place Markdown elsewhere
--dry-run           parse only (no writes)
--fail-fast         stop on first export error
```

### Analyze Stats

Compute deterministic thread and message statistics from canonical `parsed.jsonl` files:

```bash
uv run llm-logparser analyze stats \
  --input <parsed.jsonl-or-directory> \
  [--per-thread] \
  [--top <N>] \
  [--sort messages|chars|span|conversation_id] \
  [--include-role-breakdown] \
  [--json] \
  [--out <path>]
```

Use `analyze stats` when you want aggregation and exploratory summaries across
one thread or a directory of threads. It can render text or JSON directly from
canonical `parsed.jsonl`, and its additive `research_summary` section provides
deterministic temporal, turn-taking, safety, and lightweight structural
aggregates.

### Analyze Datasheet

Build a concise appendix-ready dataset summary from canonical parsed artifacts:

```bash
uv run llm-logparser analyze datasheet \
  --input <parsed.jsonl-or-directory> \
  [--json] \
  [--out <path>]
```

Use `analyze datasheet` when you want a stable report layer rather than an
exploratory summary. Markdown is the default output. `--json` returns the same
content as a machine-readable summary object.

### Analyze Timeline

Aggregate timestamped message activity from canonical `parsed.jsonl` files:

```bash
uv run llm-logparser analyze timeline \
  --input artifacts/output/openai \
  --bucket day \
  [--json] \
  [--out <path>]
```

### Analyze Tokens

Build deterministic per-thread `token_stats.json` sidecars from canonical `parsed.jsonl`:

```bash
uv run llm-logparser analyze tokens \
  --input <parsed.jsonl-or-directory> \
  [--model <model>] \
  [--encoding <tiktoken-encoding>] \
  [--skip-existing] \
  [--dry-run]
```

Current tokenizer backend:

* `tiktoken`
* provider defaults for `openai`, `anthropic`, and `xai`
* `--encoding` overrides provider and model resolution

Runtime caveats:

* `tiktoken` may perform a one-time network fetch on first use to download encoding data
* downloaded encoding data is cached locally afterward
* subsequent token analysis runs use the local cache
* existing `token_stats.json` sidecars are rebuilt by default; `--skip-existing` only fills in missing sidecars
* `--dry-run` previews sidecar generation without writing files

### Analyze Metrics

Build deterministic per-thread `metrics.json` sidecars from `parsed.jsonl` plus `token_stats.json`:

```bash
uv run llm-logparser analyze metrics \
  --input <parsed.jsonl-or-directory> \
  [--skip-existing] \
  [--dry-run]
```

Run `analyze tokens` first so each thread already has a sibling `token_stats.json`.

Current metrics include:

* ratio, token, character, distribution, and diversity metrics
* `safety.refusal`
* `safety.intervention_count`
* `interaction.revision` with `correction`, `clarification`, and `retry` subtype counts
* `user_effort` metrics

Additional behavior notes:

* existing `metrics.json` sidecars are rebuilt by default
* `--skip-existing` only fills in missing sidecars
* `--dry-run` previews sidecar generation before writing

### Analyze SQLite Build

Build an optional per-provider SQLite analysis index from canonical and
deterministic thread artifacts:

```bash
uv run llm-logparser analyze sqlite-build \
  --input <provider-artifact-root> \
  --provider <provider-id> \
  [--overwrite]
```

`analysis.db` is an optional deterministic, rebuildable, and non-canonical index
for query acceleration. It does not replace `parsed.jsonl` and is not a storage
layer for every future derived artifact.

### Analyze Semantic Prototype

Build experimental semantic sidecars from deterministic `message_windows.jsonl`:

```bash
uv run llm-logparser analyze semantic-prototype \
  --input <provider-artifact-root> \
  [--backend deterministic-hash|ollama] \
  [--model <local-embedding-model>] \
  [--top-k <N>] \
  [--min-score <float>] \
  [--sqlite-db <path/to/analysis.db>] \
  [--candidate-window-days <N>] \
  [--candidate-min-chars <N>] \
  [--candidate-min-assistant-ratio <float>] \
  [--candidate-same-thread allow|prefer|only|exclude] \
  [--overwrite]
```

This command currently produces:

* `window_embeddings.jsonl`
* `window_neighbors.jsonl`
* `window_clusters.jsonl`

These outputs are:

* experimental
* local-first
* non-canonical
* additive
* rebuildable from L1 artifacts

Current Ollama chunking behavior is deterministic and UTF-8 byte-based. It is
intentionally not tokenizer-accurate token budgeting.

Backend and model are separate:

* `backend` selects the runtime binding such as `deterministic-hash` or `ollama`
* `model` selects the embedding model identifier for that backend

The primary configuration surface is CLI/config, not Python constants. Safe
built-in fallback embedding settings remain conservative at
`max_input_bytes=256`, `chunk_overlap_bytes=32`, `aggregate=mean`. A small
compatibility fallback still exists for a couple of historic Ollama model IDs
when users omit explicit embedding tuning, but recommended settings should be
declared explicitly in config.

```yaml
analyze:
  semantic_prototype:
    backend: ollama
    model: embeddinggemma
    backend_options:
      base_url: http://localhost:11434
      timeout_seconds: 30.0
    embedding:
      max_input_bytes: 2048
      chunk_overlap_bytes: 128
      aggregate: mean
```

The semantic layer is not yet positioned as a stable topic system.

Current L3 prototype behavior is intentionally limited:

* `window_neighbors.jsonl` still stores nearest-neighbor links, but those links are now filtered by `--min-score` before emission
* when `--sqlite-db` is provided, candidate windows are first narrowed with `analysis.db`, then compared symmetrically inside each deduplicated local candidate pool instead of using a global dense comparison for every pair
* `window_clusters.jsonl` groups windows by connected components over retained mutual neighbor links; same-thread mutual edges are suppressed when the two windows share more than one underlying message, while cross-thread mutual edges must also meet a stricter runtime threshold equal to the current corpus P75 of cross-thread mutual scores
* if cross-thread mutual scores are unavailable in older neighbor rows, cluster construction falls back to the legacy mutual-only behavior for those edges instead of failing
* clusters are structural groupings only; they are not canonical topics, do not carry labels, and do not model lifecycle state or summaries

If `--sqlite-db` is omitted, `semantic-prototype` keeps its original all-windows fallback path and computes neighbors directly from the full embedded window set.

Window shape is controlled at parse time because `semantic-prototype` reads the
stored `message_windows.jsonl` artifact. Config therefore uses the parse/chain
sections:

```yaml
parse:
  message_windows:
    size: 4
    stride: 2

chain:
  message_windows:
    size: 4
    stride: 2
```

### Analyze Semantic Preview

Browse stored L3 semantic clusters in a readable terminal view. By default the
command lists the largest clusters from `window_clusters.jsonl`:

```bash
uv run llm-logparser analyze semantic-preview \
  --input <provider-artifact-root> \
  [--top-clusters <N>] \
  [--min-cluster-size <N>] \
  [--cross-thread-only]
```

Cluster detail:

```bash
uv run llm-logparser analyze semantic-preview \
  --input <provider-artifact-root> \
  --cluster-id <cluster_id> \
  [--top-k <N>]
```

Conversation-centric view:

```bash
uv run llm-logparser analyze semantic-preview \
  --input <provider-artifact-root> \
  --conversation-id <conversation_id> \
  [--cross-thread-only]
```

Legacy single-window lookup is still available:

```bash
uv run llm-logparser analyze semantic-preview \
  --input <provider-artifact-root> \
  --conversation-id <conversation_id> \
  --window <window_id> \
  [--top-k <N>] \
  [--max-chars <N>]
```

`semantic-preview` is read-only. It reuses stored `message_windows.jsonl`,
`window_clusters.jsonl`, and optional `window_neighbors.jsonl`; it does not
recompute embeddings or write new files. `--json` emits machine-readable output
for downstream tooling.

### Analyze Semantic Topics

Build formal topic artifacts under `<provider-artifact-root>/l3/semantic-topics/`:

```bash
uv run llm-logparser analyze semantic-topics \
  --input <provider-artifact-root> \
  [--model <ollama-model>] \
  [--min-cluster-size <N>] \
  [--cross-thread-only]
```

Target one cluster only:

```bash
uv run llm-logparser analyze semantic-topics \
  --input <provider-artifact-root> \
  --cluster-id <cluster_id> \
  [--model <ollama-model>]
```

`semantic-topics` writes:

- `topics.json`: forward topic index in `schema_version: "1.0"` with top-level
  `generated_at`, `source_inputs`, and `provenance`, plus per-topic
  cluster/window/message references
- `topic_membership.jsonl`: reverse lookup rows for `cluster -> topic`, `window -> topic`, and `message -> topic`

Structural fields such as `topic_id`, `cluster_ids`, `window_refs`,
`message_refs`, `conversation_ids`, `first_seen`, and `last_seen` are built
from stored L3 artifacts. `cluster_ids` remain the primary L3-native anchor;
`window_refs` and `message_refs` are derived from cluster membership. The
forward artifact also carries `state`, which is currently emitted as `null` for
every topic. If `--model` is omitted, labels and summaries remain empty and the
command still writes structural-only artifacts. `provenance` records both the
topic-labeling settings and the upstream L3 clustering basis so the artifact
stays additive, rebuildable, and non-canonical.

### Analyze Semantic Topic Explore

Browse the semantic topic index without any model calls:

```bash
uv run llm-logparser analyze semantic-topic-explore \
  --input <artifact-root>
```

Topic detail:

```bash
uv run llm-logparser analyze semantic-topic-explore \
  --input <artifact-root> \
  --topic-id <topic_id>
```

Reverse lookup from one message:

```bash
uv run llm-logparser analyze semantic-topic-explore \
  --input <artifact-root> \
  --message-id <message_id>
```

Conversation-centric view:

```bash
uv run llm-logparser analyze semantic-topic-explore \
  --input <artifact-root> \
  --conversation-id <conversation_id>
```

`semantic-topic-explore` reads `topics.json`, `topic_membership.jsonl`, and
`message_windows.jsonl`, builds in-memory indexes, and lets you navigate:

- `topic -> conversations / windows / messages`
- `message -> topic`
- `topic -> timeline`

### Analyze Semantic Topic

Render experimental topic labels and summaries from stored L3 clusters without
writing artifacts:

```bash
uv run llm-logparser analyze semantic-topic \
  --input <provider-artifact-root> \
  --model <ollama-model> \
  [--top-clusters <N>] \
  [--min-cluster-size <N>] \
  [--cross-thread-only]
```

One cluster only:

```bash
uv run llm-logparser analyze semantic-topic \
  --input <provider-artifact-root> \
  --model <ollama-model> \
  --cluster-id <cluster_id>
```

`semantic-topic` is read-only. It requires stored `message_windows.jsonl` and
`window_clusters.jsonl`, does not recompute embeddings, and does not write any
new artifacts. The current production prompt is fixed from the repository's
tmp-based prompt experiment winner: Prompt B with an 8-window per-cluster cap
and 300-character per-window truncation. `--json` emits the same result in a
machine-readable form. Use `semantic-topics` when you need durable topic
artifacts and reverse lookup.

---

## Markdown Format

Each exported Markdown file begins with YAML front matter:

```yaml
---
thread: "abc123"
provider: "openai"
messages: 42
models: ["gpt-4o"]
range: "2025-10-01T01:00:00+00:00 〜 2025-10-18T10:15:00+00:00"
---
```

Messages follow in timestamp order:

```markdown
## [User] 2025-10-18 10:00
Good morning!

## [Assistant] 2025-10-18 10:01
Good morning - how can I help today?
```

Markdown is GFM-compatible and preserves:

* fenced code blocks
* links
* tables
* quotes

---

## Splitting

Split large Markdown output by size, count, or an automatic preset:

```text
--split size=4M
--split count=1500
--split auto
```

Extra tuning:

```text
--split-soft-overflow 0.20
--split-hard
--tiny-tail-threshold 20
```

---

## Architecture

> **This section is for understanding how the tool works internally.** You don't
> need it to use the tool — see [Quick Start](#quick-start) instead.

### Pipeline

`llm-logparser` is built around one canonical source of truth:

```text
raw export
  -> parse
  -> canonical parsed.jsonl
     -> export        -> Markdown transcripts
     -> analyze stats -> aggregation and exploratory summaries
     -> analyze tokens -> token_stats.json
     -> analyze metrics -> metrics.json
     -> analyze datasheet -> appendix-ready report (Markdown or JSON)
```

Analyzer commands reuse existing sidecar artifacts when they are already present
and fall back to canonical `parsed.jsonl` when they are missing. Output remains
deterministic and local-first either way.

### Canonical Data Model

The parser normalizes provider-specific exports into a stable JSONL schema. That JSONL is the canonical intermediate format for the project.

Downstream features consume that canonical layer:

* Markdown export
* HTML or GUI viewers
* analyzers
* future applications

Parser responsibilities end at deterministic JSONL generation. Presentation, export formatting, and analysis are downstream concerns handled separately.

### Analyzer Layering

At a glance:

* canonical `parsed.jsonl` is the source of truth
* `thread_stats.json`, `token_stats.json`, and `metrics.json` are sidecar artifacts
* Layer 1 (L1) is deterministic analysis: `stats`, `timeline`, `tokens`, `metrics`, and `datasheet`
* Layer 2 (L2) is `analyze sqlite-build`: an optional deterministic SQLite index
* Layer 3 (L3) now has an experimental additive semantic path: window embeddings, semantic neighbors, and `semantic-preview`
* L3 remains experimental, local-first, additive, rebuildable, and non-canonical
* Layer 4 (L4) remains a future model/API layer, not a stable current CLI feature
* `analyze sqlite-build` is a rebuildable non-canonical index, not a general-purpose analysis engine or catch-all derived-data store

Sidecar artifacts are rebuildable from canonical `parsed.jsonl` and contain no runtime timestamps. `analysis.db` is likewise rebuildable and non-canonical.

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

Incremental sidecar policy:

- default behavior: existing `token_stats.json` and `metrics.json` sidecars are rebuilt and overwritten
- `--skip-existing`: leave an existing sidecar untouched and only build missing sidecars
- `--dry-run`: preview detected threads plus planned create/rebuild/skip counts without writing files
- `analyze metrics --skip-existing` still requires pre-existing `token_stats.json` for any thread whose `metrics.json` is missing

Analyzer i18n is intentionally narrow:

- locale-backed YAML resources only affect heuristic inputs such as refusal and revision cues
- the human-readable text renderers for `analyze stats`, `analyze datasheet`,
  and `analyze timeline` are intentionally English-only
- structured JSON output and schema keys remain English for tooling stability

### Directory Layout

> **You don't need to memorize this.** The tool creates this structure
> automatically. This reference is here when you need it.

```text
artifacts/
  output/
    openai/
      manifest.json
      thread-<conversation_id>/
        parsed.jsonl
        thread_stats.json
        message_windows.jsonl
        token_stats.json
        metrics.json
        thread-<conversation_id>__*.md
        meta.json (optional)
```

Pass only the root path via `--outdir`. The tool creates `output/<provider>/...` automatically.

---

## Configuration (`config.yaml`)

> **Configuration is optional.** The CLI works with just command-line flags.
> `config.yaml` is useful when you want to save default settings or manage
> multiple export sources.

`llm-logparser` supports optional runtime defaults via YAML `config.yaml`. CLI flags always take precedence. Profile values are used only to fill in missing options.

External provider mapping YAML is not used at runtime yet. Current normalization is adapter-based under `src/llm_logparser/core/providers/`.

### Config Discovery Order

When no `--config` flag is provided, the tool searches in this order:

1. explicit `--config <path>`
2. environment variable `LLM_LOGPARSER_CONFIG=<path>`
3. `config.yaml` in the current directory
4. the nearest parent directory containing `config.yaml`
5. `~/.config/llm-logparser/config.yaml` when applicable

If no configuration file is found, the CLI behaves normally.

---

### Profiles

You can define multiple profiles and select one using `--profile <name>`:

```yaml
schema_version: 1
active_profile: default

profiles:
  default:
    provider: openai

    input:
      path: exports/messages.jsonl
      # or:
      # paths: [exports/a.jsonl, exports/b.jsonl]
      # export uses:
      # parsed: artifacts/output/openai/thread-123/parsed.jsonl

    sanitize:
      enabled: true
      replacement: REDACTED
      scope: content_parts

    output:
      path: artifacts/thread.md
      formatting: light
      split: auto

    parse:
      outdir: artifacts
      validate_schema: true
```

Profile selection priority:

```text
--profile > active_profile > the only defined profile
```

Value precedence for supported config-backed options:

```text
CLI flags > selected profile values > built-in CLI defaults
```

The section-based shape is canonical. Older profile-level compatibility keys such as `outdir`, `dry_run`, `fail_fast`, `validate_schema`, `export_outdir`, `parsed_root`, and `conversation_id` are still accepted for `schema_version: 1`, but the loader warns and points to the section-based replacements. That compatibility is intended for removal in a future schema-version-2 cleanup.

If multiple `input.paths` are defined and no explicit `--input` is provided:

* in interactive mode, you will be prompted
* in non-interactive mode, the program exits with code `2`

If multiple profiles exist and neither `--profile` nor `active_profile` selects one:

* in interactive mode, you will be prompted to choose a profile
* in non-interactive mode, no profile defaults are applied

---

### Relative Path Resolution

Relative paths defined in `config.yaml` are resolved against the directory where the discovered `config.yaml` resides.

This keeps behavior stable when using:

```bash
LLM_LOGPARSER_CONFIG=/etc/llm/config.yaml
```

and avoids unintended CWD-dependent path resolution.

---

### Config Subcommands

Use these helpers to inspect and debug config resolution:

```bash
uv run llm-logparser config path
uv run llm-logparser config show [--profile work]
uv run llm-logparser config validate
```

`config show` prints the normalized selected profile when one is resolved. Otherwise it prints the normalized full config structure.

For `extract`, the canonical sanitize section is:

```yaml
sanitize:
  enabled: true
  replacement: REDACTED
  scope: content_parts   # or: all_strings
  extra_keywords: [credential]
  mask_patterns:
    - acct-\d+
```

If `sanitize` is omitted, `extract` keeps the current safe default behavior:

* sanitization stays enabled
* sensitive field names are redacted
* built-in email and phone patterns are applied to `content.parts`

---

### Non-Interactive Mode

Disable prompts with:

```bash
--non-interactive
```

or:

```bash
LLM_LOGPARSER_NON_INTERACTIVE=1
```

In non-interactive mode, the program exits with code `2` if:

* required options are missing
* multiple input candidates are ambiguous

This makes the CLI safe for CI and automation workflows.

---

## Localization

> **You can ignore this section** unless you need non-English heuristic phrase
> lists or want Japanese CLI output.

`llm-logparser` uses a best-effort i18n model. Locale files are optional, user-extensible YAML resources, and missing keys are expected to fall back safely rather than block execution.

You can control output formatting with:

```text
--locale   en-US | ja-JP | …
--timezone Asia/Tokyo | UTC | …
```

Locale files live under `src/llm_logparser/i18n/*.yaml` and may contain:

* `messages:` for scalar CLI, help, runtime, and error text
* `analysis:` for structured analyzer phrase resources

Localized:

* CLI, help, and runtime messages from `messages:`
* analyzer heuristic phrase resources from `analysis:`

Not localized by design:

* `analyze stats`, `analyze datasheet`, and `analyze timeline` rendered summaries
* JSON artifacts and stable schema keys
* argparse built-ins such as `usage:` and parser-generated boilerplate
* Markdown timestamp formatting beyond timezone conversion

Locale precedence:

1. CLI `--locale` or `--lang`
2. environment variable `LLP_LOCALE`
3. selected profile locale `profiles.<name>.locale` when applicable
4. `en-US`

Notes:

* not all commands fully honor profile-level locale yet; CLI and environment settings take precedence
* parser and help output can pick up CLI locale early via raw argv scanning
* unknown locales resolve to `en-US`
* missing message keys fall back to `en-US`, then to the raw key if still missing
* analyzer resource keys fall back to `en-US`
* short aliases such as `en` and `ja` are auto-derived from locale filenames when the language prefix is unambiguous
* if multiple locale files share a language prefix, use the full locale tag

---

## YAML Customization

Locale data is YAML-driven. Locale files under `src/llm_logparser/i18n/` are best-effort extensions, not strict contracts: partial files are acceptable and fallback to `en-US` is normal behavior.

Scalar CLI, help, and runtime messages live under `messages:`, and analyzer phrase tuning lives under `analysis:`.

Keys:

* `analysis.refusal.indicators`
  Phrase list used by `metrics.json` refusal detection for assistant messages.

* `analysis.revision.cues`
  Phrase list used by `metrics.json` revision detection for user messages.

* `analysis.correction.cues`
  Phrase list used by `metrics.json` correction subtyping for detected revisions.

* `analysis.clarification.cues`
  Phrase list used by `metrics.json` clarification subtyping for detected revisions.

Guidance:

* edit `messages:` only when you are changing user-facing CLI, help, or runtime text
* add domain-specific phrases, dialects, or informal wording directly in YAML
* prefer small, conservative phrase lists to avoid obvious false positives
* if your logs use organization-specific language, tune the YAML first before changing code
* locale-specific behavior falls back to `en-US` when a section or key is missing
* revision heuristics ignore very short user messages before cue or similarity matching

This is the intended customization path for phrase-based heuristic tuning.

---

## Security & Privacy

* offline-first for parse, export, and most analyzer workflows
* no telemetry
* sensitive logs stay local
* deterministic output for audits
* `extract` sanitization is config-driven and enabled by default for compatibility
* `extract.meta.json` records whether sanitization was enabled, which scope ran, which replacement token was used, and whether custom keywords or patterns were supplied
* `analyze tokens` and `analyze metrics` are generally local, but `tiktoken` may fetch encoding data once on first use and then use the local cache afterward

---

## Dependencies & Credits

Current analyze and tokenizer work relies mainly on:

* Python standard library utilities for deterministic analysis and heuristics
* [`tiktoken`](https://github.com/openai/tiktoken) for tokenizer-based analysis

Phrase resources for refusal and revision heuristics live in project YAML files under `src/llm_logparser/i18n/` and are intended to be user-tunable.

---

## Roadmap

- [x] CLI MVP (parse / export / extract / chain / analyze)
- [x] Markdown exporter with thread splitting
- [x] JSON Schema validation
- [x] Config file loading (auto-discovery + profiles)
- [x] Analyzer stats / timeline / tokens / metrics

Near term:

- [ ] Anthropic / Claude support
- [ ] xAI / Grok support
- [ ] VS Code Extension for browsing normalized logs

Later / exploratory:

- [ ] Gemini support (format under evaluation)
- [ ] GUI applications

---

## Contributing

PRs are welcome.

Good places to start:

* adapters
* exporter improvements
* localization

Principles:

* deterministic core
* provider-specific behavior lives in adapters
* offline by default

Run the test suite locally with:

```bash
uv run pytest
```

---

## License

MIT - simple and permissive.

---

## Author

```text
The words you weave are not mere echoes;
they carry weight,
and may they never be lost to the tide of time.
```

© 2025 **Ashes Division - Reyz Laboratory**
