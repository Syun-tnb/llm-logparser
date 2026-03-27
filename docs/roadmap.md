# Roadmap

The roadmap stays intentionally conservative:

* canonical parsing remains the foundation
* deterministic L1/L2 analysis remains the stable base
* semantic-layer work remains experimental, local-first, additive, rebuildable,
  and non-canonical

---

## Current Focus

| Priority | Item | Status | Notes |
| -------: | ---- | ------ | ----- |
| ⭐⭐⭐ | Canonical parser stability | In progress | Keep provider normalization and schema contracts predictable |
| ⭐⭐⭐ | Deterministic analyzer contracts | In progress | Preserve stable L1/L2 sidecars and rebuildability |
| ⭐⭐ | Experimental semantic layer | In progress | Window embeddings, semantic neighbors, and preview remain additive only |
| ⭐ | Viewer and usability improvements | Planned | Read-only inspection workflows over existing artifacts |

---

## Experimental Semantic Layer

Current implemented scope:

| Priority | Item | Status | Notes |
| -------: | ---- | ------ | ----- |
| ⭐⭐ | Window embeddings | Done | Experimental sidecars built from `message_windows.jsonl` |
| ⭐⭐ | Semantic neighbors | Done | Experimental nearest-neighbor structure over embedded windows |
| ⭐ | `analyze semantic-preview` | Done | Read-only CLI renderer for one window and its stored neighbors |
| ⭐ | Interactive semantic preview | Exploratory | Future CLI exploration only; default-off by design |

Positioning rules:

* semantic outputs are not canonical truth
* semantic outputs must remain safe to delete and rebuild
* local-first execution remains the default assumption
* higher-level topic clustering, labeling, and timeline tracking remain future work

---

## Near-Term Follow-Ups

| Priority | Item | Status | Notes |
| -------: | ---- | ------ | ----- |
| ⭐⭐ | README and docs alignment | In progress | Keep semantic-layer wording consistent across user-facing docs |
| ⭐ | Minimal HTML viewer | Planned | Read-only inspection layer, separate from canonical generation |
| ⭐ | Additional semantic evaluation fixtures | Planned | Small deterministic fixtures to validate preview and neighbor quality |

---

Roadmap items may shift with real-world usage feedback. Breaking changes will
continue to follow semantic versioning and be documented before release.
