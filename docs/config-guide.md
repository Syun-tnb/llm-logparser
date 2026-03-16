# Config Guide (YAML/JSON)

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

Multiple sources may define values.  
Priority order:

1️⃣ CLI arguments  
2️⃣ environment variables  
3️⃣ user config file  
4️⃣ built-in defaults  

> If something behaves differently than expected, first check **which layer won**.

---

## Files & locations (MVP)

Supported configuration files:

| Location | Purpose |
|----------|---------|
| `config.yaml` | project-local defaults |
| `~/.llm-logparser/config.yaml` | per-user defaults |

JSON equivalents are also supported.

> The CLI discovers one config file (`--config`, env var, local, parent, then home) and
> applies profile values only when the corresponding CLI flags are missing.

---

## Minimal Example (YAML)

```yaml
active_profile: default

profiles:
  default:
    provider: openai
    timezone: Asia/Tokyo
    locale: en-US

    input:
      path: exports/messages.json

    parse:
      outdir: artifacts
      validate_schema: true

    output:
      path: artifacts/thread.md
      formatting: light
      split: auto
```

The final value for each setting is resolved as:

```
CLI args > environment variables > user profile > defaults
```

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

### `output`

Controls export defaults such as output path, formatting, and split mode.

Examples:

```yaml
output:
  formatting: light
  split: auto
```

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

* A config value not applying? → check **priority order**  
* Unexpected timestamps? → verify `timezone`
* A provider mapping YAML not taking effect? → that is expected today; runtime parsing is adapter-based
* Output too fragmented? → adjust `output.split` or pass `--split` explicitly

---

## Summary

Configuration lets you adapt `llm-logparser` to:

* personal workflows
* corporate environments
* different default CLI profiles

— **without touching the code.**

For provider-specific field remapping, treat the YAML files under `docs/examples/providers/`
as documentation/examples only until runtime wiring is implemented.
