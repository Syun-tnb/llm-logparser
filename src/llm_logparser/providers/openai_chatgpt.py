from typing import Iterable, Dict, Any
from ..utils import to_iso_utc

def iter_messages(conversation: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    conv_id = conversation.get("id") or conversation.get("conversation_id") or "conv"
    base_ts = to_iso_utc(conversation.get("update_time") or conversation.get("create_time"))
    mapping = conversation.get("mapping") or {}
    if isinstance(mapping, dict) and mapping:
        for i, (_, node) in enumerate(mapping.items(), 1):
            msg = (node or {}).get("message") or {}
            role = ((msg.get("author") or {}).get("role")) or "unknown"
            content = (msg.get("content") or {})
            raw = content.get("parts") or content or conversation.get("title") or ""
            if isinstance(raw, list):
                raw = "\n".join(map(str, raw))
            ts = to_iso_utc(msg.get("create_time")) or base_ts or ""
            yield {
                "id": f"{conv_id}:{msg.get('id') or i}",
                "conv_id": conv_id,
                "msg_id": msg.get("id") or str(i),
                "ts": ts,
                "role": role,
                "raw": raw,
                "src": conversation.get("title") or "",
                "len_raw": len(str(raw)),
                "meta": {"provider": "openai"},
            }
    else:
        yield {
            "id": f"{conv_id}:m1", "conv_id": conv_id, "msg_id": "m1",
            "ts": base_ts or "", "role": "system",
            "raw": conversation.get("title") or "(empty)",
            "src": conversation.get("title") or "", "len_raw": len(conversation.get("title") or ""),
            "meta": {"provider": "openai"},
        }
