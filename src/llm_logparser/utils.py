# src/llm_logparser/utils.py
import re, json
from datetime import datetime, timezone

_CTRL = re.compile(r'[\x00-\x08\x0B\x0C\x0E-\x1F]')

def to_iso_utc(ts) -> str | None:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)\
                       .isoformat().replace("+00:00", "Z")
    except Exception:
        return None

def sanitize_text(s: object) -> str:
    s = str(s) if not isinstance(s, str) else s
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    return _CTRL.sub(" ", s)

def dumps_jsonl(obj: dict) -> str:
    # フィールド共通クリーン
    for k in ("raw", "role", "id", "conv_id", "msg_id", "src"):
        if k in obj and obj[k] is not None:
            obj[k] = sanitize_text(obj[k])
    return json.dumps(obj, ensure_ascii=False)
