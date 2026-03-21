# llm-logparser

[![Sponsor](https://img.shields.io/github/sponsors/Syun-tnb)](https://github.com/sponsors/Syun-tnb)

`llm-logparser` turns raw LLM export dumps into deterministic, local artifacts you can inspect, archive, analyze, and share. It parses provider-specific JSON into canonical thread-oriented JSONL, then exports readable GitHub-Flavored Markdown and optional analysis sidecars.

Built for offline-first workflows, reproducibility, audits, and migration. No cloud. No telemetry. Your data stays local.

> Runtime caveat for token analysis: `tiktoken` may fetch encoding assets on first use, then caches them locally. Token counting is otherwise local and deterministic.

---

## Installation

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

The shortest end-to-end path is `chain`, which runs parse then export in one command:

```bash
llm-logparser chain \
  --provider openai \
  --input path/to/export.json \
  --outdir artifacts \
  --timezone Asia/Tokyo
```

This creates canonical parsed artifacts plus Markdown output under:

```text
artifacts/output/<provider>/
```

If you want to inspect the canonical parsed threads afterward:

```bash
llm-logparser analyze stats \
  --input artifacts/output/openai
```

---

## What it does

`llm-logparser` is designed around a simple pipeline:

* parse provider exports into canonical JSONL thread records
* export those records to readable Markdown
* analyze canonical artifacts without re-parsing raw exports

At a glance:

* Parse -> Normalize -> JSONL -> Export (Markdown)
* Thread-based layout with YAML front matter
* Automatic splitting by size, count, or auto mode
* Localized CLI, help, and runtime messages
* Timezone-aware Markdown timestamps
* `chain` mode for parse + export in one command
* `analyze stats` and `analyze timeline` for reporting from canonical `parsed.jsonl`
* `analyze tokens` for deterministic per-thread `token_stats.json`
* `analyze metrics` for deterministic `metrics.json` with refusal and revision heuristics
* `analyze sqlite-build` for an optional SQLite index built from canonical artifacts
* Locale-tunable heuristic phrases via YAML resources
* Deterministic, local-first workflows
* Future-proof architecture for multiple providers

> MVP currently focuses on OpenAI logs. Providers like Claude and Gemini remain planned areas of expansion.

---

## Canonical Data Model

The parser normalizes provider-specific exports into a stable JSONL schema. That JSONL is the canonical intermediate format for the project.

Downstream features consume that canonical layer:

* Markdown export
* HTML or GUI viewers
* analyzers
* future applications

Parser responsibilities end at deterministic JSONL generation. Presentation, export formatting, and analysis are downstream concerns handled separately.

---

## Directory Layout

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

## Markdown Format (Overview)

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

## Localization

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

* `analyze stats` and `analyze timeline` text summaries
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

Analyzer heuristics use locale-backed YAML resources under `analysis:`.

Current analyzer-tunable keys include:

* `analysis.refusal.indicators`
* `analysis.revision.cues`

If a selected locale does not provide one of these keys, the analyzer falls back to `en-US`.

Current limitations:

* no top-level config `locale` yet
* no argparse built-in localization for `usage:`, parser-generated errors, or built-in help boilerplate
* no system-locale fallback

For the project-wide i18n model and boundaries, see `docs/requirements.md` and `docs/config-guide.md`.

Example:

```bash
uv run llm-logparser export \
  --input parsed.jsonl \
  --locale ja-JP \
  --timezone Asia/Tokyo
```

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

## Chain Mode

`chain` runs parse then export in one flow.

Useful options:

```text
--parsed-root       reuse existing parsed threads
--export-outdir     place Markdown elsewhere
--dry-run           parse only (no writes)
--fail-fast         stop on first export error
```

---

## CLI Reference (MVP)

The sections below use `uv run` because they are also convenient when working from a source checkout. If you installed from PyPI, remove the `uv run` prefix.

### Parse

Normalize raw provider exports into canonical thread artifacts:

```bash
uv run llm-logparser parse \
  --provider openai \
  --input <file> \
  --outdir artifacts \
  [--dry-run] [--fail-fast] \
  [--validate-schema]
```

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

Parse and export in a single command:

```bash
uv run llm-logparser chain \
  --provider openai \
  --input <raw> \
  --outdir artifacts \
  [--validate-schema] \
  [other export options...]
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
* `interaction.revision` with `correction`, `clarification`, and `retry` subtype counts

`metrics.json` requires `token_stats.json` to exist next to each `parsed.jsonl`.

Additional behavior notes:

* existing `metrics.json` sidecars are rebuilt by default
* `--skip-existing` only fills in missing sidecars
* `--dry-run` previews sidecar generation before writing

### Analyze SQLite Build

Build an optional per-provider SQLite analysis index from canonical thread artifacts:

```bash
uv run llm-logparser analyze sqlite-build \
  --input <provider-artifact-root> \
  --provider <provider-id> \
  [--overwrite]
```

---

## Analyzer Outputs

Current analyze-layer sidecars:

* `token_stats.json`
  Deterministic per-thread token counts derived from canonical message text. Includes tokenizer metadata, per-role token totals, and per-message token counts.

* `metrics.json`
  Deterministic per-thread research-oriented metrics derived from `parsed.jsonl` plus `token_stats.json`. Includes ratio, token, character, distribution, and diversity metrics together with heuristic `safety.refusal` and `interaction.revision`.

Both artifacts are rebuildable from canonical data and contain no runtime timestamps.

Recommended sidecar workflow after parse:

1. run `analyze tokens`
2. run `analyze metrics`

Examples:

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

These subcommands intentionally produce different output classes:

* `stats` and `timeline` render results
* `tokens` and `metrics` write per-thread JSON sidecars
* `sqlite-build` writes a single SQLite database artifact

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

## Configuration (`config.yaml`)

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

> "The words you weave are not mere echoes;
> they carry weight,
> and may they never be lost to the tide of time."

© 2025 **Ashes Division - Reyz Laboratory**
