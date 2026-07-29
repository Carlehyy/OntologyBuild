#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if ! command -v uv >/dev/null 2>&1; then
  echo "uv was not found."
  echo "Install uv from https://docs.astral.sh/uv/ and run this file again."
  exit 1
fi

cd "$SCRIPT_DIR"
echo "Preparing the OpenOntology local configuration center..."
uv sync --locked
exec uv run --no-sync python -m app.main
