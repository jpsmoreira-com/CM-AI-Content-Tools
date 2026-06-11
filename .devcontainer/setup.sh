#!/usr/bin/env bash
set -euo pipefail

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
fi

mkdir -p data/reports
python -m py_compile main.py app.py tfs_dashboard.py run_server.py run_worker.py doc_automation/*.py
