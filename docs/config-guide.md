# Config Guide (YAML/JSON)

Define field mappings, fallbacks, split policies, and runtime defaults without code changes.

---

## What this guide is (and when to use it)

`llm-logparser` can be customized **without modifying source code**.  
This guide explains the *configuration layer* — how YAML / JSON files change behavior at runtime.

Use this when you want to:

* remap fields from provider exports
* define fallback values or normalization rules
* tune export splitting behavior
* set default locale / timezone preferences
* keep environment-specific settings out of git

This guide does **not** replace the CLI reference.  
Think of it as:

> “How to teach the tool about your data and preferences.”

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

> The tool automatically loads both (user → project), then applies CLI overrides.

---

## Minimal Example (YAML)

```yaml
schema_version: 1

provider: openai

mapping:
  conversation_id: $.conversation_id
  message_id: $.message_id
  ts: $.create_time | epoch_ms
  author_role: $.author.role
  author_name: $.author.name | null
  model: $.metadata.model | null
  content: $.content

split:
  by: size
  size_mb: 20

locale:
  lang: en-US
  timezone: Asia/Tokyo
```

The final value for each setting is resolved as:

```
CLI args > environment variables > user profile > defaults
```

---

## Sections explained

### `mapping`

Defines how raw provider data becomes normalized fields.

You can:

* pull values using JSON paths
* convert timestamps
* insert `null` fallbacks
* normalize optional fields

Example:

```yaml
mapping:
  ts: $.create_time | epoch_ms
  author_name: $.author.name | null
```

---

### `split`

Controls how large threads are split during export.

Examples:

```yaml
split:
  by: size
  size_mb: 20
```

or by count:

```yaml
split:
  by: count
  count: 1500
```

---

### `locale`

Controls how dates and messages are formatted:

```yaml
locale:
  lang: ja-JP
  timezone: Asia/Tokyo
```

Missing translations automatically fall back to `en-US`.

---

## Troubleshooting tips

* A config value not applying? → check **priority order**  
* Unexpected timestamps? → verify `timezone`  
* Output too fragmented? → increase `split.size_mb` or switch to `count`

---

## Summary

Configuration lets you adapt `llm-logparser` to:

* different providers  
* personal workflows  
* corporate environments  

— **without touching the code.**

If your setup grows complex, prefer documenting it in `config.yaml` instead of stacking CLI flags.
