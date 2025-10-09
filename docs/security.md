# Security & Trust

## Offline-by-Default

- Network sockets are disabled at startup (monkey-patch).
- No telemetry or hidden calls.

### Example Guard (Python)

```python
# startup_guard.py
import socket
class _NoNet(socket.socket):
    def __init__(self, *a, **kw):
        raise OSError("Network disabled by logparser (offline mode)")
socket.socket = _NoNet
```

## Reproducible Builds

- Pin dependencies with hashes.
- Generate SBOM; sign release artifacts (SHA256 + signature).

## Verification

- `lsof -i -p <PID>` → no sockets
- `strace -f -e trace=network <cmd>` → no network syscalls
- GUI and Apps SDK are **opt-in** and separated from parser core.
