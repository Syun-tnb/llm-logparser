# src/llm_logparser/providers/openai/adapter.py
from __future__ import annotations
import typing as t
from collections import defaultdict, deque

from .utils import normalize_text, json_safe


PREFERRED_KEYS = ("summary", "result", "user_profile", "user_instructions")


# ============================================================
#  Manifest & Policy
# ============================================================

def get_manifest() -> dict:
    return {
        "schema_version": "1.2",
        "provider": "openai",
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
    """extract node_id → node_info safely."""
    nodes = {}
    for node_id, node in mapping.items():
        if not isinstance(node, dict):
            continue
        msg = node.get("message")
        if not isinstance(msg, dict):
            continue

        msg_id = msg.get("id") or node_id

        # REAL skip rules（rootノードのみ）
        if msg_id in ("client-created-root", "root"):
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
                nodes[cid]["message"].get("create_time") is None,
                nodes[cid]["message"].get("create_time"),
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

def adapter(conversation: dict) -> list[dict]:
    conv_id = (
        conversation.get("id")
        or conversation.get("conversation_id")
        or conversation.get("uuid")
        or "unknown"
    )

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

        author = msg.get("author") or {}
        author_role = author.get("role") or msg.get("role") or "unknown"

        content = msg.get("content")

        # timestamp resolution
        ts = (
            msg.get("create_time")
            or node.get("create_time")
            or msg.get("end_turn_time")
            or msg.get("timestamp")
        )

        text = normalize_text(content, preferred_keys=PREFERRED_KEYS, allow_loose=True)

        mmeta = msg.get("metadata") or {}
        model = (
            mmeta.get("model_slug")
            or mmeta.get("default_model_slug")
            or mmeta.get("model")
            or msg.get("model")
            or "unknown"
        )

        meta = {
            "provider": "openai",
            "model": model,
            "source": mmeta.get("source") or node.get("type") or None,
            "relations": {
                "parent": parents.get(node_id),
                "children": children_map.get(node_id, []),
            },
        }

        entry = {
            "conversation_id": conv_id,
            "message_id": msg.get("id") or node_id,
            "author_role": author_role,
            "text": text or "",
            "ts": ts,
            "meta": meta,
        }

        out.append(json_safe(entry))

    return out


def get_adapter():
    return adapter
