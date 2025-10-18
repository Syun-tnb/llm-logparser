# providers/openai/adapter.py
from typing import Any, Dict, List, Iterable
from .utils import normalize_text

OPENAI_PREFERRED_KEYS = ("summary", "result", "user_profile", "user_instructions")

def _conv_id(conv: Dict[str, Any]) -> str | None:
    return conv.get("id") or conv.get("conversation_id") or conv.get("uuid")

def _role(msg: Dict[str, Any]) -> str | None:
    a = (msg.get("author") or {})
    return a.get("role") or msg.get("role")

def _timestamp(primary: Dict[str, Any], secondary: Dict[str, Any] | None = None):
    for o in (primary, secondary or {}):
        if isinstance(o, dict):
            for k in ("create_time", "end_turn_time", "timestamp", "ts"):
                if k in o:
                    return o.get(k)
    return None

def _is_root_or_empty(msg: dict, node: dict) -> bool:
    mid = (msg or {}).get("id") or (node or {}).get("id")
    if mid == "client-created-root":
        return True
    role = (msg.get("author") or {}).get("role") or msg.get("role")
    content = (msg or {}).get("content")
    return role is None and (content is None or content == {})

def _build_entry(conv_id: str | None, msg: dict, node: dict | None) -> dict:
    role = _role(msg)
    content = msg.get("content")
    # ここでは“正規化のみ”。空は空のまま許容（表示は Exporter の責務）
    text = normalize_text(
        content,
        preferred_keys=OPENAI_PREFERRED_KEYS,
        allow_loose=True,
    )
    # meta は“元メタ + 共通フィールド”に限定（placeholder等は付けない）
    meta_src = (msg.get("metadata") or {}).copy()
    meta = {
        **meta_src,
        "provider": "openai",
        "model": meta_src.get("model") or msg.get("model"),
    }
    return {
        "conv_id": conv_id,
        "msg_id": msg.get("id") or (node or {}).get("id"),
        "role": role,
        "text": text,
        "ts": _timestamp(msg, node),
        "meta": meta,
    }

def _iter_from_mapping(conv: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    conv_id = _conv_id(conv)
    for node in (conv.get("mapping") or {}).values():
        if not isinstance(node, dict): continue
        msg = node.get("message") or {}
        if not isinstance(msg, dict) or _is_root_or_empty(msg, node): continue
        yield _build_entry(conv_id, msg, node)

def _iter_from_messages(conv: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    conv_id = _conv_id(conv)
    for msg in (conv.get("messages") or []):
        if not isinstance(msg, dict) or _is_root_or_empty(msg, {}): continue
        yield _build_entry(conv_id, msg, None)

def _iter_from_message(conv: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    conv_id = _conv_id(conv)
    msg = conv.get("message")
    if isinstance(msg, dict) and not _is_root_or_empty(msg, {}):
        yield _build_entry(conv_id, msg, None)

def _iter_from_flat_record(rec: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    if not {"conv_id","msg_id","role"}.issubset(rec.keys()): return []
    msg = {"id": rec.get("msg_id") or rec.get("id"),
           "role": rec.get("role"),
           "content": rec.get("raw") if "raw" in rec else rec.get("content"),
           "metadata": (rec.get("meta") or {})}
    node = {"id": rec.get("id"), "create_time": rec.get("ts")}
    yield _build_entry(rec.get("conv_id"), msg, node)

def adapter(conversation: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if isinstance(conversation.get("mapping"), dict):
        out.extend(_iter_from_mapping(conversation))
    elif isinstance(conversation.get("messages"), list):
        out.extend(_iter_from_messages(conversation))
    elif isinstance(conversation.get("message"), dict):
        out.extend(_iter_from_message(conversation))
    else:
        out.extend(_iter_from_flat_record(conversation))
    out.sort(key=lambda e: (e["ts"] is None, e["ts"]))
    return out

def get_adapter():
    return adapter
