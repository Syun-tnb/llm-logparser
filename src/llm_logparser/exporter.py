# src/llm_logparser/exporter.py
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Iterable, List, Dict, Any

def _to_iso_utc(ts: float | int | None) -> str:
    if ts is None:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

def _to_local_human(ts: float | int | None, tz=timezone.utc) -> str:
    if ts is None:
        return ""
    dt = datetime.fromtimestamp(ts, tz=tz)
    return dt.strftime("%Y-%m-%d %H:%M")

def _as_yaml_list(items: Iterable[str]) -> str:
    # YAMLの安全な配列形式（クォート付与）
    return "[" + ", ".join(f'"{s}"' for s in items) + "]"

def export_thread_md(parsed_path: Path, out_path: Path, tz=timezone.utc) -> Path:
    messages: List[Dict[str, Any]] = []
    thread_meta: Dict[str, Any] | None = None
    models = set()
    ts_min: float | int | None = None
    ts_max: float | int | None = None

    # --- read jsonl with basic validation ---
    with parsed_path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                # 壊れた行はスキップ（将来loggerに差し替え可）
                # print(f"warn: skip invalid json at line {line_no}")
                continue

            rt = row.get("record_type")
            if rt == "thread":
                # 最初のthread行を採用。2個目以降は無視
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

    # --- build front matter ---
    conv_id = thread_meta.get("conversation_id", "unknown")
    provider = thread_meta.get("provider_id", "unknown")
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
    front_matter = "\n".join(fm_lines)

    # --- build body ---
    body_lines: List[str] = []
    for m in messages:
        role = m.get("role", "unknown")
        ts_human = _to_local_human(m.get("ts"), tz=tz)
        text = (m.get("text") or "").rstrip()  # 軽整形：末尾改行を整理
        body_lines.append(f"## [{role}] {ts_human}")
        body_lines.append(text)
        body_lines.append("")  # blank line

    md = front_matter + "\n".join(body_lines)

    # --- write ---
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    return out_path
