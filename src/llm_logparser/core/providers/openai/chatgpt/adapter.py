# src/llm_logparser/providers/openai/chatgpt/adapter.py
from __future__ import annotations
import typing as t
from collections import defaultdict, deque
from hashlib import sha1
from pathlib import Path

from .utils import json_safe


# ============================================================
#  Manifest & Policy
# ============================================================

def get_manifest() -> dict:
    return {
        "schema_version": "1.2",
        "provider": "openai",
        "family": "chatgpt",
        "input_format": "chatgpt_export_v2+",
        "description": "Improved adapter with structural correction and linearization.",
        "expected_top_keys": ["mapping", "id", "create_time", "update_time"],
        "id_fields": ["conversation_id", "message_id"],
    }


def get_policy() -> dict:
    return {
        "keep_unicode_escape": True,
        "ignore_fields": ["metadata", "status", "recipient", "weight"],
        "flatten_parts_in_exporter": True,
        "allow_partial_parse": True,
        "timestamp_fields": ["create_time", "end_turn_time", "timestamp"],
        "safe_null_handling": True,
    }


# ============================================================
#  Helper: Extract nodes from mapping
# ============================================================

def _extract_nodes(mapping: dict) -> dict[str, dict]:
    """extract node_id → node_info safely.

    Note: includes structural nodes (message is None) to preserve graph order.
    """
    nodes = {}
    for node_id, node in mapping.items():
        if not isinstance(node, dict):
            continue
        nodes[node_id] = node

    return nodes


# ============================================================
#  Helper: Build graph based on parent-child relation
# ============================================================

def _build_graph(nodes: dict[str, dict]):
    """Construct parent → children adjacency graph."""
    parents = {}
    children_map = defaultdict(list)

    for node_id, node in nodes.items():
        parent = node.get("parent")
        parents[node_id] = parent
        for child_id in node.get("children") or []:
            if isinstance(child_id, str) and child_id in nodes:
                children_map[node_id].append(child_id)

    # Fallback: parent pointers (when children are missing)
    if not children_map:
        for node_id, parent in parents.items():
            if parent and parent in nodes:
                children_map[parent].append(node_id)

    return parents, children_map


# ============================================================
#  Helper: Determine root nodes
# ============================================================

def _find_roots(nodes: dict[str, dict], parents: dict[str, str | None]):
    """Find valid root nodes (no valid parent)."""
    roots = []

    for nid in nodes:
        p = parents.get(nid)
        if not p or p not in nodes:
            roots.append(nid)

    return roots


# ============================================================
#  Helper: Linearize graph
# ============================================================

def _linearize(nodes, parents, children_map):
    """
    Parent-first traversal (BFS) with timestamp secondary ordering.
    """
    roots = _find_roots(nodes, parents)
    roots.sort(
        key=lambda rid: (
            (nodes[rid].get("message") or {}).get("create_time") is None,
            (nodes[rid].get("message") or {}).get("create_time"),
            rid,
        )
    )

    # BFS queue
    queue = deque(roots)
    order: list[str] = []
    seen = set()

    while queue:
        nid = queue.popleft()
        if nid in seen:
            continue
        seen.add(nid)
        order.append(nid)

        # sort children by timestamp fallback
        kids = children_map.get(nid, [])
        kids.sort(
            key=lambda cid: (
                (nodes[cid].get("message") or {}).get("create_time") is None,
                (nodes[cid].get("message") or {}).get("create_time"),
                cid,
            )
        )
        queue.extend(kids)

    # fallback: nodes not reached by BFS (rare)
    for nid in nodes:
        if nid not in seen:
            order.append(nid)

    return order


# ============================================================
#  Main Adapter
# ============================================================

def _derive_conversation_id(conversation: dict, *, source: str | None = None) -> str:
    conv_id = (
        conversation.get("conversation_id")
        or conversation.get("id")
        or conversation.get("uuid")
    )
    if isinstance(conv_id, str) and conv_id:
        return conv_id

    if source:
        return Path(source).stem

    title = conversation.get("title") or ""
    ct = conversation.get("create_time") or conversation.get("update_time") or ""
    seed = f"{title}|{ct}".encode("utf-8", errors="ignore")
    return sha1(seed).hexdigest()[:12] if seed else "unknown"


def _to_epoch_ms(value: t.Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value) * 1000)
    except Exception:
        return None


def adapter(conversation: dict, *, source: str | None = None) -> list[dict]:
    conv_id = _derive_conversation_id(conversation, source=source)

    mapping = conversation.get("mapping")
    if not isinstance(mapping, dict):
        return []

    # ---- extract valid nodes ----
    nodes = _extract_nodes(mapping)

    if not nodes:
        return []

    # ---- build graph ----
    parents, children_map = _build_graph(nodes)

    # ---- linearize ----
    order = _linearize(nodes, parents, children_map)

    # ---- build final messages ----
    out: list[dict] = []
    for node_id in order:
        node = nodes[node_id]
        msg = node.get("message")
        if msg is None:
            # structural node (UI-only)
            continue
        if not isinstance(msg, dict):
            continue

        author = msg.get("author") or {}
        role = author.get("role") or msg.get("role") or "unknown"
        if not isinstance(role, str) or not role:
            role = "unknown"

        content = msg.get("content") or {}
        if not isinstance(content, dict):
            content = {}
        content_type = content.get("content_type") if isinstance(content.get("content_type"), str) else "text"
        raw_parts = content.get("parts")
        if isinstance(raw_parts, list):
            parts = [str(p) for p in raw_parts if isinstance(p, str)]
        else:
            parts = []

        ts = _to_epoch_ms(msg.get("create_time") or node.get("create_time"))
        if ts is None:
            # create_time is required for stable ordering in normalized schema
            continue

        text = "\n".join(parts)

        entry = {
            "conversation_id": conv_id,
            "message_id": msg.get("id") or node_id,
            "parent_id": node.get("parent") if isinstance(node.get("parent"), str) else None,
            "role": role,
            "ts": ts,  # epoch milliseconds
            "content": {"content_type": content_type, "parts": parts},
            "text": text,
        }

        out.append(json_safe(entry))

    out.sort(key=lambda m: (m.get("ts") is None, m.get("ts"), m.get("message_id") or ""))
    return out


def get_adapter():
    return adapter
