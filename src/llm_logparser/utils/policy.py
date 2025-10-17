# src/llm_logparser/utils/policy.py
from typing import Optional, Tuple
from .text_normalizer import normalize_text_generic

def placeholder_for(role: Optional[str]) -> str:
    if role == "tool": return "[tool]"
    if role == "system": return "[system]"
    if role == "assistant": return "[non-text response]"
    if role == "user": return "[user content]"
    return "[empty]"

def apply_text_policy(role: Optional[str], content, text: str, *, enable_placeholder: bool = True) -> Tuple[str, dict]:
    """
    表示ポリシー：textが空のときの最小代替（プレースホルダ）。
    enable_placeholder=False なら textは空のまま返す。
    """
    meta = {}
    if text:
        return text, meta
    if not enable_placeholder:
        return "", meta
    return placeholder_for(role), {"placeholder": True}
