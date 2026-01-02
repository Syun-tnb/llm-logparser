# Test Strategy

## Golden Tests

* Provider YAML + synthetic samples → expected Markdown snapshots.
* Snapshots are diff-friendly; any schema/formatting change must be reviewed.

## Robustness

* Broken JSON lines, missing fields, oversized lines, control characters, multi-lingual content.
* Timezone / locale formatting correctness.
* Verify both behaviors: **fail-fast** and **skip-and-continue**.

## i18n

* Per-locale snapshots; lint for missing message keys (fallback = English with WARN).
* Unknown locales must gracefully fall back without crashing.

## Config

* Load/merge, schema mismatch, locking/atomic writes, backup/restore.
* Priority order is respected: CLI > environment > profile > defaults.

## Network Prohibition

* Startup socket patch enabled in tests; assert no network syscalls.
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
