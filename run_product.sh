#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
  UV="$ROOT/.venv/bin/uvicorn"
else
  PY=python3
  UV=uvicorn
fi
# Ensure chromium for Playwright once
"$PY" -m playwright install chromium >/dev/null 2>&1 || true
exec "$UV" app.product_web:app --host 0.0.0.0 --port "${PORT:-8080}" --reload
