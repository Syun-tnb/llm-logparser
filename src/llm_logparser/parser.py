# src/llm_logparser/parser.py
from pathlib import Path
import json
from .providers import get_provider
from .utils import dumps_jsonl, to_iso_utc

def parse_export(infile: Path, provider_name: str = "openai") -> list[dict]:
    data = json.loads(infile.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("input must be a JSON array")
    adapter = get_provider(provider_name)
    out = []
    for conv in data:
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
