# src/llm_logparser/cli.py
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# --- Offline-by-default guard -------------------------------------------------
def _install_offline_guard():
    import socket
    class _NoNet(socket.socket):
        def __init__(self, *a, **kw):
            raise OSError("Network disabled by llm-logparser (offline mode)")
    socket.socket = _NoNet  # type: ignore[attr-defined]

# --- Utilities ----------------------------------------------------------------
def _err(msg: str) -> None:
    print(f"[llm-logparser] ERROR: {msg}", file=sys.stderr)

def _warn(msg: str) -> None:
    print(f"[llm-logparser] WARN: {msg}", file=sys.stderr)

def _info(msg: str) -> None:
    print(f"[llm-logparser] {msg}")

def _to_epoch_ms_any(x: Any) -> int:
    """
    Accept ISO8601 string, seconds(int/float), or epoch ms(int) and return epoch ms(int).
    """
    if x is None:
        raise ValueError("timestamp is None")
    # int/float?
    if isinstance(x, (int, float)):
        # heuristic: >= 10^12 -> already ms
        if int(x) >= 10**12:
            return int(x)
        # treat as seconds
        return int(float(x) * 1000)
    # string?
    s = str(x).strip()
    # numeric string
    if s.isdigit():
        n = int(s)
        return n if n >= 10**12 else n * 1000
    # try ISO8601
    try:
        # Allow both with/without Z; fromisoformat needs tweak for Z
        if s.endswith("Z"):
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception as e:
        raise ValueError(f"unrecognized timestamp format: {x!r}") from e

def _guess_provider(provider_arg: str | None, manifest_path: Path | None) -> str:
    if provider_arg:
        return provider_arg
    # fallback: try manifest
    try:
        if manifest_path and manifest_path.exists():
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
            provs = m.get("providers")
            if isinstance(provs, list) and provs:
                return str(provs[0])
    except Exception:
        pass
    return "openai"

def _iter_input_files(paths: List[str]) -> Iterable[Path]:
    for p in paths:
        path = Path(p)
        if path.is_dir():
            for q in path.rglob("*"):
                if q.is_file() and q.suffix.lower() in {".json", ".jsonl", ".ndjson"}:
                    yield q
        else:
            yield path

def _iter_json_records(path: Path) -> Iterable[Dict[str, Any]]:
    """
    Supports:
      - .json (array or object per line fallback)
      - .jsonl/.ndjson (one JSON object per line)
    """
    suf = path.suffix.lower()
    text = path.read_text(encoding="utf-8", errors="replace")
    if suf == ".json":
        text_stripped = text.strip()
        if text_stripped.startswith("["):
            try:
                arr = json.loads(text_stripped)
                for obj in arr:
                    if isinstance(obj, dict):
                        yield obj
                    else:
                        _warn(f"{path}: skip non-object element in array")
                return
            except Exception:
                _warn(f"{path}: JSON array parse failed; trying line-by-line")
        # fallthrough: try line-by-line
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    yield obj
                else:
                    _warn(f"{path}: skip non-object line")
            except Exception:
                _warn(f"{path}: skip broken JSON line")
        return
    else:
        # jsonl / ndjson
        for i, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    yield obj
                else:
                    _warn(f"{path}:{i}: skip non-object line")
            except Exception:
                _warn(f"{path}:{i}: skip broken JSON line")
        return

def _apply_openai_mapping(obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    Minimal normalizer for OpenAI export-like objects.
    Targets unified schema: conversation_id, message_id, ts(ms), author_role, author_name?, model?, content
    """
    # conversation / message id
    conv = obj.get("conversation_id") or obj.get("conv_id") or obj.get("thread_id") or "unknown"
    mid = obj.get("message_id") or obj.get("id") or obj.get("uuid") or f"{conv}-{obj.get('index','')}"
    # timestamp
    ts_src = obj.get("create_time") or obj.get("ts") or obj.get("created_at")
    try:
        ts_ms = _to_epoch_ms_any(ts_src)
    except Exception:
        # last resort: now
        ts_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        _warn(f"missing/invalid timestamp; using now for message_id={mid}")
    # author
    author = obj.get("author") or {}
    role = author.get("role") or obj.get("role") or "user"
    name = author.get("name") or obj.get("author_name")
    # model
    model = obj.get("metadata", {}).get("model") if isinstance(obj.get("metadata"), dict) else obj.get("model")
    # content
    content = obj.get("content")
    if content is None:
        # some exports may nest content parts
        parts = obj.get("content_parts") or obj.get("parts")
        if isinstance(parts, list):
            content = "\n".join(str(p) for p in parts)
    if content is None:
        content = ""
    return {
        "conversation_id": str(conv),
        "message_id": str(mid),
        "ts": int(ts_ms),
        "author_role": str(role),
        "author_name": (str(name) if name is not None else None),
        "model": (str(model) if model is not None else None),
        "content": str(content),
    }

def _group_by_conversation(records: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    seen: set[Tuple[str, str]] = set()
    for r in records:
        u = _apply_openai_mapping(r)  # MVP: OpenAI想定（他Providerは後続）
        key = (u["conversation_id"], u["message_id"])
        if key in seen:
            # skip duplicate
            continue
        seen.add(key)
        groups.setdefault(u["conversation_id"], []).append(u)
    return groups

# --- Main ---------------------------------------------------------------------
def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="llm-logparser",
        description="LLM log parser (CLI-first, offline by default)."
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # parse
    sp = sub.add_parser("parse", help="Parse export and write Markdown.")
    sp.add_argument("--provider", default=None, help="Provider id (default: guessed from manifest or 'openai').")
    sp.add_argument("--input", "-i", nargs="+", required=True, help="Input file(s) or directory (json/jsonl/ndjson).")
    sp.add_argument("--outdir", "-o", default="artifacts", help="Root output directory (default: 'artifacts')." \
                     "Do NOT include the 'output/' segment (the tool will create 'output/<provider>/...' under it).")
    sp.add_argument("--split-by", choices=["size", "count", "none"], default="size", help="Split strategy.")
    sp.add_argument("--split-size-mb", type=int, default=20, help="Split size in MB when --split-by size.")
    sp.add_argument("--max-msgs-per-file", type=int, default=8000, help="Split count when --split-by count.")
    sp.add_argument("--locale", default="en-US", help="Locale for labels (default: en-US).")
    sp.add_argument("--timezone", default="Asia/Tokyo", help="IANA timezone for headings (default: Asia/Tokyo).")
    sp.add_argument("--dry-run", action="store_true", help="Do not write files; just show stats.")
    sp.add_argument("--config", default=None, help="(reserved) Path to config file (YAML/JSON).")
    sp.add_argument("--enable-network", action="store_true", help="Allow networking (default: disabled).")

    # --- new: export (alias of parse) ---
    se = sub.add_parser("export", help="Alias of 'parse' (same options).")
    se.add_argument("--provider", default=None, help="Provider id (default: guessed from manifest or 'openai').")
    se.add_argument("--input", "-i", nargs="+", required=True, help="Input file(s) or directory (json/jsonl/ndjson).")
    se.add_argument("--outdir", "-o", default="artifacts", help="Root output directory (default: 'artifacts'). " \
                     "Do NOT include the 'output/' segment (the tool will create 'output/<provider>/...' under it).")

    se.add_argument("--split-by", choices=["size", "count", "none"], default="size", help="Split strategy.")
    se.add_argument("--split-size-mb", type=int, default=20, help="Split size in MB when --split-by size.")
    se.add_argument("--max-msgs-per-file", type=int, default=8000, help="Split count when --split-by count.")
    se.add_argument("--locale", default="en-US", help="Locale for labels (default: en-US).")
    se.add_argument("--timezone", default="Asia/Tokyo", help="IANA timezone for headings (default: Asia/Tokyo).")
    se.add_argument("--dry-run", action="store_true", help="Do not write files; just show stats.")
    se.add_argument("--enable-network", action="store_true", help="Allow networking (default: disabled).")

    # config (placeholder subcommand for future)
    sc = sub.add_parser("config", help="Show or edit runtime config (placeholder).")
    sc.add_argument("--show", action="store_true", help="Print effective config and exit.")

    return p

def cmd_parse(args: argparse.Namespace) -> int:
    # offline-by-default
    if not getattr(args, "enable_network", False):
        _install_offline_guard()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    # provider guess (from --provider or manifest.json if exists)
    manifest_path = outdir / "manifest.json"
    provider = _guess_provider(args.provider, manifest_path)

    # gather records
    input_files = list(_iter_input_files(args.input))
    if not input_files:
        _err("no input files found")
        return 2

    total = 0
    records: List[Dict[str, Any]] = []
    for f in input_files:
        for obj in _iter_json_records(f):
            records.append(obj)
            total += 1

    if total == 0:
        _warn("no records parsed from input")
    _info(f"parsed {total} records from {len(input_files)} file(s)")

    # normalize & group
    groups = _group_by_conversation(records)
    conv_count = len(groups)
    msg_count = sum(len(v) for v in groups.values())
    _info(f"grouped into {conv_count} conversation(s), {msg_count} message(s)")

    if getattr(args, "dry_run", False):
        _info("dry-run enabled; no files will be written")
        return 0

    # export
    try:
        # lazy import to keep cli.py standalone-friendly during tests
        from . import exporter  # type: ignore
    except Exception as e:
        _err(f"failed to import exporter module: {e}")
        return 3

    split_by = args.split_by
    if split_by == "none":
        # emulate "very big size" to write one file per conversation
        split_by = "size"
        args.split_size_mb = 10_000  # ~10GB

    try:
        exporter.export_threads(
            groups=groups,
            outdir=str(outdir),
            provider=provider,
            split_by=split_by,
            size_mb=int(args.split_size_mb),
            max_msgs=int(args.max_msgs_per_file),
            locale=str(args.locale),
            tzname=str(args.timezone),
        )
    except Exception as e:
        _err(f"export failed: {e}")
        return 5

    _info(f"export finished: {outdir}/output/{provider}/")
    return 0

def cmd_config(args: argparse.Namespace) -> int:
    # Placeholder: later will show merged runtime config
    if args.show:
        data = {
            "schema_version": 1,
            "defaults": {
                "outdir": "artifacts",
                "split_by": "size",
                "split_size_mb": 20,
                "max_msgs_per_file": 8000,
                "locale": "en-US",
                "timezone": "Asia/Tokyo",
                "offline": True,
            }
        }
        print(json.dumps(data, indent=2))
        return 0
    _info("config command is a placeholder in MVP")
    return 0

def main(argv: List[str] | None = None) -> int:
    parser = build_argparser()
    args = parser.parse_args(argv)
    if args.cmd == "parse":
        return cmd_parse(args)
    elif args.cmd == "export":
     # export は parse のエイリアスとして同じ処理を呼ぶ
        return cmd_parse(args)
    elif args.cmd == "config":
        return cmd_config(args)
    else:
        _err(f"unknown command: {args.cmd}")
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
