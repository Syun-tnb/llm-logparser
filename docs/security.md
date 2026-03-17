# Security & Trust

## Offline-by-Default

- No telemetry or hidden network calls.
- Sensitive logs stay local.
- Deterministic output supports reproducible audits.

---

## PII Sanitization (Implemented)

The `extract` subcommand applies config-driven sanitization. If the `sanitize`
section is omitted, the default remains enabled for compatibility.

### Sensitive key redaction
When sanitization is enabled, keys containing any of these built-in keywords are
redacted by default:
- `SECRET`
- `TOKEN`
- `API_KEY`
- `AUTHORIZATION`
- `COOKIE`
- `PASSWORD`

### Text content masking
By default, message `content.parts` are scanned with built-in email and phone
patterns and matches are replaced with the configured replacement token
(`REDACTED` unless overridden).

Supported scopes:

- `content_parts`: sanitize only extracted text fields such as `content.parts`
- `all_strings`: sanitize every traversed string value in the extracted payload

Custom behavior is controlled from `config.yaml`:

```yaml
sanitize:
  enabled: true
  replacement: REDACTED
  scope: content_parts
  extra_keywords: [credential]
  mask_patterns:
    - acct-\d+
```

Each successful extract also writes `extract.meta.json` with a safe summary of the
applied sanitize policy. It records whether sanitization ran, which scope was used,
which replacement token was used, and whether custom keywords or patterns were supplied.

---

## Network Prohibition (Recommended Practice)

> [!NOTE]
> The socket guard below is a **recommended practice** for deployment environments.
> It is not currently wired into the CLI startup automatically.

### Example Guard (Python)

```python
# startup_guard.py
import socket
class _NoNet(socket.socket):
    def __init__(self, *a, **kw):
        raise OSError("Network disabled by logparser (offline mode)")
socket.socket = _NoNet
```

---

## Reproducible Builds

- Pin dependencies with hashes.
- Generate SBOM; sign release artifacts (SHA256 + signature).

## Verification

- `lsof -i -p <PID>` → no sockets
- `strace -f -e trace=network <cmd>` → no network syscalls
- GUI and Apps SDK are **opt-in** and separated from parser core.
