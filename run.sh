#!/usr/bin/env bash
set -e

source venv/bin/activate
source export_secrets.sh
uvicorn app.webhook:app --reload --port 8000
