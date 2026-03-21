# Test Strategy

Local test execution assumes a synced `uv` environment:

```bash
uv sync --extra dev
uv run pytest
```

## Suite Categories

The pytest suite is grouped with lightweight markers so contributors can choose
the right confidence level for local work and releases:

* `unit`: isolated logic and helper coverage
* `cli`: parser/handler/command-path coverage
* `contract`: machine-readable artifact/schema coverage
* `integration`: deterministic end-to-end or subsystem pipeline coverage

Recommended commands:

```bash
uv run pytest
uv run pytest -m integration
```

Release gate:

* run `uv run pytest -m integration` for the focused end-to-end pipeline checks
* run `uv run pytest` before cutting a release for the full suite

## Golden Tests

* Provider adapter samples → expected normalized JSONL output.
* Snapshots are diff-friendly; any schema/formatting change must be reviewed.

> [!NOTE]
> Full Markdown snapshot tests are **planned**. Current tests are primarily unit tests
> validating adapter output, exporter formatting, and schema validation.

## Robustness

* Broken JSON lines, missing fields, oversized lines, control characters, multi-lingual content.
* Timezone / locale formatting correctness.
* Verify both behaviors: **fail-fast** and **skip-and-continue**.

## i18n

* Focused tests should cover scalar `messages:` lookup and structured `analysis:` lookup.
* Fallback rules should be verified explicitly:
  selected locale → `en-US` → raw key for scalar messages.
* Analyzer resource fallback should be verified as:
  selected locale → `en-US`.
* Locale precedence should be covered explicitly:
  CLI `--locale` / `--lang` → `LLP_LOCALE` → selected profile locale → `en-US`.
* Unknown locales must gracefully resolve to `en-US` without crashing.
* Help/bootstrap locale behavior should be tested separately from post-config runtime locale updates.

## Config

* Typed config loading, schema mismatch, profile resolution, and CLI override precedence.
* Priority order is respected for config-backed options: CLI > selected profile > defaults.
* `config path`, `config show`, and `config validate` get smoke coverage.
* Config write-back, locking/atomic writes, and backup/restore remain future work.

## Network Prohibition

* Startup socket patch is a **recommended practice** (see `docs/security.md`).
* When implemented, tests should assert no network syscalls.
* GUI / Apps SDK features remain isolated from the parser core.

## Determinism

* Same input + same settings → **byte-identical output**.
* Stable ordering is preserved under concurrent execution.

## Large-scale

* Validate against large JSONL datasets (hundreds of thousands of lines):

  * Memory use does not degrade disproportionately.
  * Split policies (size / count / auto) behave as expected.
  * No performance regressions (baseline tracked over time).

---

All tests exist to ensure that changes **do not break user output compatibility or stability**.
