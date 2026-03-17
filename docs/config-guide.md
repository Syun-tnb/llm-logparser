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

Environment variables still matter for config discovery (`LLM_LOGPARSER_CONFIG`) and a
few global fallbacks such as log level and locale. They are not a general config layer.

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

Controls how dates and messages are formatted:

```yaml
locale: ja-JP
timezone: Asia/Tokyo
```

Missing translations automatically fall back to `en-US`.

---

## Troubleshooting tips

* A config value not applying? → check CLI flags first, then the selected profile
* Unexpected timestamps? → verify `timezone`
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
