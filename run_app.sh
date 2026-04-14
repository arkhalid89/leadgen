#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$ROOT_DIR/venv/bin/python"

if [[ ! -x "$VENV_PY" ]]; then
  echo "Python virtual environment not found at: $VENV_PY"
  echo "Create it first, then install requirements."
  exit 1
fi

cd "$ROOT_DIR"

echo "Starting LeadGen..."
exec "$VENV_PY" app.py
