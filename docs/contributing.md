# Contributing

Thanks for considering a contribution!

## Principles
- Keep core deterministic & raw-only.
- Prefer thin provider adapters; avoid mixing UI with parsing.
- Uphold offline-first: no telemetry, no implicit network.

## Getting Started
1. Fork and create a feature branch.
2. Add tests (golden snapshots for providers).
3. Run lint & audit: `ruff`, `bandit`, `pip-audit`.
4. Ensure network-prohibition tests pass.
5. Submit a PR with scope & rationale.

## Coding style — comments & i18n
* Source code comments should be written in **English**.
* Comments that describe **design intent, constraints, or subtle behavior** MUST be English.
* Short temporary notes may be left in Japanese, but should eventually be rewritten or removed.
* User-facing strings follow the i18n rules and should never be hard-coded per-language.

We value small, focused PRs.
