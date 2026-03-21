# llm-logparser

[![Sponsor](https://img.shields.io/github/sponsors/Syun-tnb)](https://github.com/sponsors/Syun-tnb)

**Convert full LLM export dumps into clean, human-readable Markdown — offline-first, deterministic, CLI-centric.**

`llm-logparser` parses conversation logs (JSON / JSONL / NDJSON),
normalizes them into thread records, and exports **GitHub-Flavored Markdown** with metadata —
built for reproducibility, audits, archiving, and migration.

No cloud. No telemetry. Your data stays local.

When you use tokenizer-based analysis, note one runtime caveat:
`tiktoken` may fetch encoding assets on first use, then caches them locally for later runs.
Token counting is otherwise local and deterministic.

---

## ✨ What it does

* **Parse → Normalize → JSONL → Export (Markdown)**
* **Thread-based layout** with YAML front-matter
* **Automatic splitting** (size / count / auto)
* **Localized CLI/help/runtime messages**
* **Timezone-aware Markdown timestamps**
* **Chain mode**: parse & export in one command
* **Analyze stats / timeline** from canonical `parsed.jsonl`
* **Analyze tokens**: deterministic per-thread `token_stats.json`
* **Analyze metrics**: deterministic `metrics.json` with refusal/revision heuristics
* **Analyze sqlite-build**: optional SQLite index built from canonical artifacts
* **Locale-tunable heuristic phrases** via YAML resources
* **Deterministic, local-first workflows**
* **Future-proof architecture** (multi-provider adapters)

> MVP currently focuses on **OpenAI logs**.
> Providers like Claude / Gemini are planned.

---

## 🧱 Canonical Data Model

The parser normalizes provider-specific exports into a stable JSONL schema.

That JSONL is the canonical intermediate format for the project.

Downstream features consume that format:

- Markdown export
- HTML / GUI viewers
- analyzers
- future applications

Parser responsibilities end at deterministic JSONL generation.
Presentation, export formatting, and analysis are downstream concerns handled separately.

---

## 🚀 Quick Start

Install [`uv`](https://docs.astral.sh/uv/getting-started/installation/) and sync the project environment:

```bash
uv sync
uv sync --extra dev
```

Parse an export:

```bash
uv run llm-logparser parse \
  --provider openai \
  --input examples/messages.jsonl \
  --outdir artifacts
```

Export a parsed thread to Markdown:

```bash
uv run llm-logparser export \
  --input artifacts/output/openai/thread-abc123/parsed.jsonl \
  --timezone Asia/Tokyo \
  --formatting light
```

End-to-end (parse → export everything):

```bash
uv run llm-logparser chain \
  --provider openai \
  --input examples/messages.jsonl \
  --outdir artifacts \
  --timezone Asia/Tokyo
```

Analyze canonical parsed threads:

```bash
uv run llm-logparser analyze stats \
  --input artifacts/output/openai
```

These subcommands intentionally produce different output classes: `stats` / `timeline` render results, `tokens` / `metrics` write per-thread JSON sidecars, and `sqlite-build` writes a single SQLite database artifact.

Recommended sidecar workflow after parse: run `analyze tokens` first, then `analyze metrics`.

Build per-thread token sidecars first:

```bash
uv run llm-logparser analyze tokens \
  --input artifacts/output/openai
```

Preview sidecar generation before writing:

```bash
uv run llm-logparser analyze tokens \
  --input artifacts/output/openai \
  --dry-run
```

Then build per-thread metrics sidecars from `parsed.jsonl` plus `token_stats.json`:

```bash
uv run llm-logparser analyze metrics \
  --input artifacts/output/openai
```

---

## 📁 Directory Layout

```
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

> Pass **only the root** via `--outdir`.
> The tool creates `output/<provider>/...` automatically.

---

## 📝 Markdown Format (Overview)

Each file begins with YAML front-matter:

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
Good morning — how can I help today?
```

Markdown is **GFM-compatible** and preserves:

* fenced code blocks
* links
* tables
* quotes

---

## 🌍 Localization

`llm-logparser` uses a best-effort i18n model. Locale files are optional,
user-extensible YAML resources, and missing keys are expected to fall back safely
rather than block execution.

You can control output formatting using:

```
--locale   en-US | ja-JP | …
--timezone Asia/Tokyo | UTC | …
```

Locale files live under `src/llm_logparser/i18n/*.yaml` and may contain:

* `messages:` for scalar CLI/help/runtime/error text
* `analysis:` for structured analyzer phrase resources

Localized:

* CLI/help/runtime messages from `messages:`
* analyzer heuristic phrase resources from `analysis:`

Not localized by design:

* `analyze stats` and `analyze timeline` text summaries
* JSON artifacts and stable schema keys
* argparse built-ins such as `usage:` and parser-generated boilerplate
* Markdown timestamp formatting beyond timezone conversion

Locale precedence is:

1. CLI `--locale` / `--lang`
2. environment variable `LLP_LOCALE`
3. selected profile locale `profiles.<name>.locale` (when applicable)
4. `en-US`

Note:

not all commands fully honor profile-level locale yet; CLI and environment settings take precedence.

Behavior notes:

* parser/help output can pick up CLI locale early via raw argv scanning
* unknown locales resolve to `en-US`
* missing message keys fall back to `en-US`, then to the raw key if still missing
* analyzer resource keys fall back to `en-US`
* short aliases such as `en` and `ja` are auto-derived from locale filenames when the language prefix is unambiguous
* if multiple locale files share a language prefix, use the full locale tag

Analyzer heuristics use locale-backed YAML resources under `analysis:`.
Current analyzer-tunable keys include:

* `analysis.refusal.indicators`
* `analysis.revision.cues`

If a selected locale does not provide one of these keys, the analyzer falls back to `en-US`.

Current limitations:

* no top-level config `locale` yet
* no argparse built-in localization (`usage:`, parser-generated errors, built-in help boilerplate)
* no system-locale fallback

For the project-wide i18n model and boundaries, see `docs/requirements.md`
and `docs/config-guide.md`.

Example:

```bash
uv run llm-logparser export \
  --input parsed.jsonl \
  --locale ja-JP \
  --timezone Asia/Tokyo
```

---

## 🪓 Splitting

```
--split size=4M
--split count=1500
--split auto     # size=4M + count=1500
```

Extra tuning:

```
--split-soft-overflow 0.20
--split-hard
--tiny-tail-threshold 20
```

---

## 🔗 Chain Mode

Runs **parse → export** in one flow:

```
--parsed-root       reuse existing parsed threads
--export-outdir     place Markdown elsewhere
--dry-run           parse only (no writes)
--fail-fast         stop on first export error
```

---

## 🛠 CLI Reference (MVP)

### Parse

```bash
uv run llm-logparser parse \
  --provider openai \
  --input <file> \
  --outdir artifacts \
  [--dry-run] [--fail-fast] \
  [--validate-schema]
```

### Export

```bash
uv run llm-logparser export \
  --input parsed.jsonl \
  [--out <md>] \
  [--split auto|size=N|count=N] \
  [--timezone <IANA>] \
  [--formatting none|light]
```

### Extract

Extract a single conversation as Gemini-compatible JSON (with PII masking):

```bash
uv run llm-logparser extract \
  --provider openai \
  --input <file> \
  --conversation-id <id> \
  --outdir artifacts \
  [--dry-run]
```

### Chain

```bash
uv run llm-logparser chain \
  --provider openai \
  --input <raw> \
  --outdir artifacts \
  [--validate-schema] \
  [other export options...]
```

### Analyze Stats

Compute deterministic thread/message statistics from canonical `parsed.jsonl` files:

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
* `--encoding` overrides provider/model resolution

Runtime caveat:

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

* ratio / token / character / distribution / diversity metrics
* `safety.refusal`
* `interaction.revision` with `correction`, `clarification`, and `retry` subtype counts

`metrics.json` requires `token_stats.json` to exist next to each `parsed.jsonl`.
Existing `metrics.json` sidecars are rebuilt by default; `--skip-existing` only fills in missing sidecars.
Use `--dry-run` to preview sidecar generation before writing.

### Analyze SQLite Build

Build an optional per-provider SQLite analysis index from canonical thread artifacts:

```bash
uv run llm-logparser analyze sqlite-build \
  --input <provider-artifact-root> \
  --provider <provider-id> \
  [--overwrite]
```

---

## 📊 Analyzer Outputs

Current analyze-layer sidecars:

* `token_stats.json`
  Deterministic per-thread token counts derived from canonical message text.
  Includes tokenizer metadata, per-role token totals, and per-message token counts.

* `metrics.json`
  Deterministic per-thread research-oriented metrics derived from `parsed.jsonl`
  plus `token_stats.json`. Includes ratio/token/character/distribution/diversity
  metrics together with heuristic `safety.refusal` and `interaction.revision`.

Both artifacts are rebuildable from canonical data and contain no runtime timestamps.

---

## 🧩 YAML Customization

Locale data is YAML-driven. Locale files under `src/llm_logparser/i18n/` are
best-effort extensions, not strict contracts: partial files are acceptable and
fallback to `en-US` is normal behavior.

Scalar CLI/help/runtime messages live under `messages:`, and analyzer phrase
tuning lives under `analysis:`.

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

* edit `messages:` only when you are changing user-facing CLI/help/runtime text
* add domain-specific phrases, dialects, or informal wording directly in YAML
* prefer small, conservative phrase lists to avoid obvious false positives
* if your logs use organization-specific language, tune the YAML first before changing code
* locale-specific behavior falls back to `en-US` when a section or key is missing
* revision heuristics also ignore very short user messages before cue/similarity matching

This is the intended customization path for phrase-based heuristic tuning.

---

## ⚙️ Configuration (`config.yaml`)

`llm-logparser` supports optional runtime defaults via YAML `config.yaml`.
CLI flags always take precedence. Profile values are only used to fill in missing options.

External provider mapping YAML is not used at runtime yet.
Current normalization is adapter-based under `src/llm_logparser/core/providers/`.

### 🔎 Config Discovery Order

When no `--config` flag is provided, the tool searches in the following order:

1. Explicit `--config <path>`
2. Environment variable: `LLM_LOGPARSER_CONFIG=<path>`
3. `config.yaml` in the current directory
4. The nearest parent directory containing `config.yaml`
5. `~/.config/llm-logparser/config.yaml` (if applicable)

If no configuration file is found, the CLI behaves normally.

---

### 👤 Profiles

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

```
--profile > active_profile > the only defined profile
```

Value precedence for supported config-backed options:

```
CLI flags > selected profile values > built-in CLI defaults
```

The section-based shape is canonical. Older profile-level compatibility keys such as
`outdir`, `dry_run`, `fail_fast`, `validate_schema`, `export_outdir`, `parsed_root`,
and `conversation_id` are still accepted for `schema_version: 1`, but the loader now
warns and points to the section-based replacement keys. That compatibility is intended
for removal in a future schema-version-2 cleanup.

If multiple `input.paths` are defined and no explicit `--input` is provided:

* In interactive mode, you will be prompted.
* In non-interactive mode, the program exits with code `2`.

If multiple profiles exist and neither `--profile` nor `active_profile` selects one:

* In interactive mode, you will be prompted to choose a profile.
* In non-interactive mode, no profile defaults are applied.

---

### 📂 Relative Path Resolution

Relative paths defined in `config.yaml` are resolved against
the directory where the discovered `config.yaml` resides.

This ensures stable behavior when using:

```bash
LLM_LOGPARSER_CONFIG=/etc/llm/config.yaml
```

and avoids unintended CWD-dependent path resolution.

---

### 🔧 Config Subcommands

Use the lightweight inspection helpers to debug config resolution:

```bash
uv run llm-logparser config path
uv run llm-logparser config show [--profile work]
uv run llm-logparser config validate
```

`config show` prints the normalized selected profile when one is resolved.
Otherwise it prints the normalized full config structure.

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
sanitization stays enabled, sensitive field names are redacted, and the built-in
email/phone patterns are applied to `content.parts`.

---

### 🛑 Non-Interactive Mode

You can disable prompts using:

```bash
--non-interactive
```

or:

```bash
LLM_LOGPARSER_NON_INTERACTIVE=1
```

In non-interactive mode, the program exits with code `2` if:

* Required options are missing
* Multiple input candidates are ambiguous

This makes the CLI safe for CI and automation workflows.

---

## 🔒 Security & Privacy

* Offline-first for parse/export and most analyzer workflows
* No telemetry
* Sensitive logs stay local
* Deterministic output for audits
* `extract` sanitization is config-driven and enabled by default for compatibility
* `extract.meta.json` records whether sanitization was enabled, which scope ran,
  which replacement token was used, and whether custom keywords/patterns were supplied
* `analyze tokens` / `analyze metrics` are generally local, but `tiktoken` may fetch
  encoding data once on first use and then use the local cache afterward

---

## 📦 Dependencies & Credits

Current analyze/tokenizer work relies mainly on:

* Python standard library utilities for deterministic analysis and heuristics
* [`tiktoken`](https://github.com/openai/tiktoken) for tokenizer-based analysis

Phrase resources for refusal/revision heuristics live in project YAML files under
`src/llm_logparser/i18n/` and are intended to be user-tunable.

---

## 🗺 Roadmap

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

## 🤝 Contributing

PRs welcome!
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

## 📄 License

MIT — simple and permissive.

---

## Author

> "The words you weave are not mere echoes;  
> they carry weight,  
> and may they never be lost to the tide of time."

© 2025 **Ashes Division — Reyz Laboratory**  
