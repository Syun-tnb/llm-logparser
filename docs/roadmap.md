# Roadmap

* [x] CLI MVP — Markdown export, deduplication, thread splitting
* [ ] Minimal HTML Viewer — index + simple search
* [ ] Multi-provider adapters (Claude, Gemini, etc.)
* [ ] Apps SDK integration (experimental)
* [ ] Full GUI (desktop, later stage)

---

## MVP Roadmap — llm-logparser

The roadmap is intentionally incremental: stabilize the pipeline first, then iterate on usability and integrations.

### 🎯 Phase 1: Core Stability

| Priority | Item                | Status         | Notes                                              |
| -------: | ------------------- | -------------- | -------------------------------------------------- |
|      ⭐⭐⭐ | Harden Parser       | 🔧 In progress | Streaming, error isolation, fail-fast behavior     |
|       ⭐⭐ | Exporter (Markdown) | ✅ Done         | Front-matter, formatting, GFM output, thread splitting |
|       ⭐⭐ | CLI chain execution | ✅ Done         | Reliable `parse → export` end-to-end               |
|        ⭐ | Split policies      | ✅ Done         | size / count / auto, soft-overflow, hard, tail-merge |

---

### ⚙️ Phase 2: Operation & Resilience

| Priority | Item                                | Status             | Notes                                     |
| -------: | ----------------------------------- | ------------------ | ----------------------------------------- |
|      ⭐⭐⭐ | Differential cache by `update_time` | 🔧 Partial          | `load_manifest_if_exists` / `should_skip_thread` implemented |
|       ⭐⭐ | Unified error handling              | 🔧 In progress      | Log levels implemented; structured JSON error output not yet |
|        ⭐ | Locale / timezone sanitation        | 🕓 Planned          | Safe file names, robust ZoneInfo handling |

---

### 🌐 Phase 3: Output & Viewer

| Priority | Item                        | Status     | Notes                                          |
| -------: | --------------------------- | ---------- | ---------------------------------------------- |
|       ⭐⭐ | Minimal HTML viewer         | 🕓 Planned | `index + list + detail`, read-only             |
|        ⭐ | i18n dictionary             | ✅ Done     | Structure exists; translations added gradually |
|        ⭐ | Quickstart & README refresh | 🕓 Planned | Practical CLI examples + output samples        |

---

### 🧠 Phase 4: Experimental Semantic Layer

| Priority | Item                        | Status     | Notes |
| -------: | --------------------------- | ---------- | ----- |
|      ⭐⭐ | Window embeddings           | ✅ Done     | Experimental, local-first, additive, non-canonical prototype sidecars |
|      ⭐⭐ | Semantic neighbors          | ✅ Done     | Experimental nearest-neighbor view over window embeddings |
|       ⭐ | `analyze semantic-preview`  | ✅ Done     | Read-only CLI renderer for one window and its stored neighbors |
|       ⭐ | Interactive semantic preview | 🕓 Exploratory | Future CLI exploration only; default-off by design and not planned as the default workflow |

Current semantic-layer positioning:

* semantic outputs remain experimental and should not be treated as stable canonical analysis
* the current scope is window embeddings, semantic neighbors, and `semantic-preview`
* these features remain local-first, additive, rebuildable, and non-canonical
* higher-level topic clustering, labeling, and timeline tracking remain future work

---

> Roadmap items may shift as we gain more real-world usage feedback.
> Breaking changes will follow semantic versioning and be documented before release.
