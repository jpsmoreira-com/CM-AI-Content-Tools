#!/usr/bin/env bash
set -euo pipefail

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
fi

mkdir -p data/reports
chmod +x scripts/*.sh

if [ "${CONTENT_AI_SYNC_ASSETS:-false}" = "true" ]; then
  bash scripts/sync-content-ai-assets.sh "${CONTENT_AI_TARGET_WORKSPACE:-$PWD}"
fi

python -m py_compile main.py app.py tfs_dashboard.py run_server.py run_worker.py doc_automation/*.py
