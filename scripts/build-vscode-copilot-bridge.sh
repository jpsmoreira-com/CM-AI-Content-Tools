#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BRIDGE_DIR="$PROJECT_DIR/vscode-copilot-bridge"
OUTPUT_PATH="$PROJECT_DIR/vscode-copilot-bridge/content-ai-pipeline-bridge-0.1.0.vsix"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required to package the Content AI VS Code bridge." >&2
  exit 1
fi

rm -f "$OUTPUT_PATH"
python3 - "$BRIDGE_DIR" "$OUTPUT_PATH" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
files = ["package.json", "extension.js", "README.md"]

with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
    archive.writestr(
        "[Content_Types].xml",
        """<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
        "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">"
        "<Default Extension=\"json\" ContentType=\"application/json\"/>"
        "<Default Extension=\"js\" ContentType=\"application/javascript\"/>"
        "<Default Extension=\"md\" ContentType=\"text/markdown\"/>"
        "</Types>\n""",
    )
    for relative in files:
        archive.write(source / relative, f"extension/{relative}")
PY
echo "Built $OUTPUT_PATH"
