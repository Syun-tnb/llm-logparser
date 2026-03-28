# Config Guide (YAML)

Define runtime defaults for the CLI without code changes.

> [!IMPORTANT]
> **Current status:** `config.yaml` loading is implemented for CLI/profile defaults.
> External provider mapping YAML is **not** loaded at runtime yet.
> Provider normalization still happens in Python adapters under `src/llm_logparser/core/providers/`.

---

* define profile defaults for CLI options
* tune export splitting behavior
* set default locale / timezone preferences
* keep environment-specific settings out of git

This guide does **not** replace the CLI reference.  
Think of it as:

> “How to set default CLI behavior for your environment.”

---

## How configuration is applied

For config-backed command options, the precedence is:

1️⃣ CLI arguments  
2️⃣ selected profile values  
3️⃣ built-in CLI defaults  

Environment variables still matter for config discovery (`LLM_LOGPARSER_CONFIG`) and
locale resolution (`LLP_LOCALE`). They are not a general config layer.

Locale has one extra rule beyond normal option defaults:

1️⃣ CLI `--locale` / `--lang`  
2️⃣ `LLP_LOCALE`  
3️⃣ selected profile `locale`  
4️⃣ `en-US`

---

## Files & locations (MVP)

Supported configuration files:

| Location | Purpose |
|----------|---------|
| `config.yaml` | project-local defaults |
| `~/.config/llm-logparser/config.yaml` | per-user defaults |

> The CLI discovers one config file (`--config`, env var, local, parent, then home) and
> applies profile values only when the corresponding CLI flags are missing.

---

## Minimal Example (YAML)

```yaml
schema_version: 1
active_profile: default

profiles:
  default:
    provider: openai
    timezone: Asia/Tokyo
    locale: en-US

    input:
      path: exports/messages.json

    sanitize:
      enabled: true
      replacement: REDACTED
      scope: content_parts

    parse:
      outdir: artifacts
      validate_schema: true

    output:
      path: artifacts/thread.md
      formatting: light
      split: auto
```

Profile selection is resolved as:

```
--profile > active_profile > the only defined profile
```

If multiple profiles exist and none is selected automatically:

* interactive commands prompt for a profile
* non-interactive commands continue without profile defaults

---

## Sections explained

### Provider mapping YAML

External provider field-mapping YAML is not active runtime config yet.

Files such as:

* `docs/examples/providers/openai/chatgpt.yaml`
* `docs/examples/providers/anthropic/claude.yaml`
* `docs/examples/providers/xai/grok.yaml`

are documentation/examples for a future external mapping system.
Today, the parser ignores those files and uses provider adapters instead.

---

### `input`

`input` is shared across the config-aware commands:

```yaml
input:
  path: exports/messages.json
  parsed: artifacts/output/openai/thread-123/parsed.jsonl
```

* `input.path` / `input.paths` are used by `parse`, `chain`, and `extract`
* `input.parsed` is used by `export`

### `output`

Controls export defaults such as output path, formatting, and split mode.

Examples:

```yaml
output:
  path: artifacts/thread.md
  formatting: light
  split: auto
```

`output.path` is the canonical export file path key. `output.dir` is not supported.

### `parse` / `chain` / `extract`

Command-specific defaults live under their matching command names:

```yaml
parse:
  outdir: artifacts
  validate_schema: true

chain:
  outdir: artifacts
  export_outdir: artifacts/markdown

extract:
  outdir: artifacts
  conversation_id: conv-123
```

For backward compatibility, older profile-level keys such as `outdir`, `dry_run`,
`fail_fast`, and `validate_schema` are still accepted and normalized into these sections.
New configs should prefer the command-specific shape.

Legacy profile-level keys currently accepted for `schema_version: 1` compatibility:

* `outdir` → `parse.outdir`, `chain.outdir`, `extract.outdir`
* `dry_run` → `parse.dry_run`, `chain.dry_run`, `extract.dry_run`
* `fail_fast` → `parse.fail_fast`, `chain.fail_fast`
* `validate_schema` → `parse.validate_schema`, `chain.validate_schema`
* `export_outdir` → `chain.export_outdir`
* `parsed_root` → `chain.parsed_root`
* `conversation_id` → `extract.conversation_id`

The loader emits deprecation warnings when these legacy keys are used. They remain
supported for schema-version-1 compatibility only and are intended for removal in a
future schema-version-2 cleanup.

### `sanitize`

`sanitize` controls `extract` redaction behavior:

```yaml
sanitize:
  enabled: true
  replacement: REDACTED
  scope: content_parts
  extra_keywords: [credential]
  mask_patterns:
    - acct-\d+
```

Supported `scope` values:

* `content_parts`: sanitize only message `content.parts` and similar extracted text fields
* `all_strings`: sanitize every traversed string value in the extracted payload

When sanitization is enabled, fields whose key names match the built-in sensitive
keywords are also redacted. `extra_keywords` extends that field-name matcher.

Default behavior when `sanitize` is omitted:

* sanitization stays enabled for `extract`
* replacement defaults to `REDACTED`
* scope defaults to `content_parts`
* built-in email and phone regexes are applied

If `mask_patterns` is provided, it becomes the active regex list for string masking.
Use `mask_patterns: []` to disable regex-based string masking while keeping
field-name redaction enabled.

Each successful `extract` run also writes `extract.meta.json` with a safe summary of
the applied sanitize policy.

---

### `locale` / `timezone`

`profiles.<name>.locale` is the current config-backed locale source:

```yaml
locale: ja-JP
timezone: Asia/Tokyo
```

Project-wide i18n is best-effort and non-blocking. Locale files are optional
extensions, missing keys are expected to fall back safely, and stable
machine-readable artifacts remain English. See `docs/requirements.md` for the
canonical project-wide scope and boundaries.

Current locale behavior:

* locale files are discovered from `src/llm_logparser/i18n/*.yaml`
* a locale file may contain `messages:` and/or `analysis:` at the top level
* missing sections and missing keys are allowed; fallback behavior is expected
* scalar CLI/help/runtime/error messages are loaded from `messages:` in `src/llm_logparser/i18n/{locale}.yaml`
* analyzer phrase resources are loaded from `analysis:` in the same locale files
* missing message keys fall back to `en-US`, then to the raw key if still missing there
* unknown locales resolve to `en-US`
* short language aliases such as `en` or `ja` are derived automatically when one locale file unambiguously owns that language prefix
* if multiple locale files share the same language prefix, use the full locale tag
* profile locale is applied only after config/profile resolution
* profile locale never overrides CLI `--locale` / `--lang` or `LLP_LOCALE`

Current command scope:

* `parse`, `export`, `chain`, and `extract` use broader profile-backed CLI defaults
* `analyze semantic-prototype` supports profile-backed defaults for its input path and embedding settings
* other `analyze` modes currently share locale resolution only; their command-specific options should still be passed explicitly
* `profiles.<name>.locale` can still affect `analyze` because locale resolution is shared across runtime commands
* `config` can apply profile locale after config/profile resolution

Analyzer text output policy:

* `analyze stats` and `analyze timeline` text summaries are intentionally English-only
* `--json` output is the primary machine-readable analyzer interface
* locale-backed analyzer resources under `analysis:` are for heuristic inputs, not artifact schema localization
* exported Markdown remains timezone-aware but not locale-formatted

---

## `analyze.semantic_prototype`

The experimental semantic prototype can read backend and embedding defaults from
config:

```yaml
analyze:
  semantic_prototype:
    backend: ollama
    model: embeddinggemma
    top_k: 5
    backend_options:
      base_url: http://localhost:11434
      timeout_seconds: 30.0
```

CLI flags still override config values. `input.path` remains the default source
path for `analyze semantic-prototype` when `--input` is omitted.

`backend` and `model` are separate concerns:

* `backend` selects the runtime binding implemented in code
* `model` selects the embedding model identifier used by that backend
* `backend_options` carries transport/runtime options such as Ollama base URL
* `embedding` carries chunking and aggregation controls

Safe built-in fallback embedding settings remain conservative when `embedding`
overrides are omitted:

* `max_input_bytes: 256`
* `chunk_overlap_bytes: 32`
* `aggregate: mean`

There is still a small compatibility fallback for a couple of historic Ollama
model IDs, but it is intentionally not the primary configuration surface.
Recommended tuning should be declared explicitly in config.

Example with explicit tuning:

```yaml
analyze:
  semantic_prototype:
    backend: ollama
    model: my-local-embedder
    top_k: 5
    embedding:
      max_input_bytes: 768
      chunk_overlap_bytes: 96
      aggregate: mean
```

Behavior notes:

* long window text is chunked automatically before embedding requests
* chunk sizing is deterministic UTF-8 byte-based sizing, not tokenizer-accurate token budgeting
* chunk embeddings are aggregated into one final embedding per original window
* outputs remain rebuildable, non-canonical prototype artifacts
* `deterministic-hash` remains the default backend for plumbing and tests
* the current built-in `min_score` default is `0.62`, selected from repeated
  real-data subset validation as the best current tradeoff between broad
  cross-thread noise and extra fragmentation

Current limitations:

* there is no top-level config `locale` yet
* argparse built-ins are not localized
* there is no system-locale fallback

`timezone` is a separate setting. In current config handling it is mainly useful for
`export` and `chain`, where it controls local timestamp rendering in Markdown output.
Those timestamps are timezone-aware, but not currently locale-formatted.

Analyzer phrase heuristics are not configured through `config.yaml` yet.
To tune refusal or revision phrase matching, edit the locale resource files directly:

* `src/llm_logparser/i18n/en-US.yaml`
* `src/llm_logparser/i18n/ja-JP.yaml`

Relevant keys:

* `messages.*` for CLI/help/runtime strings
* `analysis.refusal.indicators`
* `analysis.revision.cues`

This is the intended customization path for dialect-specific, informal, or
domain-specific wording.

---

## Troubleshooting tips

* A config value not applying? → check CLI flags first, then the selected profile
* Unexpected timestamps? → verify `timezone`
* Refusal or revision phrase matching feels off? → tune `src/llm_logparser/i18n/{locale}.yaml`
* A provider mapping YAML not taking effect? → that is expected today; runtime parsing is adapter-based
* Output too fragmented? → adjust `output.split` or pass `--split` explicitly
* Want to inspect what the CLI sees? → use `llm-logparser config path|show|validate`

---

## Summary

Configuration lets you adapt `llm-logparser` to:

* personal workflows
* corporate environments
* different default CLI profiles

— **without touching the code.**

For provider-specific field remapping, treat the YAML files under `docs/examples/providers/`
as documentation/examples only until runtime wiring is implemented.
