# src/llm_logparser/parser.py
from pathlib import Path
import json
from .providers import _get_provider
from .utils import dumps_jsonl, to_iso_utc
import importlib

def get_provider(name: str):
    try:
        mod = importlib.import_module(f"providers.{name}.adapter")
    except ModuleNotFoundError as e:
        raise RuntimeError(f"Provider '{name}' not found") from e
    # adapter側は「callable」をエクスポート（関数 or クラスのインスタンス化）
    if hasattr(mod, "get_adapter"):
        return mod.get_adapter()
    if hasattr(mod, "adapter"):
        return mod.adapter
    raise RuntimeError(f"Provider '{name}' has no adapter entry")

def parse_export(infile: Path, provider_name: str = "openai"):
    conversations = json.load(infile.open("r", encoding="utf-8"))
    adapter = _get_provider(provider_name)   # providers.<name>.adapter に一本化
    out: list[dict] = []
    for conv in conversations:
        out.extend(adapter(conv))
    return out

def write_jsonl_and_manifest(entries: list[dict], outdir: Path):
    outdir.mkdir(parents=True, exist_ok=True)
    jsonl = outdir / "messages-00001.jsonl"
    with jsonl.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(dumps_jsonl(e) + "\n")
    manifest = {
        "schema": "1.0",
        "providers": sorted({(e.get("meta") or {}).get("provider","unknown") for e in entries}),
        "generated_at": to_iso_utc(__import__("time").time()),
        "timezone_display": "Asia/Tokyo",
        "index": {"shards":[{"path":"messages-00001.jsonl","count":len(entries)}]},
        "id_policy": {"strategy":"composite","composite":{"parts":["conv_id","msg_id"],"separator":":"}}
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
