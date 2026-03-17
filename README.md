# llm-logparser

[![Sponsor](https://img.shields.io/github/sponsors/Syun-tnb)](https://github.com/sponsors/Syun-tnb)

**Convert full LLM export dumps into clean, human-readable Markdown — offline-first, deterministic, CLI-centric.**

`llm-logparser` parses conversation logs (JSON / JSONL / NDJSON),
normalizes them into thread records, and exports **GitHub-Flavored Markdown** with metadata —
built for reproducibility, audits, archiving, and migration.

No cloud. No telemetry. Your data stays local.

---

## ✨ What it does

* **Parse → Normalize → JSONL → Export (Markdown)**
* **Thread-based layout** with YAML front-matter
* **Automatic splitting** (size / count / auto)
* **Localized timestamps** (locale + timezone support)
* **Chain mode**: parse & export in one command
* **Analyze stats**: deterministic conversation counts from canonical parsed JSONL
* **Deterministic, offline workflows**
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

---

## 📁 Directory Layout

```
artifacts/
  output/
    openai/
      thread-<conversation_id>/
        parsed.jsonl
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
range: 2025-10-01 〜 2025-10-18
locale: "ja-JP"
timezone: "Asia/Tokyo"
updated: "2025-10-18T10:15:00Z"
# checksum: "<sha1>"  ← planned for future release
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

`llm-logparser` supports localized timestamps and messages.

You can control output formatting using:

```
--locale   en-US | ja-JP | …
--timezone Asia/Tokyo | UTC | …
```

* Dates in Markdown are rendered using the selected **locale**
* Internally, timestamps remain **UTC ISO-8601** for reproducibility
* Missing or unknown locales gracefully fall back to `en-US`
* `--locale` takes precedence when both `--locale` and `--lang` are supplied
  *(--lang exists for compatibility)*

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

* Offline-first
* No telemetry
* Sensitive logs stay local
* Deterministic output for audits
* `extract` sanitization is config-driven and enabled by default for compatibility
* `extract.meta.json` records whether sanitization was enabled, which scope ran,
  which replacement token was used, and whether custom keywords/patterns were supplied

---

## 🗺 Roadmap

- [x] CLI MVP (parse / export / extract / chain / analyze stats)
- [x] Markdown exporter with thread splitting
- [x] JSON Schema validation
- [x] Config file loading (auto-discovery + profiles)

Near term:
- [ ] Anthropic / Claude support
- [ ] xAI / Grok support
- [ ] Analyzer subcommand
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
