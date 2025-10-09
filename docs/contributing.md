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

We value small, focused PRs.
