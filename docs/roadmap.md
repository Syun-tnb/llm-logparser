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
|       ⭐⭐ | Exporter (Markdown) | 🕓 Pending     | Front-matter, formatting hints, consistency checks |
|       ⭐⭐ | CLI chain execution | ✅ Done         | Reliable `parse → export` end-to-end               |
|        ⭐ | Split policies      | 🕓 Pending     | Support size / count, preview before split         |

---

### ⚙️ Phase 2: Operation & Resilience

| Priority | Item                                | Status         | Notes                                     |
| -------: | ----------------------------------- | -------------- | ----------------------------------------- |
|      ⭐⭐⭐ | Differential cache by `update_time` | 🕓 Planned     | Re-process only changed threads           |
|       ⭐⭐ | Unified error handling              | 🔧 In progress | Log levels, structured exit codes         |
|        ⭐ | Locale / timezone sanitation        | 🕓 Planned     | Safe file names, robust ZoneInfo handling |

---

### 🌐 Phase 3: Output & Viewer

| Priority | Item                        | Status     | Notes                                          |
| -------: | --------------------------- | ---------- | ---------------------------------------------- |
|       ⭐⭐ | Minimal HTML viewer         | 🕓 Planned | `index + list + detail`, read-only             |
|        ⭐ | i18n dictionary             | ✅ Done     | Structure exists; translations added gradually |
|        ⭐ | Quickstart & README refresh | 🕓 Planned | Practical CLI examples + output samples        |

---

> Roadmap items may shift as we gain more real-world usage feedback.
> Breaking changes will follow semantic versioning and be documented before release.
