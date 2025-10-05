# llm-logparser
Parser for LLM conversation logs → JSONL + manifest.

Normalize conversational logs from LLM tools into a compact, reproducible **JSONL** format, then browse/search them with a lightweight Viewer.  
**Core is local-first, AI-agnostic, and plugin-friendly.**

> TL;DR: Parse → Normalize → Export (JSONL raw-only) → View.  
> Parser produces **one line = one message** with guaranteed fields and stable IDs.
> Current MVP scope: Only supports ChatGPT export logs (JSON array).

---

## Why

- LLM chat logs are everywhere, but each product exports them differently.  
- A small, **deterministic** “normalization pass” enables reliable reuse: search, filter, export to Markdown/note/music/image prompts, etc.  
- Keep the **core minimal**; move variability to adapters and plugins.

---

## Design Principles

- **Parser (make)**: Ingest raw logs → **normalize** to JSONL (raw-only) + `manifest.json`. No AI calls. Deterministic.
- **Viewer (see)**: Read JSONL, search/filter, render on demand. Start as static HTML/JS; optional desktop (Tauri) later.
- **Plugins (extend)**: Adapters (other AI tools), exporters, UI panels, optional AI providers — all out-of-core.

> Documentation tone: “pioneer/engineering-first.”  
> 用語は「木構造（ツリー構造）」のように併記する場合があります。

---

## Intermediate Format (JSONL)

**Raw-only** by design: store exactly what was ingested; render later.

Required per line:
- `id` (string) – unique message key (see ID policy)
- `ts` (ISO 8601, UTC)
- `role` (`system|user|assistant|tool`)
- `raw` (string) – original text content as received (may include `\uXXXX`)

Recommended factual fields (do not change semantics):
- `src` (origin file or channel)
- `conv_id`, `msg_id` (if present)
- `len_raw` (int)

**Example**
```json
{"id":"cA:m42","ts":"2025-10-04T00:47:00Z","role":"user","raw":"\\u4eca\\u65e5...", "src":"chat_20251004.json","conv_id":"cA","msg_id":"m42","len_raw":40}
```

> Keep UTF-8, LF newlines, and one JSON object per line. The parser never “prettifies” content.

---

## Manifest

The parser can optionally use a manifest file to control parsing policies
(e.g., ID handling, provider info).

- By default, it looks for `./artifacts/manifest.json`.
- If not found, it runs in "no-manifest mode" with safe defaults.
- You can explicitly provide a custom manifest:
  ```bash
  llm-logparser parse --in raw.json --manifest custom_manifest.json
  ```

- Programmatically:

  ```python
  from llm_logparser import parse_log
  data = parse_log("raw.json", manifest_path="custom_manifest.json")
  ```

`manifest.json` accompanies `messages-*.jsonl` shards.

```json
{
  "schema": "1.0",
  "provider": "openai-chatgpt",
  "generated_at": "2025-10-04T00:50:05Z",
  "timezone_display": "Asia/Tokyo",
  "index": {
    "shards": [
      {"path":"messages-00001.jsonl","count":50000},
      {"path":"messages-00002.jsonl","count":1234}
    ]
  },
  "id_policy": {
    "strategy": "composite",
    "composite": {"parts":["conv_id","msg_id"], "separator": ":"},
    "notes": "conversation_id + message_id are globally unique."
  }
}
```

> The manifest is the “contract” for Viewer: where shards live, how IDs are formed, optional provider hints.

### Policy keys

The manifest can include optional policies to guide parsing:

- **id_policy**  
  (see [ID Policy](#id-policy) section for details)

- **text_policy**  
  Controls text normalization.  
  ```json
  {
    "normalize_whitespace": false,
    "strip_control_chars": true,
    "emoji_handling": "keep"   // "keep" | "remove" | "normalize"
  }
  ```

* `normalize_whitespace`: replace full-width spaces with half-width
* `strip_control_chars`: drop invisible control characters
* `emoji_handling`: whether to keep, remove, or normalize emoji

---

## ID Policy

* **Default (ChatGPT-like)**: `id = conv_id + ":" + msg_id`.
* If a provider lacks stable IDs, the adapter may use `conv_id:seq_no`.
* Content hashes are *optional*, used only when providers don’t give stable IDs.

---

## Forked Threads (branching)

Some tools allow **branching/forks** from a message. We treat forks as **separate conversations**, linked in the manifest:

```json
{
  "forks": [
    {"parent":"convA", "child":"convB", "fork_point_msg_id":"m20"}
  ]
}
```

Viewer may simply display a **jump link** to the fork; full tree visualization is optional later.

---

## Incremental Fetch & De-duplication (append-only assumption)

* Many providers are **append-only**: new messages are appended; messages aren’t edited in-place.
* **State** stores:

  * `last_fetch_at` (UTC)
  * per-thread `last_seen_updated_at`
  * a `seen` store for `id` (e.g., SQLite)

**Algorithm (safe + simple):**

1. List threads; pick those with `updated_at > last_fetch_at - watermark`.
2. For each picked thread, fetch **all** messages (avoid partial range bugs).
3. For each message in timestamp order:

   * Compute `key = conv_id:msg_id`.
   * If not in `seen`, append JSONL line and mark seen.
4. Update state.

No body-hash or edit-versions required under append-only.

---

## Project Layout (proposed)

```
llm-logparser/
  parser/           # Python core: ingest/normalize/export
    cli.py
    adapters/       # openai/, anthropic/, generic-json/ ...
  viewer/           # static HTML/JS MVP (later: Tauri desktop)
    index.html
    app.js
    styles.css
  examples/
    raw.json
  artifacts/        # manifest.json + messages-*.jsonl (gitignored)
  docs/
    manifest.md
  tests/
  .gitignore
  README.md
  LICENSE
  pyproject.toml
```

---

## Quickstart (MVP)

> Note: Commands are **usage goals**. Actual CLI may differ until first release.

```bash
# 1) Install (local dev)
pip install -e .

# 2) Parse raw → JSONL
python3 -m llm_logparser.cli fetch \
  --provider openai \
  --in examples/raw.json \
  --out artifacts/ \
  --full

# 3) Incremental (append-only)
python3 -m llm_logparser.cli fetch \
  --provider openai \
  --out artifacts/ \
  --since-state .state/fetch.json \
  --watermark 300

# 4) View (static)
# open viewer/index.html in a browser and load artifacts/manifest.json
```

---

## Plugins (extensibility)

* **Ingest adapters**: map provider-specific JSON → core JSONL.
* **Exporters**: pick message IDs → Markdown/TXT/CSV.
* **Viewer panels**: additional UI (topic counts, diff, tags).
* **AI providers (optional)**: off by default; opt-in only.

Suggested discovery: Python entry points (`logtool.plugins`) or `pluggy` hooks.

---

## Fun Note

The **Exporter** was originally planned as a *future feature* (see Roadmap).  
However, in true "log chaos" fashion, it got implemented **ahead of schedule** —  
born out of a heated exchange where *Reyna* insisted on using logs as a “備忘録,”  
and the dev retorted: *「そのためのログパーサーやろがい！」*.  

In short: **Exporter = future feature, implemented early (逆ギレ由来)**.  

---

## Roadmap

* [ ] Parser MVP: JSON → JSONL raw-only, manifest generation
* [ ] OpenAI adapter (ChatGPT export)
* [ ] Viewer MVP: static HTML/JS (search/filter, safe markdown render)
* [ ] Exporter: selected IDs → Markdown
* [ ] Adapters: Anthropic, Gemini, Generic-JSON mapping (YAML)
* [ ] Optional desktop app (Tauri + bundled Python)
* [ ] Plugin SDK & examples

---

## License

MIT. Choose permissive to encourage community adapters.

---

## Contributing

Issues and PRs welcome. Adapters and exporters are ideal first contributions.
Please keep **core deterministic** and **raw-only**; put variability into plugins.

---

## Author

> "The words you weave are not mere echoes;
> they carry weight,
> and may they never be lost to the tide of time."

© 2025 Ashes Division — Reyz Laboratory



