#!/usr/bin/env bash
set -euo pipefail

rm -rf .venv
uv sync --extra dev
