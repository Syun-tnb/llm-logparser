# Error Codes & JSON Error Contract

The CLI returns structured JSON on errors, with stable codes.

## Payload

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

- **LP1xxx**: Startup/Environment (args, I/O permissions)
- **LP2xxx**: Input format (JSON broken, encoding)
- **LP3xxx**: Provider config/mapping
- **LP4xxx**: Normalization/schema
- **LP5xxx**: Output/splitting
- **LP6xxx**: i18n/locale
- **LP9xxx**: Unexpected internal

## Exit Codes

- `0`: success (WARN/ERROR aggregated in summary)
- `1/2/3/4/5/9`: fatal per range
