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

import json
import sys
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr
from zipfile import ZIP_DEFLATED, ZipFile

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
files = ["package.json", "extension.js", "README.md"]

manifest = json.loads((source / "package.json").read_text(encoding="utf-8"))
name = manifest["name"]
version = manifest["version"]
publisher = manifest["publisher"]
display_name = manifest.get("displayName", name)
description = manifest.get("description", "")
engine = manifest.get("engines", {}).get("vscode", "*")
categories = ",".join(manifest.get("categories", ["Other"]))
extension_kind = ",".join(manifest.get("extensionKind", []))

content_types = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="json" ContentType="application/json"/>'
    '<Default Extension="js" ContentType="application/javascript"/>'
    '<Default Extension="md" ContentType="text/markdown"/>'
    '<Default Extension="vsixmanifest" ContentType="text/xml"/>'
    "</Types>\n"
)

properties = [
    ('Microsoft.VisualStudio.Code.Engine', engine),
]
if extension_kind:
    properties.append(('Microsoft.VisualStudio.Code.ExtensionKind', extension_kind))
property_xml = "".join(
    f'<Property Id={quoteattr(key)} Value={quoteattr(value)}/>' for key, value in properties
)

vsix_manifest = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<PackageManifest Version="2.0.0" '
    'xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011" '
    'xmlns:d="http://schemas.microsoft.com/developer/vsx-schema-design/2011">'
    "<Metadata>"
    f'<Identity Language="en-US" Id={quoteattr(name)} '
    f'Version={quoteattr(version)} Publisher={quoteattr(publisher)}/>'
    f"<DisplayName>{escape(display_name)}</DisplayName>"
    f'<Description xml:space="preserve">{escape(description)}</Description>'
    "<Tags></Tags>"
    f"<Categories>{escape(categories)}</Categories>"
    "<GalleryFlags>Private</GalleryFlags>"
    f"<Properties>{property_xml}</Properties>"
    "</Metadata>"
    '<Installation><InstallationTarget Id="Microsoft.VisualStudio.Code"/></Installation>'
    "<Dependencies/>"
    "<Assets>"
    '<Asset Type="Microsoft.VisualStudio.Code.Manifest" '
    'Path="extension/package.json" Addressable="true"/>'
    '<Asset Type="Microsoft.VisualStudio.Services.Content.Details" '
    'Path="extension/README.md" Addressable="true"/>'
    "</Assets>"
    "</PackageManifest>\n"
)

with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
    archive.writestr("[Content_Types].xml", content_types)
    archive.writestr("extension.vsixmanifest", vsix_manifest)
    for relative in files:
        archive.write(source / relative, f"extension/{relative}")
PY
echo "Built $OUTPUT_PATH"
