# Contributing

Thanks for considering a contribution!

## Principles
- Keep core deterministic & raw-only.
- Prefer thin provider adapters; avoid mixing UI with parsing.
- Uphold offline-first: no telemetry, and no avoidable network use.
  Current caveat: tokenizer-based analysis may trigger a one-time `tiktoken`
  encoding download on first use, then relies on the local cache.

## Getting Started
1. Fork and create a feature branch.
2. Sync the project environment: `uv sync --extra dev`.
3. Add tests (golden snapshots for providers).
4. Run tests locally: `uv run pytest`.
5. Run lint & audit as needed: `uv run ruff`, `uv run bandit`, `uv run pip-audit`.
6. Ensure network-prohibition tests pass.
7. Submit a PR with scope & rationale.

## Coding style — comments & i18n
* Source code comments should be written in **English**.
* Comments that describe **design intent, constraints, or subtle behavior** MUST be English.
* Short temporary notes may be left in Japanese, but should eventually be rewritten or removed.
* User-facing strings follow the i18n rules and should never be hard-coded per-language.

We value small, focused PRs.
