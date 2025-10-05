# src/llm_logparser/cli.py
from pathlib import Path
import json, argparse
from .parser import parse_chatgpt_export, write_jsonl_and_manifest
from .exporter import export_md, export_txt

def main():
    ap = argparse.ArgumentParser(prog="llm-logparser", description="Parse & Export ChatGPT logs")
    sp = ap.add_subparsers(dest="cmd", required=True)

    ap_parse = sp.add_parser("parse", help="raw JSON -> JSONL+manifest")
    ap_parse.add_argument("--in", dest="infile", required=True)
    ap_parse.add_argument("--out", dest="outdir", required=True)

    ap_export = sp.add_parser("export", help="JSONL -> md/txt")
    ap_export.add_argument("--in", dest="infile", required=True)
    ap_export.add_argument("--out", dest="outdir", required=True)
    ap_export.add_argument("--conv", dest="conv_id", default=None)
    ap_export.add_argument("--format", choices=["md","txt"], default="md")

    args = ap.parse_args()

    if args.cmd == "parse":
        entries = parse_chatgpt_export(Path(args.infile))
        write_jsonl_and_manifest(entries, Path(args.outdir))
        print(f"OK: wrote {len(entries)} entries")
    else:
        entries = [json.loads(l) for l in Path(args.infile).read_text(encoding="utf-8").splitlines()]
        conv_id = args.conv_id or (entries[0].get("conv_id","conv") if entries else "conv")
        p = export_md(entries, args.outdir, conv_id) if args.format=="md" else export_txt(entries, args.outdir, conv_id)
        print(p)

if __name__ == "__main__":
    main()
