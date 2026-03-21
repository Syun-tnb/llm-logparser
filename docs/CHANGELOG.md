# CHANGELOG

## [v1.3.0] - 2026-03-21

### Summary

`v1.3.0` expands **llm-logparser** from a parse/export-focused CLI into a more complete local analysis toolkit.

Relative to `v1.0.0`, this release introduces configuration and profile support, single-thread extraction, deterministic analyzer commands and rebuildable sidecar artifacts, optional SQLite indexing, broader provider coverage (Anthropic and xAI/Grok), YAML-backed i18n, and significantly stronger artifact contracts and test coverage.

This remains a SemVer **minor** release: substantial new capabilities without intentional breaking CLI changes.

---

### Added

* Added `extract` command for single-thread Gemini-compatible export.
* Added `config.yaml` support:

  * auto-discovery
  * profile selection
  * CLI override precedence
  * interactive resolution flow
* Added analyzer command suite:

  * `analyze stats`
  * `analyze timeline`
  * `analyze tokens`
  * `analyze metrics`
* Added analyzer sidecar artifacts:

  * `thread_stats.json`
  * `message_windows.jsonl`
  * `token_stats.json`
  * `metrics.json`
* Added optional SQLite analysis index via:

  * `analyze sqlite-build`
* Added provider support beyond OpenAI:

  * Anthropic / Claude adapter
  * xAI / Grok adapter (runtime + wrapper shape support)
* Added YAML-backed i18n system:

  * CLI / help / runtime messages
  * analyzer heuristic phrase resources
* Added CLI alias:

  * `llp` → same entrypoint as `llm-logparser`
* Added Japanese documentation:

  * `README_jp`

---

### Changed

* Established canonical-data-first architecture:

  * `parsed.jsonl` is the single source of truth
  * all sidecar artifacts are deterministic and rebuildable
  * parser, exporter, and analyzer responsibilities are explicitly separated
* Refactored CLI internals:

  * split parser construction and command handlers
  * improved maintainability without changing top-level commands
* Finalized config v1 behavior:

  * sanitize policy defaults
  * explicit legacy/deprecation handling
* Unified locale resolution across CLI lifecycle:

  * clarified precedence rules
  * ensured analyzer outputs remain machine-stable while UI is localized
* Improved analyzer usability:

  * clearer `tokens → metrics` dependency handling
  * `--skip-existing` for incremental builds
  * `--dry-run` preview support
  * standardized error codes (LP7xxx range, e.g. `LP7100`)
* Improved normalization and output stability:

  * OpenAI metadata normalization
  * deterministic ID shortening for thread paths
* Strengthened artifact contracts:

  * clearer JSON schema expectations
  * improved JSON ↔ SQLite correspondence documentation

---

### Fixed

* Fixed analyzer dependency resolution to fail early and explicitly when `token_stats.json` is missing.
* Removed hardcoded Japanese CLI messages and fully integrated i18n routing.
* Fixed manifest validation workflow and schema path issues after refactors.
* Improved locale alias resolution and fallback consistency.
* Eliminated redundant token-analysis scan and clarified metric heuristics without changing behavior.

---

### Docs / Tests

* Added major documentation:

  * analyzer architecture
  * analysis artifacts
  * config guide
  * output contracts and i18n boundaries
* Improved documentation clarity:

  * `tokens → metrics` dependency flow
  * canonical data-source policy
  * analyzer output class differences
  * artifact reproducibility guarantees
* Migrated local development workflow:

  * from `pip/venv` → `uv`
* Expanded automated coverage:

  * analyzer CLI tests
  * artifact contract/schema tests
  * config and i18n tests
  * Anthropic and xAI adapter tests
  * SQLite builder tests
  * release-gate integration suite
  * pytest markers (`unit / cli / contract / integration`)

---

### Notes

* No intentional breaking changes detected from commit history; appropriate as a minor release.
* Existing config compatibility is preserved, though some legacy keys are now deprecated and documented.
* This release marks a transition from:

  * parse/export CLI → canonical-data-first analysis toolchain
* All analyzer outputs are deterministic and rebuildable from `parsed.jsonl`.
* Token analysis remains local-first but depends on `tiktoken`, which may fetch encoding data once on first use.

---

### Evidence (commit-level highlights)

* Core features:

  * `b1a6ed0` extract command
  * config system: `619d925`, `0eb381c`, `3e0220d`, `ec484ae`, `dcee800`
  * analyzer suite: `807ffc4`, `13c55b7`, `578b0ed`, `bdf4e46`, `6a231ab`, `27e0f80`, `d064ac5`, `30e50e0`
  * artifacts / SQLite: `2b8c283`, `ae95a57`, `8602871`
* Providers:

  * Anthropic: `3112643`
  * xAI: `d5f3d68`, `075e815`
  * OpenAI normalization: `dd68e06`
* i18n / CLI:

  * `5bf5dae`, `f620209`, `433423a`, `33c75d0`, `77bdeb4`, `c6e5c1e`, `031c53e`
  * UX polish: `e635ade`, `593cf73`, `f7aef94`, `440e8de`
* Contracts / architecture:

  * `d640c3c`, `99fae95`
  * docs: `b89567e`, `dfe97b9`, `77ef7a2`, `11dd427`, `1e6bd41`
* Release confidence:

  * `e68a6f9`
  * `3ed89f7`

---

## [v1.0.0] - 2026-01-02

Exporter MVP — first public release

This release ships the first stable Exporter MVP.

Highlights:
- end-to-end parse → normalize → export pipeline
- canonical JSONL format + Markdown contract
- offline-first behavior (no telemetry)
- chain mode and automatic splitting
- schema validation (optional)

This tag marks the start of the public history line (v1.x).
