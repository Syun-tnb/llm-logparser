# Security & Trust

## Offline-by-Default

- No telemetry or hidden network calls.
- Sensitive logs stay local.
- Deterministic output supports reproducible audits.

---

## PII Sanitization (Implemented)

The `extract` subcommand automatically applies the following sanitization:

### Sensitive key redaction
Keys containing any of these keywords are replaced with `REDACTED`:
- `SECRET`
- `TOKEN`
- `API_KEY`
- `AUTHORIZATION`
- `COOKIE`
- `PASSWORD`

### Text content masking
Within message `content.parts`:
- **Email addresses** → `[REDACTED_EMAIL]`
- **Phone numbers** → `[REDACTED_PHONE]`

These protections are applied by `providers/openai/extractor.py` before writing `extract.json`.

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

