from __future__ import annotations
import re
import json
from typing import Tuple

# 内部定数（非公開）
_CONTROL = re.compile(r'[\x00-\x08\x0B\x0C\x0E-\x1F]')
_NBSP = "\u00A0"

# ==== 内部実装は1箇所に集約 ====
def _sanitize_impl(s: str) -> str:
    if not isinstance(s, str):
        s = str(s)
    # NBSP→通常空白、制御文字除去、trim
    s = s.replace(_NBSP, " ")
    s = _CONTROL.sub("", s)
    return s.strip()

# ==== 公開ラッパ（契約名） ====
def sanitize_text(s: str) -> str:
    """公開API：テキストのサニタイズ"""
    return _sanitize_impl(s)

def _join(parts) -> str:
    return _sanitize_impl("\n".join([p for p in parts if p is not None]))

def _strict_parts_to_text(parts) -> str:
    out = []
    for p in parts:
        if isinstance(p, str):
            out.append(p)
        elif isinstance(p, dict) and isinstance(p.get("text"), str):
            out.append(p["text"])
    return _join(out) if out else ""

def _loose_parts_to_text(parts) -> str:
    out = []
    for p in parts:
        if isinstance(p, str):
            out.append(p)
        elif isinstance(p, dict):
            if isinstance(p.get("text"), str):
                out.append(p["text"])
            else:
                # 短縮JSON化（視認性優先）
                out.append(json.dumps({k: p[k] for k in ("type", "name", "id") if k in p}))
        else:
            out.append(str(p))
    return _join(out)

def normalize_text(content: object, *, preferred_keys: tuple[str, ...] = (), allow_loose: bool = True) -> str:
    """
    汎用テキスト正規化（プロバイダ非依存）
    - preferred_keys: そのプロバイダで優先抽出したいキー（例: ("summary","result",...)）
    - allow_loose: 厳格抽出で空の時、緩い可視化（dict短縮JSON等）を許可
    """
    if content is None:
        return ""

    # 1) 素の文字列
    if isinstance(content, str):
        return _sanitize_impl(content)

    # 2) dict
    if isinstance(content, dict):
        # parts 優先（content_typeは見ない）
        if isinstance(content.get("parts"), list):
            s = _strict_parts_to_text(content["parts"])
            if not s and allow_loose:
                s = _loose_parts_to_text(content["parts"])
            if s:
                return s

        # プロバイダ好みのキーを優先
        for k in preferred_keys:
            v = content.get(k)
            if isinstance(v, str) and v.strip():
                return _sanitize_impl(v)

        # 単純 text
        if isinstance(content.get("text"), str):
            return _sanitize_impl(content["text"])

        # 浅い回収（type==text / 内側parts）
        out = []
        for v in content.values():
            if isinstance(v, str) and v.strip():
                out.append(v)
            elif isinstance(v, dict):
                if v.get("type") == "text" and isinstance(v.get("text"), str):
                    out.append(v["text"])
                if isinstance(v.get("parts"), list):
                    s = _strict_parts_to_text(v["parts"]) or (allow_loose and _loose_parts_to_text(v["parts"])) or ""
                    if s: out.append(s)
            elif isinstance(v, list):
                s = _strict_parts_to_text(v) or (allow_loose and _loose_parts_to_text(v)) or ""
                if s: out.append(s)
        return _join(out) if out else ""

    # 3) list
    if isinstance(content, list):
        s = _strict_parts_to_text(content)
        if not s and allow_loose:
            s = _loose_parts_to_text(content)
        return s

    return ""

# 公開シンボルを固定（内部は露出しない）
__all__ = ["sanitize_text", "normalize_text"]
