# Analyzer Architecture

The `analyze` subsystem provides analysis capabilities on top of the
canonical JSONL conversation format produced by the parser.

Its purpose is **not** to replace the parser or exporter, but to build
additional insight layers on top of the normalized conversation data.

The design follows a **layered analysis model**, where each layer adds
capability while preserving deterministic and offline-first behavior
whenever possible.

---

# Philosophy

The analyzer is designed around three core principles:

- **Canonical input**  
  All analysis operates on normalized JSONL threads produced by the parser.

- **Deterministic first**  
  Analyses that do not require AI models should remain deterministic and reproducible.

- **Progressive capability**  
  More powerful analysis layers exist above deterministic ones but remain optional.

This allows users to run the analyzer in environments ranging from:

- fully offline
- local-LLM only
- hybrid (local + API)
- API-driven workflows

---

# Layered Analysis Model

The analyzer is structured into four conceptual layers.

```
L1  Text / Schema Analysis
L2  Indexed Analysis (SQLite)
L3  Local LLM Analysis
L4  Frontier LLM Analysis (API)
```

Each layer builds on the same canonical JSONL conversation structure.

---

# Layer 1 — Deterministic Text Analysis

This layer performs analysis using only the normalized JSONL data.

No databases, no AI models.

These analyses are fast, reproducible, and safe to run in any environment.

Typical metrics include:

- message counts
- character counts
- role distribution
- thread length
- conversation duration
- inter-message timing
- provider statistics
- schema validation

Example outputs:

```
messages: 42
user_messages: 21
assistant_messages: 21
characters_total: 18345
conversation_duration: 2h14m
```

This layer should remain:

- deterministic
- fast
- dependency-light

---

# Layer 2 — Indexed Analysis (SQLite)

Some analyses require efficient querying across many conversations.

For these cases, the analyzer can optionally load conversation data
into a local SQLite database.

The database acts as an **analysis index**, not as the canonical storage.

Canonical data remains JSONL.

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

SQLite databases may be generated during parsing or imported later.

---

# Layer 3 — Local LLM Analysis

This layer uses **local language models** to perform lightweight semantic analysis.

Typical tasks include:

- conversation summarization
- topic extraction
- tag generation
- classification
- TODO or task extraction
- clustering conversations

Local models may include:

- llama.cpp
- Ollama
- local transformers
- any compatible inference backend

Because these models run locally, this layer remains privacy-friendly
and compatible with offline workflows.

Example tasks:

```
summarize thread
extract key topics
generate tags
```

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

---

# CLI Concept

The analyzer is exposed via the `analyze` command.

Example conceptual structure:

```
llm-logparser analyze stats ...
llm-logparser analyze index ...
llm-logparser analyze local ...
llm-logparser analyze llm ...
```

Possible modes:

| Mode | Layer |
|-----|------|
| stats | L1 |
| index | L2 |
| local | L3 |
| llm | L4 |

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

- token statistics and cost estimation
- conversation similarity search
- semantic indexing
- embeddings-based analysis
- dataset generation
- research tooling

These features will remain layered on top of the canonical JSONL model.

---

# Summary

The analyzer transforms normalized conversation logs into
useful insights through a progressive analysis stack:

```
JSONL → deterministic analysis → indexed analysis → local AI → frontier AI
```

This design preserves the core philosophy of the project:

- deterministic where possible
- powerful where needed
- always built on canonical normalized data
