# src/llm_logparser/parser.py
from __future__ import annotations
import json
import importlib
import logging
from pathlib import Path
from typing import Iterable, Dict, Any, Generator, Optional


# ------------------------------------------------------------
# 1. Provider Adapter Loader
# ------------------------------------------------------------
def load_adapter(provider: str):
    """
    動的に provider adapter をロードする。
    各 adapter モジュールは llm_logparser.providers.{provider}.adapter に配置。
    """
    mod = importlib.import_module(f"llm_logparser.providers.{provider}.adapter")
    get_adapter = getattr(mod, "get_adapter", None)
    if not get_adapter:
        raise RuntimeError(f"adapter missing for provider={provider}")

    manifest = getattr(mod, "get_manifest", lambda: {})()
    policy = getattr(mod, "get_policy", lambda: {})()
    return get_adapter(), manifest, policy


# ------------------------------------------------------------
# 2. JSON / JSONL Stream Reader
# ------------------------------------------------------------
def iter_json_records(path: Path, logger: logging.Logger) -> Generator[Dict[str, Any], None, None]:
    """巨大JSON/JSONLをストリーム的に読み込む。[]配列形式でもNDJSON形式でも処理可能。"""
    with path.open("r", encoding="utf-8") as f:
        first = f.read(1)
        f.seek(0)
        if first == "[":
            try:
                for x in json.load(f):
                    yield x
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Invalid JSON array input: {e}")
        else:
            for i, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as e:
                    logger.warning(f"skip invalid JSON line ({i}): {e}")
                    continue


# ------------------------------------------------------------
# 3. Thread JSONL Writer
# ------------------------------------------------------------
def write_thread_jsonl(
    provider: str,
    conversation_id: str,
    thread_meta: Dict[str, Any],
    messages: Iterable[Dict[str, Any]],
    out_root: Path,
    logger: logging.Logger,
) -> Path:
    """スレッド単位の parsed.jsonl を出力する。"""
    provider_dir = out_root / provider
    provider_dir.mkdir(parents=True, exist_ok=True)
    outdir = provider_dir / f"thread-{conversation_id}"
    outdir.mkdir(parents=True, exist_ok=True)
    outpath = outdir / "parsed.jsonl"

    with outpath.open("w", encoding="utf-8") as f:
        thread_row = {
            "record_type": "thread",
            "provider_id": provider,
            "conversation_id": conversation_id,
            **{k: v for k, v in thread_meta.items() if k != "conversation_id"},
        }
        f.write(json.dumps(thread_row, ensure_ascii=True) + "\n")

        for m in messages:
            row = {"record_type": "message", "provider_id": provider, **m}
            f.write(json.dumps(row, ensure_ascii=True) + "\n")

    logger.debug(f"wrote {outpath}")
    return outpath


# ------------------------------------------------------------
# 4. Main Parser
# ------------------------------------------------------------
def parse_to_jsonl(
    provider: str,
    input_path: Path,
    outdir: Path,
    dry_run: bool = False,
    fail_fast: bool = False,
    logger: Optional[logging.Logger] = None,
) -> Dict[str, Any]:
    """
    各プロバイダのエクスポートJSONを解析し、スレッド単位のJSONLファイルを生成する。
    dry_run=True の場合は出力せず統計のみ返す。
    fail_fast=True の場合は例外発生時に即停止する。
    """
    log = logger or logging.getLogger("llm_logparser.parser")
    log.info(f"Starting parse for provider={provider} (dry-run={dry_run}, fail-fast={fail_fast})")

    adapter_func, manifest, policy = load_adapter(provider)
    grouped: Dict[str, list] = {}
    error_summary = {"errors": 0, "skipped": 0, "samples": []}

    for raw in iter_json_records(input_path, log):
        try:
            for rec in adapter_func(raw):
                cid = rec.get("conversation_id")
                if not cid:
                    error_summary["skipped"] += 1
                    continue
                grouped.setdefault(cid, []).append(rec)
        except Exception as e:
            msg = f"adapter error: {e}"
            error_summary["errors"] += 1
            if len(error_summary["samples"]) < 5:
                error_summary["samples"].append(msg)
            log.warning(msg)
            if fail_fast:
                raise

    stats = {"threads": 0, "messages": 0}
    manifest_index = []
    provider_dir = outdir / provider
    provider_dir.mkdir(parents=True, exist_ok=True)

    for cid, msgs in grouped.items():
        msgs.sort(key=lambda r: (r.get("ts") is None, r.get("ts"), r.get("message_id") or ""))
        thread_meta = {"message_count": len(msgs)}

        if not dry_run:
            outpath = write_thread_jsonl(provider, cid, thread_meta, msgs, outdir, log)
        else:
            outpath = None

        stats["threads"] += 1
        stats["messages"] += len(msgs)

        ts_values = [m.get("ts") for m in msgs if isinstance(m.get("ts"), (int, float))]
        ts_min = min(ts_values) if ts_values else None
        ts_max = max(ts_values) if ts_values else None
        manifest_index.append({
            "conversation_id": cid,
            "path": f"thread-{cid}/parsed.jsonl",
            "count": len(msgs),
            "ts_min": ts_min,
            "ts_max": ts_max,
        })

    if not dry_run:
        manifest_path = provider_dir / "manifest.json"
        manifest_obj = {
            "schema_version": "1.1",
            "provider": provider,
            "policy": policy,
            "index": {"threads": manifest_index},
        }
        manifest_path.write_text(
            json.dumps(manifest_obj, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        log.info(f"manifest saved: {manifest_path}")

    log.info(f"Parsed {stats['threads']} threads, {stats['messages']} messages.")
    log.info(f"SUMMARY: {json.dumps(error_summary, ensure_ascii=False)}")
    return {**stats, **error_summary}


# ------------------------------------------------------------
# 5. CLI entry (debug only)
# ------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Parse LLM export logs to JSONL")
    parser.add_argument("--provider", required=True)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--outdir", type=Path, default=Path("artifacts/output"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")

    args = parser.parse_args()
    parse_to_jsonl(args.provider, args.input, args.outdir, dry_run=args.dry_run, fail_fast=args.fail_fast)
