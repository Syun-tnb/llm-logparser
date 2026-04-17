#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"[ERROR] file not found: {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[ERROR] invalid JSON in {path}: {exc}")


def mb(path: Path) -> float:
    return path.stat().st_size / (1024 * 1024)


def top_n(rows: list[dict], key: str, n: int) -> list[dict]:
    return sorted(
        rows,
        key=lambda row: (-int(row.get(key, 0)), str(row.get("token", ""))),
    )[:n]


def print_token_rows(title: str, rows: list[dict], *, limit: int = 20) -> None:
    print(f"\n=== {title} (top {min(limit, len(rows))}) ===")
    for row in rows[:limit]:
        token = row.get("token", "")
        count = row.get("count", 0)
        conv_count = row.get("conversation_count", 0)
        topic_count = row.get("topic_count", 0)
        roles = row.get("role_hints", {})
        co = row.get("cooccurrence", [])[:5]
        print(
            f"- token={token!r} "
            f"count={count} convs={conv_count} topics={topic_count} "
            f"roles={roles} cooccurrence={co}"
        )


def print_bundle_rows(title: str, rows: list[dict], *, limit: int = 20) -> None:
    print(f"\n=== {title} (top {min(limit, len(rows))}) ===")
    for row in rows[:limit]:
        bundle_id = row.get("bundle_id", "")
        weight = row.get("weight", 0.0)
        tokens = row.get("tokens", [])
        print(f"- {bundle_id}: weight={weight} tokens={tokens}")


def suspicious_generic_tokens(rows: list[dict], *, limit: int = 50) -> list[dict]:
    generic = {
        "the", "and", "for", "with", "from", "that", "this", "are", "was",
        "you", "your", "have", "not", "but", "just", "they", "them",
        "する", "ある", "いる", "これ", "それ", "ため", "よう", "こと",
        "ん", "や", "で", "に", "を", "は", "が", "と",
    }
    out: list[dict] = []
    for row in rows:
        token = str(row.get("token", "")).lower()
        if token in generic:
            out.append(row)
    return sorted(out, key=lambda r: (-int(r.get("count", 0)), str(r.get("token", ""))))[:limit]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect token-dictionary artifacts."
    )
    parser.add_argument(
        "--root",
        required=True,
        help="Provider root, e.g. artifacts/openai_chatgpt_full_run_20260411T190141/openai",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="How many rows to print for each section (default: 20)",
    )
    args = parser.parse_args()

    root = Path(args.root).expanduser()
    td_root = root / "l3" / "token-dictionary"
    dictionary_path = td_root / "dictionary.json"
    bundles_path = td_root / "bundles.json"
    provenance_path = td_root / "provenance.json"

    dictionary = load_json(dictionary_path)
    bundles = load_json(bundles_path)
    provenance = load_json(provenance_path)

    token_rows = list(dictionary.get("tokens", []))
    bundle_rows = list(bundles.get("bundles", []))

    print("=== Artifact Summary ===")
    print(f"dictionary.json   : {dictionary_path}")
    print(f"bundles.json      : {bundles_path}")
    print(f"provenance.json   : {provenance_path}")
    print(f"dictionary size   : {mb(dictionary_path):.2f} MB")
    print(f"bundles size      : {mb(bundles_path):.2f} MB")
    print(f"provenance size   : {mb(provenance_path):.4f} MB")
    print(f"provider_id       : {dictionary.get('provider_id')}")
    print(f"token_count field : {dictionary.get('token_count')}")
    print(f"actual tokens len : {len(token_rows)}")
    print(f"bundle_count field: {bundles.get('bundle_count')}")
    print(f"actual bundles len: {len(bundle_rows)}")
    print(f"source_inputs     : {dictionary.get('source_inputs')}")
    print(f"created_at        : {dictionary.get('created_at')}")

    by_count = top_n(token_rows, "count", args.top)
    by_conversations = top_n(token_rows, "conversation_count", args.top)
    by_topics = top_n(token_rows, "topic_count", args.top)
    by_cooccurrence = sorted(
        token_rows,
        key=lambda row: (-len(row.get("cooccurrence", [])), str(row.get("token", ""))),
    )[:args.top]
    generic_hits = suspicious_generic_tokens(token_rows, limit=args.top)

    print_token_rows("Top tokens by count", by_count, limit=args.top)
    print_token_rows("Top tokens by conversation_count", by_conversations, limit=args.top)
    print_token_rows("Top tokens by topic_count", by_topics, limit=args.top)
    print_token_rows("Top tokens by cooccurrence list length", by_cooccurrence, limit=args.top)

    if generic_hits:
        print_token_rows("Potentially generic tokens that still rank high", generic_hits, limit=args.top)
    else:
        print("\n=== Potentially generic tokens that still rank high ===")
        print("(none found in simple heuristic set)")

    bundle_top = sorted(
        bundle_rows,
        key=lambda row: (-float(row.get("weight", 0.0)), str(row.get("bundle_id", ""))),
    )[:args.top]
    print_bundle_rows("Top bundles by weight", bundle_top, limit=args.top)

    print("\n=== Provenance ===")
    for key in [
        "artifact_type",
        "schema_version",
        "producer_layer",
        "provider_id",
        "created_at",
        "source_inputs",
        "reproducibility_note",
        "token_count",
        "bundle_count",
    ]:
        print(f"- {key}: {provenance.get(key)}")


if __name__ == "__main__":
    main()