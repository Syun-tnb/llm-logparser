# src/llm_logparser/core/utils.py
from __future__ import annotations
import hashlib
import re
from pathlib import Path

_IEC = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3}
_SI  = {"KB": 1000, "MB": 1000**2, "GB": 1000**3}
_IEC_WORDS = {"KIB": "K", "MIB": "M", "GIB": "G"}


def format_display_path(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve())

def parse_size_expr(expr: str) -> int:
    """
    Example: "4M", "512K", "1G", "4096", "4MB", "4MiB", "512KiB", "512KB"
    Case-insensitive.
    """
    s = expr.strip().upper()
    m = re.match(r"^\s*(\d+)\s*([KMG]?)(I?B)?\s*$", s)
    if not m:
        # Fallback for alternative notations like "4MB"/"4MiB"
        m2 = re.match(r"^\s*(\d+)\s*([KMG])(I?B)\s*$", s)
        if not m2:
            raise ValueError(f"Invalid size: {expr}")
        n, u, ib = m2.groups()
    else:
        n, u, ib = m.groups()
    n = int(n)
    if ib is None:  # "4M" / "512K"
        return n * _IEC.get(u, 1)
    if ib == "B" and u in ("K", "M", "G"):  # "KB/MB/GB" (SI)
        return n * _SI[u + "B"]
    if ib == "IB":  # "KiB/MiB/GiB" (IEC)
        u = _IEC_WORDS.get(u + "IB", u)
        return n * _IEC.get(u, 1)
    return n

def format_bytes(n: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    v = float(n); i = 0
    while v >= 1024 and i < len(units) - 1:
        v /= 1024.0; i += 1
    return f"{v:.1f}{units[i]}"

def sanitize_filename(name: str, max_len: int = 120) -> str:
    # Windows forbidden + control characters
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", name)
    # Cleanup consecutive whitespaces
    name = re.sub(r"\s+", " ", name).strip()
    if len(name) > max_len:
        root, ext = (name, "")
        if "." in name:
            root, ext = name.rsplit(".", 1); ext = "." + ext
        name = root[: max_len - len(ext) - 3] + "..." + ext
    return name

def shorten_id(original_id: str | None, length: int = 12) -> str:
    """
    Hash an identifier with SHA-256 and truncate it to a fixed length.

    The default 12 hex characters provide a compact, deterministic ID that is
    suitable for reducing JSONL size while preserving linkage consistency.
    """
    if not original_id:
        return ""
    return hashlib.sha256(original_id.encode()).hexdigest()[:length]
