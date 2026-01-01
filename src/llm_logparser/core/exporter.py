# src/llm_logparser/exporter.py
from __future__ import annotations

import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Iterable, List, Dict, Any, Optional, Literal

from .utils import parse_size_expr, format_bytes, sanitize_filename

def _ts_to_seconds(ts: float | int | None) -> float | None:
    if ts is None:
        return None
    try:
        value = float(ts)
    except Exception:
        return None
    # Heuristic: epoch ms is ~1e12, epoch sec is ~1e9
    return value / 1000.0 if value >= 1e11 else value

def _to_iso_utc(ts: float | int | None) -> str:
    ts_sec = _ts_to_seconds(ts)
    if ts_sec is None:
        return ""
    return datetime.fromtimestamp(ts_sec, tz=timezone.utc).isoformat()

def _to_local_human(ts: float | int | None, tz=timezone.utc) -> str:
    ts_sec = _ts_to_seconds(ts)
    if ts_sec is None:
        return ""
    dt = datetime.fromtimestamp(ts_sec, tz=tz)
    return dt.strftime("%Y-%m-%d %H:%M")

def _as_yaml_list(items: Iterable[str]) -> str:
    # YAMLの安全な配列形式（クォート付与）
    return "[" + ", ".join(f'"{s}"' for s in items) + "]"

@dataclass
class ExportPolicy:
    """Export behavior toggles for Markdown generation."""
    formatting: Literal["none", "light"] = "light"

def _render_message_text(raw: str, policy: ExportPolicy) -> str:
    """
    軽量整形:
    - 連続空行を 1 行に圧縮
    - コードブロック (```...) を未閉鎖なら自動クローズ
    - Markdown構文（**bold** など）は極力そのまま保持
    """
    if policy.formatting == "none":
        return raw

    lines = raw.splitlines()
    out: List[str] = []
    in_code = False
    blank_streak = 0

    for line in lines:
        current = line.rstrip("\n")

        # コードフェンス検出（インデントありも許容）
        if current.lstrip().startswith("```"):
            in_code = not in_code
            out.append(current)
            blank_streak = 0
            continue

        if not in_code:
            if current.strip() == "":
                # 連続空行は 1 行まで
                if blank_streak == 0:
                    out.append("")
                blank_streak += 1
            else:
                out.append(current)
                blank_streak = 0
        else:
            # コードブロック内はそのまま
            out.append(current)

    # 開きっぱなしの ``` があれば自動クローズ
    if in_code:
        out.append("```")

    # 末尾の余計な空行は削る
    while out and out[-1] == "":
        out.pop()

    return "\n".join(out)

def _resolve_split(opts: Dict[str, Any]) -> Dict[str, Any]:
    """
    {"split": "size=4M"|"count=1500"|"auto"|None,
     "split_soft_overflow": float, "split_hard": bool, "split_preview": bool,
     "tiny_tail_threshold": int}
    """
    conf = {
        "mode": None, "size_limit": None, "count_limit": None,
        "soft_overflow": float(opts.get("split_soft_overflow", 0.20)),
        "hard": bool(opts.get("split_hard", False)),
        "preview": bool(opts.get("split_preview", False)),
        "tiny_tail_threshold": int(opts.get("tiny_tail_threshold", 20)),
    }
    spec = opts.get("split")
    if not spec:
        return conf
    s = str(spec).strip().lower()
    if s == "auto":
        conf["mode"] = "auto"
    elif s.startswith("size="):
        conf["mode"] = "size"
        conf["size_limit"] = parse_size_expr(s.split("=", 1)[1])
    elif s.startswith("count="):
        conf["mode"] = "count"
        conf["count_limit"] = int(s.split("=", 1)[1])
    else:
        raise ValueError(f"invalid --split: {spec}")
    return conf

def export_thread_md(
    parsed_path: Path,
    out_path: Path,           # 単一出力時のファイルパス（分割時はディレクトリ基準）
    tz=timezone.utc,
    *,
    formatting: str = "light",
    **opts: Any
) -> List[Path]:
    """
    parsed.jsonl → Markdown（分割対応）
    - 分割なし: 従来どおり out_path に1ファイル
    - 分割あり: out_path.parent に thread-<cid>__partXX.md を複数出力
    戻り値: 生成したファイルの List[Path]
    """
    logger = logging.getLogger("exporter")
    policy = ExportPolicy(formatting="none" if formatting is None else formatting)

    messages: List[Dict[str, Any]] = []
    thread_meta: Dict[str, Any] | None = None
    models = set()
    ts_min: float | int | None = None
    ts_max: float | int | None = None

    with parsed_path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                # 壊れ行はスキップ（将来: logger.warning へ）
                continue

            rt = row.get("record_type")
            if rt == "thread":
                if thread_meta is None:
                    thread_meta = row
                continue
            elif rt == "message":
                messages.append(row)
                m = row.get("meta", {}).get("model")
                if m:
                    models.add(m)
                ts = row.get("ts")
                if isinstance(ts, (int, float)):
                    ts_min = ts if ts_min is None else min(ts_min, ts)
                    ts_max = ts if ts_max is None else max(ts_max, ts)

    if not thread_meta:
        raise RuntimeError("parsed.jsonl missing thread record_type on first row.")

    # 念のためts昇順ソート（Noneは末尾）
    messages.sort(key=lambda r: (r.get("ts") is None, r.get("ts")))

    conv_id = thread_meta.get("conversation_id", "unknown")
    provider = thread_meta.get("provider_id", "unknown")

    # 本文ブロックを先に作る（二重レンダ回避）
    body_blocks: List[str] = []
    for m in messages:
        role = m.get("role", "unknown")
        ts_human = _to_local_human(m.get("ts"), tz=tz)

        # normally adapters MUST populate `text`
        # (contract: exporter should not reconstruct text)
        # this fallback exists only as a safety net for broken adapters / legacy data
        raw_text = (m.get("text") or "")
        if not raw_text:
            parts = (m.get("content") or {}).get("parts")
            if isinstance(parts, list):
                raw_text = "\n".join(str(p) for p in parts)
        text = _render_message_text(raw_text, policy)

        message_id = m.get("message_id") or ""
        parent_id = m.get("parent_id")
        parent_text = parent_id if isinstance(parent_id, str) else ""
        meta_lines = []
        if message_id:
            meta_lines.append(f"- message_id: {message_id}")
        if parent_text:
            meta_lines.append(f"- parent_id: {parent_text}")
        meta = ("\n".join(meta_lines) + "\n\n") if meta_lines else ""
        block = f"## [{role}] {ts_human}\n{meta}{text}\n\n"
        body_blocks.append(block)

    # 分割設定
    split_conf = _resolve_split(opts)

    # プレビュー（総バイト概算）
    total_preview = len("".join(body_blocks).encode("utf-8"))
    if split_conf["preview"]:
        logger.info(f"[preview] ~{format_bytes(total_preview)} / {len(messages)} messages")
        if split_conf["mode"] in ("auto", "size"):
            size_limit = split_conf["size_limit"] or parse_size_expr("4M")
            est = max(1, total_preview // max(1, size_limit))
            logger.info(f"[preview] estimated parts: {est}")
        return []

    # 分割なし（既存互換）
    if not split_conf["mode"]:
        fm_lines = [
            "---",
            f"thread: {conv_id}",
            f"provider: {provider}",
            f"messages: {len(messages)}",
            f"models: {_as_yaml_list(sorted(models))}",
            f"range: {_to_iso_utc(ts_min)} 〜 {_to_iso_utc(ts_max)}",
            "---",
            "",
        ]
        md = "\n".join(fm_lines) + "".join(body_blocks)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md, encoding="utf-8")
        logger.info(f"  - {out_path.name} (messages={len(messages)}, ~{format_bytes(len(md.encode('utf-8')))})")
        return [out_path]

    # 分割あり
    size_limit = split_conf.get("size_limit")
    count_limit = split_conf.get("count_limit")

    # autoは size=4M & count=1500
    if split_conf["mode"] == "auto":
        size_limit = size_limit or parse_size_expr("4M")
        count_limit = count_limit or 1500

    fm_overhead_approx = 1024  # 近似。--split-hard時は仮レンダで厳密計測
    parts: List[List[str]] = []
    buf_blocks: List[str] = []
    buf_bytes_body = 0
    idx = 0

    def flush():
        nonlocal buf_blocks, buf_bytes_body, idx, parts
        if not buf_blocks:
            return
        parts.append(buf_blocks)
        idx += 1
        buf_blocks, buf_bytes_body = [], 0

    def would_be_tiny_after(next_i: int) -> bool:
        remain = len(body_blocks) - next_i
        return remain <= split_conf["tiny_tail_threshold"]

    def hard_will_overflow(next_block: str) -> bool:
        if not size_limit:
            return False
        if split_conf["hard"]:
            # front-matter込みで仮レンダして厳密長を判定
            fm = [
                "---",
                f"thread: {conv_id}",
                f"provider: {provider}",
                f"models: {_as_yaml_list(sorted(models))}",
                f"message_count: {len(buf_blocks) + 1}",
                f"range: {_to_iso_utc(ts_min)} 〜 {_to_iso_utc(ts_max)}",
                f"part_index: {idx + 1}",
                f"part_total: 0",
                f"generated_at_utc: {datetime.now(timezone.utc).isoformat()}",
                f"tz: {tz.key if hasattr(tz, 'key') else str(tz)}",
                "---",
                "",
            ]
            tmp = "".join(fm) + "".join(buf_blocks + [next_block])
            return len(tmp.encode("utf-8")) > size_limit
        else:
            return (buf_bytes_body + len(next_block.encode("utf-8")) + fm_overhead_approx) > size_limit

    for i, block in enumerate(body_blocks):
        bsz = len(block.encode("utf-8"))
        # size優先 → count補助
        over_size = bool(size_limit) and hard_will_overflow(block)
        over_count = (not over_size) and bool(count_limit) and (len(buf_blocks) >= int(count_limit))

        if over_size or over_count:
            within_soft = bool(size_limit) and (not over_count) and \
                          ((buf_bytes_body + bsz + fm_overhead_approx) <= int(size_limit * (1 + split_conf["soft_overflow"])))
            small_tail = would_be_tiny_after(i + 1)
            if not split_conf["hard"] and (within_soft or small_tail):
                buf_blocks.append(block); buf_bytes_body += bsz
                continue
            flush()

        buf_blocks.append(block); buf_bytes_body += bsz

    flush()

    part_total = len(parts)
    if part_total == 0:  # 念のため
        parts = [body_blocks]
        part_total = 1

    outdir = out_path.parent
    outdir.mkdir(parents=True, exist_ok=True)
    base = f"thread-{conv_id}"
    paths: List[Path] = []

    for pidx, blocks in enumerate(parts, start=1):
        fm = [
            "---",
            f"thread: {conv_id}",
            f"provider: {provider}",
            f"models: {_as_yaml_list(sorted(models))}",
            f"message_count: {len(blocks)}",
            f"range: {_to_iso_utc(ts_min)} 〜 {_to_iso_utc(ts_max)}",
            f"part_index: {pidx}",
            f"part_total: {part_total}",
            f"generated_at_utc: {datetime.now(timezone.utc).isoformat()}",
            f"tz: {tz.key if hasattr(tz, 'key') else str(tz)}",
            "---",
            "",
        ]
        page = "".join(fm) + "".join(blocks)
        suffix = "" if part_total == 1 else f"__part{pidx:02d}"
        out_name = sanitize_filename(f"{base}{suffix}.md")
        out_file = outdir / out_name
        out_file.write_text(page, encoding="utf-8")
        logger.info(f"  - {out_name} (messages={len(blocks)}, ~{format_bytes(len(page.encode('utf-8')))})")
        paths.append(out_file)

    return paths
