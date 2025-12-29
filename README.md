# llm-logparser

**Convert full LLM export dumps into clean, human-readable Markdown — offline-first, deterministic, CLI-centric.**

`llm-logparser` parses conversation logs (JSON / JSONL / NDJSON),
normalizes them into thread records, and exports **GitHub-Flavored Markdown** with metadata —
built for reproducibility, audits, archiving, and migration.

No cloud. No telemetry. Your data stays local.

---

## ✨ What it does

* **Parse → Normalize → Export (Markdown)**
* **Thread-based layout** with YAML front-matter
* **Automatic splitting** (size / count / auto)
* **Localized timestamps** (locale + timezone support)
* **Chain mode**: parse & export in one command
* **Deterministic, offline workflows**
* **Future-proof architecture** (multi-provider adapters)

> MVP currently focuses on **OpenAI logs**.
> Providers like Claude / Gemini are planned.

---

## 🚀 Quick Start

Install (local dev):

```bash
pip install -e .
```

Parse an export:

```bash
llm-logparser parse \
  --provider openai \
  --input examples/messages.jsonl \
  --outdir artifacts
```

Export a parsed thread to Markdown:

```bash
llm-logparser export \
  --input artifacts/output/openai/thread-abc123/parsed.jsonl \
  --timezone Asia/Tokyo \
  --formatting light
```

End-to-end (parse → export everything):

```bash
llm-logparser chain \
  --provider openai \
  --input examples/messages.jsonl \
  --outdir artifacts \
  --timezone Asia/Tokyo
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
checksum: "<sha1>"
---
```

Messages follow in timestamp order:

```markdown
## [User] 2025-10-18 10:00
こんにちは！

## [Assistant] 2025-10-18 10:01
こんにちは — どうお手伝いしましょう？
```

Markdown is **GFM-compatible** and preserves:

* fenced code blocks
* links
* tables
* quotes

---

## 🌍 Localization

```
--locale   en-US | ja-JP (etc.)
--timezone Asia/Tokyo | UTC | ...
```

* Rendering uses localized date formats
* Internals remain **UTC** (ISO-8601)
* Missing keys fall back to `en-US`

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
llm-logparser parse \
  --provider openai \
  --input <file> \
  --outdir artifacts \
  [--dry-run] [--fail-fast]
```

### Export

```bash
llm-logparser export \
  --input parsed.jsonl \
  [--out <md>] \
  [--split auto|size=N|count=N] \
  [--timezone <IANA>] \
  [--formatting none|light]
```

### Chain

```bash
llm-logparser chain \
  --provider openai \
  --input <raw> \
  --outdir artifacts \
  [other export options...]
```

---

## 🔒 Security & Privacy

* Offline-first
* No telemetry
* Sensitive logs stay local
* Deterministic output for audits

---

## 🗺 Roadmap

* [x] CLI MVP (parse/export/chain)
* [ ] Minimal HTML viewer
* [ ] Additional providers (Claude / Gemini / …)
* [ ] Apps SDK integration (experimental)
* [ ] GUI (later stage)

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

---

## 📄 License

MIT — simple and permissive.

---

## Author

> "The words you weave are not mere echoes;  
> they carry weight,  
> and may they never be lost to the tide of time."

© 2025 **Ashes Division — Reyz Laboratory**  
