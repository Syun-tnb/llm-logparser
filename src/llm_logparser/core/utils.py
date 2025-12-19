# src/llm_logparser/utils.py
from __future__ import annotations
import re

_IEC = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3}
_SI  = {"KB": 1000, "MB": 1000**2, "GB": 1000**3}
_IEC_WORDS = {"KIB": "K", "MIB": "M", "GIB": "G"}

def parse_size_expr(expr: str) -> int:
    """
    例: "4M", "512K", "1G", "4096", "4MB", "4MiB", "512KiB", "512KB"
    大文字小文字は不問。
    """
    s = expr.strip().upper()
    m = re.match(r"^\s*(\d+)\s*([KMG]?)(I?B)?\s*$", s)
    if not m:
        # "4MB"/"4MiB" など別表記の救済
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
    # Windows禁則 + 制御文字
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", name)
    # 連続空白の整理
    name = re.sub(r"\s+", " ", name).strip()
    if len(name) > max_len:
        root, ext = (name, "")
        if "." in name:
            root, ext = name.rsplit(".", 1); ext = "." + ext
        name = root[: max_len - len(ext) - 3] + "..." + ext
    return name
