# Test Strategy

## Golden Tests
- Provider YAML + synthetic samples → expected Markdown snapshots.

## Robustness
- Broken JSON lines, missing fields, huge lines, control chars, multi-lingual content.
- Timezone/locale formatting correctness.

## i18n
- Snapshot per locale; lint for missing message keys (fallback = English with WARN).

## Config
- Load/merge, schema mismatch, locking/atomic write, backup/restore.

## Network Prohibition
- Startup socket patch enabled in tests; assert no network syscalls.
