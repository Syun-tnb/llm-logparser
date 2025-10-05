# src/llm_logparser/exporter.py
from pathlib import Path
from datetime import datetime, timezone

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
        lines.append(f"[{ts}] {role}:\n{raw}\n")
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
        md.append(f"## {role} · {ts}\n\n{raw}\n")
        md.append("---\n")

    Path(path).write_text("\n".join(md), encoding="utf-8")
    return path
