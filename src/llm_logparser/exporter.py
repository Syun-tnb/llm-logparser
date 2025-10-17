# src/llm_logparser/exporter.py
import json, re
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

try:
    from zoneinfo import ZoneInfo  # Py3.9+
except Exception:
    ZoneInfo = None

from .utils import sanitize_text, render_text

_control = re.compile(r'[\x00-\x08\x0B\x0C\x0E-\x1F]')

def export_md_stream(
    jsonl_path: str | Path,
    outdir: str | Path,
    conv_id: Optional[str] = None,
    split_by: Optional[str] = "conv",  # "conv" または None
) -> List[str]:
    """
    JSONLをストリームで読み、Markdownを逐次書き出す。
    - split_by="conv": conv_idごとにファイルを分割
    - split_by=None : 単一ファイル（conv_idが未指定なら最初の行のconv_idを採用）
    戻り値: 生成したファイルパスのリスト
    """
    outdir = Path(outdir)
    jsonl_path = Path(jsonl_path)

    writers: dict[str, any] = {}

    def _get_writer(cid: str):
        if cid in writers:
            return writers[cid]
        tdir = outdir / "threads" / cid
        tdir.mkdir(parents=True, exist_ok=True)
        p = tdir / f"thread-{cid}.md"
        f = p.open("w", encoding="utf-8")
        f.write(f"# Conversation {cid}\n")
        writers[cid] = f
        return f

    created_paths: list[str] = []

    with jsonl_path.open("r", encoding="utf-8", errors="replace") as f:
        single_cid: Optional[str] = conv_id
        for i, line in enumerate(f, 1):
            s = line.rstrip("\n").rstrip("\r")
            try:
                obj = json.loads(s)
            except Exception:
                s2 = _control.sub(" ", s)
                obj = json.loads(s2)  # ここで失敗したら例外にして良い

            cid = obj.get("conv_id") or "conv"
            if split_by == "conv":
                w = _get_writer(cid)
            else:
                if single_cid is None:
                    single_cid = conv_id or cid
                w = _get_writer(single_cid)

            role = sanitize_text(obj.get("role", ""))
            ts   = sanitize_text(obj.get("ts", ""))
            raw  = sanitize_text(obj.get("raw", ""))

            w.write(f"## {role} · {ts}\n")
            w.write(f"{render_text(raw, role, obj)}\n\n---\n")

    # close & collect paths
    for cid, f in writers.items():
        path = f.name
        f.close()
        created_paths.append(path)

    return created_paths

def _fmt_ts(ts: str) -> str:
    # ts は ISO8601 (UTC) 前提
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return ts  # 壊れてたら素通し

def export_txt(entries: list[dict], out_dir: str, conv_id: str) -> Path:
    out = Path(out_dir) / "threads" / conv_id
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"thread-{conv_id}.txt"

    lines = []
    for m in sorted(entries, key=lambda x: x.get("ts","")):
        role = m.get("role","unknown")
        ts   = _fmt_ts(m.get("ts",""))
        raw  = m.get("raw","")
        lines.append(f"[{ts}] {role}:\n{render_text(raw, role, m)}\n")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path

def export_md(entries: list[dict], out_dir: str, conv_id: str, title: str | None = None) -> Path:
    out = Path(out_dir) / "threads" / conv_id
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"thread-{conv_id}.md"

    title = title or f"Conversation {conv_id}"
    md = [f"# {title}\n"]

    for m in sorted(entries, key=lambda x: x.get("ts","")):
        role = m.get("role","unknown")
        ts   = _fmt_ts(m.get("ts",""))
        raw  = m.get("raw","")
        md.append(f"## {role} · {ts}\n\n{render_text(raw, role, m)}\n")
        md.append("---\n")

    Path(path).write_text("\n".join(md), encoding="utf-8")
    return path

def _to_iso_z(ts_epoch_ms: int) -> str:
    return datetime.fromtimestamp(ts_epoch_ms/1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _fmt_local(ts_ms: int, tzname: str) -> str:
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(ts_ms/1000, tz=timezone.utc)
    if ZoneInfo:
        try:
            dt = dt.astimezone(ZoneInfo(tzname))
        except Exception:
            pass  # フォールバック：UTCのまま
    return dt.strftime("%Y-%m-%d %H:%M")

def _yaml_kv(k, v, indent=0):
    pad = "  " * indent
    if isinstance(v, dict):
        lines = [f"{pad}{k}:"]
        for kk in v:
            lines += _yaml_kv(kk, v[kk], indent+1)
        return lines
    if isinstance(v, list):
        if all(isinstance(x, str) and len(x) < 24 for x in v) and len(v) <= 5:
            items = ", ".join([f'"{x}"' for x in v])
            return [f'{pad}{k}: [{items}]']
        else:
            out = [f"{pad}{k}:"]
            out += [f'{pad}- "{str(x)}"' for x in v]
            return out
    if isinstance(v, (int, float)):
        return [f"{pad}{k}: {v}"]
    return [f'{pad}{k}: "{str(v)}"']

def _yaml_frontmatter(front: dict) -> str:
    # 固定キー順（仕様と一致させる）
    order = ["thread","provider","messages","range","models","locale","timezone","schema_version"]
    lines = ["---"]
    for k in order:
        if k in front:
            lines += _yaml_kv(k, front[k])
    lines.append("---")
    return "\n".join(lines) + "\n\n"

def _render_message(msg: Dict[str, Any], tzname: str) -> str:
    # 期待フィールド: ts(ms), author_role, author_name?, content
    when = _fmt_local(msg["ts"], tzname)
    role = msg.get("author_role", "user")
    name = msg.get("author_name")
    title = f"## [{when}] {role}" + (f" [{name}]" if name else "")
    body = msg.get("content", "")
    # 本文はverbatim（URL/コードブロックは入力どおり）
    return f"{title}\n{render_text(body, role, msg)}\n\n"

def export_threads(groups: Dict[str, List[Dict[str, Any]]],
                   outdir: str,
                   provider: str,
                   split_by: str = "size",
                   size_mb: int = 20,
                   max_msgs: int = 8000,
                   locale: str = "en-US",
                   tzname: str = "Asia/Tokyo"):
    """
    groups: { conversation_id: [ {ts, author_role, author_name?, content, ...}, ... ] }
    """
    base = Path(outdir) / "output" / provider
    for conv_id, msgs in groups.items():
        msgs = sorted(msgs, key=lambda m: m["ts"])
        part = 1
        chunk: List[Dict[str, Any]] = []
        acc_bytes = 0

        def flush_chunk():
            nonlocal part, chunk, acc_bytes
            if not chunk:
                return
            start_iso = _to_iso_z(chunk[0]["ts"])
            end_iso   = _to_iso_z(chunk[-1]["ts"])
            # chunk_key: 日付ベース + 連番（シンプル）
            chunk_key = f"{datetime.now(timezone.utc).date().isoformat()}_part{part:02d}"

            out_dir = base / f"thread-{conv_id}"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"thread-{conv_id}__{chunk_key}.md"

            front = {
                "thread": conv_id,
                "provider": provider,
                "messages": len(chunk),
                "range": {"start": start_iso, "end": end_iso},
                "locale": locale,
                "timezone": tzname,
                "schema_version": "1.0"
            }

            # Front Matter
            buf = _yaml_frontmatter(front)
            # Messages
            for m in chunk:
                buf += _render_message(m, tzname)

            out_path.write_text(buf, encoding="utf-8")
            part += 1
            chunk = []
            acc_bytes = 0

        for m in msgs:
            rendered = _render_message(m, tzname)
            b = len(rendered.encode("utf-8"))
            if (split_by == "size" and acc_bytes + b > size_mb * 1024 * 1024) or \
               (split_by == "count" and len(chunk) >= max_msgs):
                flush_chunk()
            chunk.append(m)
            acc_bytes += b
        flush_chunk()
