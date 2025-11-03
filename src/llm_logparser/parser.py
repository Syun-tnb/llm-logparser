# src/llm_logparser/parser.py
from __future__ import annotations
import json
import importlib
from pathlib import Path
from typing import Iterable, Dict, Any, Generator


# ------------------------------------------------------------
# 1. Provider Adapter Loader
# ------------------------------------------------------------
def load_adapter(provider: str):
    """
    動的に provider adapter をロードする。
    各 adapter モジュールは llm_logparser.providers.{provider}.adapter に配置。
    必須: get_adapter()
    任意: get_manifest(), get_policy()
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
def iter_json_records(path: Path) -> Generator[Dict[str, Any], None, None]:
    """
    巨大JSON/JSONLをストリーム的に読み込む。
    [] 配列形式でも NDJSON 形式でも処理可能。
    壊れ行は警告してスキップ。
    """
    with path.open("r", encoding="utf-8") as f:
        first = f.read(1)
        f.seek(0)
        if first == "[":
            # JSON array（MVPではメモリロードを許容）
            try:
                for x in json.load(f):
                    yield x
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Invalid JSON array input: {e}")
        else:
            # NDJSON
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as e:
                    print(f"[WARN] skip invalid JSON line: {e}")
                    continue


# ------------------------------------------------------------
# 3. Thread JSONL Writer
# ------------------------------------------------------------
def write_thread_jsonl(
    provider: str,
    conv_id: str,
    thread_meta: Dict[str, Any],
    messages: Iterable[Dict[str, Any]],
    out_root: Path,
) -> Path:
    """
    スレッド単位の parsed.jsonl を出力する。
    Unicodeエスケープは保持（ensure_ascii=True）
    出力先: {out_root}/{provider}/thread-{conv_id}/parsed.jsonl
    """
    provider_dir = out_root / provider
    provider_dir.mkdir(parents=True, exist_ok=True)
    outdir = provider_dir / f"thread-{conv_id}"
    outdir.mkdir(parents=True, exist_ok=True)
    outpath = outdir / "parsed.jsonl"

    with outpath.open("w", encoding="utf-8") as f:
        # thread meta（conversation_id重複回避）
        thread_row = {
            "record_type": "thread",
            "provider_id": provider,
            "conversation_id": conv_id,
            **{k: v for k, v in thread_meta.items() if k != "conversation_id"},
        }
        f.write(json.dumps(thread_row, ensure_ascii=True) + "\n")

        # message rows
        for m in messages:
            row = {"record_type": "message", "provider_id": provider, **m}
            f.write(json.dumps(row, ensure_ascii=True) + "\n")

    return outpath


# ------------------------------------------------------------
# 4. Main Parser (MVP Core)
# ------------------------------------------------------------
def parse_to_jsonl(provider: str, input_path: Path, outdir: Path) -> Dict[str, Any]:
    """
    各プロバイダのエクスポートJSONを解析し、
    スレッド単位のJSONLファイルを生成する。
    """
    adapter_func, manifest, policy = load_adapter(provider)
    grouped: Dict[str, list] = {}

    # Adapterを通して正規化レコードを生成
    for raw in iter_json_records(input_path):
        try:
            for rec in adapter_func(raw):
                cid = rec.get("conv_id") or rec.get("conversation_id")
                if not cid:
                    continue  # スキップ（壊れデータ・仕様違反）
                grouped.setdefault(cid, []).append(rec)
        except Exception as e:
            print(f"[WARN] adapter processing error: {e}")
            continue  # 1件の例外で全体停止しない

    # 出力処理
    stats = {"threads": 0, "messages": 0}
    provider_dir = outdir / provider
    provider_dir.mkdir(parents=True, exist_ok=True)
    manifest_index = []

    for cid, msgs in grouped.items():
        # ts優先・Noneは末尾・セカンダリキー msg_id で安定ソート
        msgs.sort(key=lambda r: (r.get("ts") is None, r.get("ts"), r.get("msg_id") or ""))
        thread_meta = {"message_count": len(msgs)}

        outpath = write_thread_jsonl(provider, cid, thread_meta, msgs, outdir)
        stats["threads"] += 1
        stats["messages"] += len(msgs)

        # manifest index（範囲情報を付加）
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

    # manifest.json を生成
    manifest_path = provider_dir / "manifest.json"
    manifest_obj = {
        "schema_version": "1.0",
        "provider": provider,
        "policy": policy,
        "index": {"threads": manifest_index},
    }
    manifest_path.write_text(
        json.dumps(manifest_obj, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )

    print(f"[INFO] Parsed {stats['threads']} threads, {stats['messages']} messages.")
    return stats


# ------------------------------------------------------------
# 5. CLI entry helper (optional)
# ------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Parse LLM export logs to JSONL")
    parser.add_argument("--provider", required=True, help="Provider ID (e.g., openai)")
    parser.add_argument("--input", required=True, type=Path, help="Input JSON/JSONL path")
    parser.add_argument(
        "--outdir",
        required=False,
        type=Path,
        default=Path("artifacts/output"),
        help="Output root directory",
    )
    args = parser.parse_args()
    parse_to_jsonl(args.provider, args.input, args.outdir)
