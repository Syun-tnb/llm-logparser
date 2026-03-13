#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURE="$ROOT_DIR/scripts/openai_minimal.json"
OUTDIR="$ROOT_DIR/artifacts"

export PYTHONPATH="$ROOT_DIR/src"
export PYTHONDONTWRITEBYTECODE=1

uv run llm-logparser parse --provider openai --input "$FIXTURE" --outdir "$OUTDIR"
uv run llm-logparser export --input "$OUTDIR/openai/thread-openai_minimal/parsed.jsonl" --out "$OUTDIR/openai/thread-openai_minimal/thread.md"

echo "OK: $OUTDIR/openai/thread-openai_minimal/thread.md"
