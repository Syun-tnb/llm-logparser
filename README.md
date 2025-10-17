# llm-logparser

**Every full export, deduplicated and clean Markdown — CLI-first, offline by design.**

LLM conversation log parser that converts full export dumps (JSON/JSONL/NDJSON) into **thread-based Markdown documents**.
Designed for **deterministic, local-only workflows** with minimal setup, future-proofed for multi-provider support and ChatGPT Apps SDK integration.

---

## Features

* **CLI MVP** – Parse → Deduplicate → Split → Export Markdown
* **Thread-based Markdown** – clean output with metadata headers
* **Lightweight HTML Viewer** – simple index + search bar (MVP scope)
* **Offline by default** – no telemetry, no hidden network calls
* **Multi-provider ready** – adapters via YAML/JSON config (OpenAI now, Claude/Gemini/etc. planned)
* **i18n/L10n support** – CLI messages, errors, and metadata
* **Structured error contract** – JSON error payloads with stable codes
* **Future-proof** – designed for Apps SDK, GUI, and extensions

---

## Quickstart

```bash
# 1) Install (local dev)
pip install -e .

# 2) Parse raw export → Markdown
llm-logparser parse \
  --provider openai \
  --input examples/messages.jsonl \
  --outdir artifacts/

# 3) Open minimal HTML Viewer
# artifacts/index.html can be opened in your browser
```
### Common pitfall: `--outdir`
Pass the **root** directory only (e.g., `--outdir artifacts`).  
Do **not** include the `output/` segment yourself.  
The tool will create `output/<provider>/...` under the root automatically.

**Correct**
```bash
llm-logparser export --input artifacts/messages-00001.jsonl --outdir artifacts
```

Example output (Markdown):

```markdown
---
thread: abc123
provider: openai
messages: 152
range: 2025-09-01 → 2025-09-07
---

## [2025-09-03 12:00] user
Hi there!

## [2025-09-03 12:01] assistant
Hello, world!
```

---

## CLI Options (MVP)

* `--input <path>` : input file(s) (`json`, `jsonl`, `ndjson`)
* `--outdir <dir>` : output directory (default `./artifacts/`)
* `--split-by {size,count,date,none}` : splitting strategy
* `--offline` : disable all networking (default: ON)
* `--provider <id>` : provider ID (default: `openai`)
* `--locale <lang-REGION>` : localization (`en-US` default)
* `--dry-run` : statistics only, no output

---

## Security & Privacy

* **Offline by default** – network sockets disabled at startup
* **Opt-in only** – external APIs enabled *only* with `--enable-network`
* **No telemetry** – no data ever leaves your machine
* **Reproducible builds** – pinned dependencies, SBOM & signed releases
* **PII caution** – assume exports contain private data; test with synthetic logs

Verification:

```bash
# macOS / Linux
lsof -i -p <PID>   # no network sockets
# or
strace -f -e trace=network <cmd>
```

---

## Roadmap

* [x] CLI MVP – Markdown export, deduplication, thread splitting
* [ ] Minimal HTML Viewer – index + search bar
* [ ] Multi-provider adapters (Claude, Gemini, etc.)
* [ ] Apps SDK integration (experimental branch)
* [ ] Full GUI (desktop, later stage)

---

## Project Layout

```
llm-logparser/
  src/
    parser/           # Core: stream ingest, normalize, export
    cli.py
    providers/        # openai/, anthropic/, gemini/...
    viewer/           # static HTML (MVP)
  artifacts/          # parsed output (ignored in git)
  docs/               # provider guide, error codes, contracts
  tests/
  README.md
  LICENSE
  pyproject.toml
```

---

## Contributing

Contributions welcome!
Ideal first PRs: provider adapters, exporters, or i18n improvements.

Principles:

* Keep **core deterministic and raw-only**
* Put variability into **providers or plugins**
* Preserve **offline-first trust**

---

## License

MIT – permissive to encourage adoption and adapters.

---

## Author

> "The words you weave are not mere echoes;  
> they carry weight,  
> and may they never be lost to the tide of time."

© 2025 **Ashes Division — Reyz Laboratory**  
