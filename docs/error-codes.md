# Error Codes & JSON Error Contract

The CLI uses stable error codes for classification. Currently, errors are output via
the logging system (plain text). A **structured JSON error payload** (described below)
is planned for a future release.

## JSON Payload (Future Design)

> [!NOTE]
> The structured JSON error payload below is a **design specification** for future implementation.
> The current CLI outputs errors as plain text via `logger.error()`.

```json
{
  "version": "1.0",
  "severity": "ERROR",
  "code": "LP2001",
  "message_key": "input.json.parse_failed",
  "params": {"path":"raw.jsonl","line": 42},
  "exit_code": 2,
  "provider_id": "openai",
  "correlation_id": "req-xyz",
  "context": {"hint":"check encoding"},
  "retryable": false,
  "partial": {"processed": 41, "skipped": 1},
  "timestamp": "2025-10-09T12:00:00Z"
}
```

## Ranges

| Range | Category | Status |
|-------|----------|--------|
| **LP2xxx** | Input format (JSON broken, encoding) | **Implemented** — `LP2000` (base), `LP2100` (input), `LP2200` (adapter), `LP2300` (write) |
| LP1xxx | Startup/Environment (args, I/O permissions) | Planned |
| LP3xxx | Provider config/mapping | Planned |
| LP4xxx | Normalization/schema | Planned |
| LP5xxx | Output/splitting | Planned |
| LP6xxx | i18n/locale | Planned |
| LP7xxx | Analyze / artifacts / dependency errors | **Implemented** — `LP7100` (metrics dependency missing) |
| LP9xxx | Unexpected internal | Planned |

## LP7xxx Analyze / Artifacts / Dependency Errors

| Code | Meaning | Current CLI Behavior |
|------|---------|----------------------|
| `LP7100` | Metrics dependency missing | Emitted when `analyze metrics` requires sibling `token_stats.json` that does not exist. CLI exits with code `2` and keeps the existing actionable hint to run `analyze tokens` first. |

## Exit Codes

- `0`: success (WARN/ERROR aggregated in summary)
- `2`: path / input error
- `3`: permission error
- `4`: chain-mode directory error
- `99`: unexpected error
