# src/llm_logparser/parser.py
from __future__ import annotations
import json
import importlib
import logging
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, Optional
import ijson
from datetime import datetime

# ============================================================
# 1. Error Classes
# ============================================================

class LLPError(Exception):
    """Base class for parser-related errors."""
    code = "LP2000"
    def __init__(self, msg: str, *, code: str | None = None):
        super().__init__(msg)
        self.code = code or self.code

class LLPInputError(LLPError):
    code = "LP2100"

class LLPAdapterError(LLPError):
    code = "LP2200"

class LLPWriteError(LLPError):
    code = "LP2300"


# ============================================================
# 2. Provider Adapter Loader
# ============================================================

def load_adapter(provider: str):
    """動的に provider adapter をロードする。"""
    mod = importlib.import_module(f"llm_logparser.providers.{provider}.adapter")
    get_adapter = getattr(mod, "get_adapter", None)
    if not get_adapter:
        raise LLPAdapterError(f"adapter missing for provider={provider}")
    manifest = getattr(mod, "get_manifest", lambda: {})()
    policy = getattr(mod, "get_policy", lambda: {})()
    return get_adapter(), manifest, policy


# ============================================================
# 3. JSON Stream Reader (Hybrid)
# ============================================================

def iter_json_records(path: Path, logger: logging.Logger) -> Generator[Dict[str, Any], None, None]:
    """
    巨大JSON/JSONLをストリーム的に読み込む。
    JSON配列はijsonで逐次読み取り、JSONLは行単位で処理。
    """
    try:
        with path.open("r", encoding="utf-8") as f:
            first = f.read(1)
            f.seek(0)

            # JSONL / NDJSON
            if first != "[":
                for i, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError as e:
                        logger.warning(f"skip invalid JSON line ({i}): {e}")
                        continue
                return

            # JSON array
            for i, item in enumerate(ijson.items(f, "item"), start=1):
                if not isinstance(item, dict):
                    logger.warning(f"skip invalid element ({i})")
                    continue
                yield item

    except FileNotFoundError:
        raise LLPInputError(f"input not found: {path}")
    except PermissionError:
        raise LLPInputError(f"permission denied: {path}")
    except Exception as e:
        raise LLPInputError(f"reader error: {e}")


# ============================================================
# 4. Validation / Cache Utilities
# ============================================================

def validate_message(msg: dict, *, fail_fast=False):
    """基本的なスキーマ検証。"""
    required = ["conversation_id", "role", "text"]
    for k in required:
        if k not in msg:
            if fail_fast:
                raise LLPAdapterError(f"missing required field: {k}")
            return False
    ts = msg.get("ts")
    if ts not in (None,) and not isinstance(ts, (int, float)):
        if fail_fast:
            raise LLPAdapterError("invalid timestamp type")
        return False
    return True


def load_manifest_if_exists(provider_dir: Path) -> dict:
    """既存manifestをロードしてキャッシュに利用。"""
    man_path = provider_dir / "manifest.json"
    if not man_path.exists():
        return {}
    try:
        return json.loads(man_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def should_skip_thread(conv_id: str, msgs: list, manifest_old: dict) -> bool:
    """update_timeなどで差分スキップを判定。"""
    try:
        index = manifest_old.get("index", {}).get("threads", [])
        old = next((t for t in index if t["conversation_id"] == conv_id), None)
        if not old:
            return False
        old_count = old.get("count")
        new_count = len(msgs)
        if old_count == new_count:
            return True
    except Exception:
        pass
    return False


# ============================================================
# 5. Safe Write Helper
# ============================================================

def safe_write_jsonl(outpath: Path, lines: Iterable[Dict[str, Any]], logger: logging.Logger):
    """原子的に近い方法で JSONL を書き出す。"""
    try:
        tmp = outpath.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for obj in lines:
                f.write(json.dumps(obj, ensure_ascii=True) + "\n")
        tmp.replace(outpath)
        logger.debug(f"wrote {outpath}")
    except Exception as e:
        raise LLPWriteError(f"write error: {e}")


# ============================================================
# 6. Main Parser
# ============================================================

def parse_to_jsonl(
    provider: str,
    input_path: Path,
    outdir: Path,
    *,
    dry_run: bool = False,
    fail_fast: bool = False,
    logger: Optional[logging.Logger] = None,
    progress_interval: int = 100,
) -> Dict[str, Any]:
    """
    各プロバイダのエクスポートJSONを解析し、スレッド単位のJSONLファイルを生成する。
    fail_fast=True の場合は一定数エラーで停止。
    """
    log = logger or logging.getLogger("llm_logparser.parser")
    log.info(f"Starting parse for provider={provider} (dry-run={dry_run}, fail-fast={fail_fast})")

    adapter_func, manifest, policy = load_adapter(provider)
    provider_dir = outdir / provider
    provider_dir.mkdir(parents=True, exist_ok=True)
    manifest_old = load_manifest_if_exists(provider_dir)

    grouped: Dict[str, list] = {}
    errors, skipped, count = 0, 0, 0
    sample_errors: list[str] = []

    for raw in iter_json_records(input_path, log):
        try:
            for rec in adapter_func(raw):
                cid = rec.get("conversation_id")
                if not cid:
                    skipped += 1
                    continue
                grouped.setdefault(cid, []).append(rec)
                count += 1
                if count % progress_interval == 0:
                    log.info(f"processed {count} messages...")
        except Exception as e:
            msg = f"adapter error: {e}"
            log.warning(msg)
            errors += 1
            if len(sample_errors) < 5:
                sample_errors.append(msg)
            if fail_fast and errors > 3:
                raise LLPAdapterError(f"too many adapter errors ({errors})")

    stats = {"threads": 0, "messages": 0}
    manifest_index = []

    for cid, msgs in grouped.items():
        msgs.sort(key=lambda r: (r.get("ts") is None, r.get("ts"), r.get("message_id") or ""))
        thread_meta = {"message_count": len(msgs)}

        # 差分スキップ判定
        if should_skip_thread(cid, msgs, manifest_old):
            log.info(f"SKIP thread {cid} (unchanged)")
            skipped += 1
            continue

        outdir_thread = provider_dir / f"thread-{cid}"
        outdir_thread.mkdir(parents=True, exist_ok=True)
        outpath = outdir_thread / "parsed.jsonl"

        if not dry_run:
            lines = [
                {
                    "record_type": "thread",
                    "provider_id": provider,
                    "conversation_id": cid,
                    **thread_meta,
                }
            ]
            for m in msgs:
                if not validate_message(m, fail_fast=fail_fast):
                    skipped += 1
                    continue
                lines.append({"record_type": "message", "provider_id": provider, **m})
            safe_write_jsonl(outpath, lines, log)

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

    # manifest出力
    if not dry_run:
        manifest_path = provider_dir / "manifest.json"
        manifest_obj = {
            "schema_version": "1.3",
            "provider": provider,
            "policy": policy,
            "exported_at": datetime.utcnow().isoformat(),
            "index": {"threads": manifest_index},
        }
        manifest_path.write_text(json.dumps(manifest_obj, ensure_ascii=True, indent=2), encoding="utf-8")
        log.info(f"manifest saved: {manifest_path}")

    log.info(
        f"SUMMARY: threads={stats['threads']} messages={stats['messages']} errors={errors} skipped={skipped}"
    )
    return {**stats, "errors": errors, "skipped": skipped, "samples": sample_errors}


# ============================================================
# 7. CLI Entry (Debug Only)
# ============================================================

if __name__ == "__main__":
    import argparse
    from .cli import setup_logger

    setup_logger()

    parser = argparse.ArgumentParser(description="Parse LLM export logs to JSONL (final robust version)")
    parser.add_argument("--provider", required=True)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--outdir", type=Path, default=Path("artifacts/output"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--progress-interval", type=int, default=100)

    args = parser.parse_args()

    parse_to_jsonl(
        args.provider,
        args.input,
        args.outdir,
        dry_run=args.dry_run,
        fail_fast=args.fail_fast,
        progress_interval=args.progress_interval,
    )
