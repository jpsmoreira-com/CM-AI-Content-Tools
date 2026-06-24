#!/usr/bin/env bash
set -euo pipefail

TARGET_WORKSPACE="${CONTENT_AI_TARGET_WORKSPACE:-$PWD}"
CONTENT_AI_REPO_PATH="${CONTENT_AI_REPO_PATH:-/workspaces/CM-AI-Content-Skills}"
CONTENT_AI_REPO_URL="${CONTENT_AI_REPO_URL:-}"
CONTENT_AI_BRANCH="${CONTENT_AI_BRANCH:-main}"
PIPELINE_PROJECT_PATH="$CONTENT_AI_REPO_PATH/projects/tfs-doc-automation-mvp"
PIPELINE_VENV="${TFS_AUTONOMOUS_PIPELINE_VENV:-$HOME/.venvs/tfs-doc-automation-mvp}"
PIPELINE_PORT="${TFS_AUTONOMOUS_PIPELINE_PORT:-8010}"

infer_target_repository() {
  if [ -n "${CONTENT_AI_TARGET_REPOSITORY:-}" ]; then
    printf "%s" "$CONTENT_AI_TARGET_REPOSITORY"
    return
  fi
  if git -C "$TARGET_WORKSPACE" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    remote_url="$(git -C "$TARGET_WORKSPACE" config --get remote.origin.url || true)"
    remote_name="${remote_url##*/}"
    remote_name="${remote_name%.git}"
    if [ -n "$remote_name" ]; then
      printf "%s" "$remote_name"
      return
    fi
  fi
  basename "$TARGET_WORKSPACE"
}

if [ ! -d "$CONTENT_AI_REPO_PATH/.git" ]; then
  if [ -z "$CONTENT_AI_REPO_URL" ]; then
    echo "CONTENT_AI_REPO_URL is required when $CONTENT_AI_REPO_PATH is not already cloned." >&2
    exit 1
  fi
  mkdir -p "$(dirname "$CONTENT_AI_REPO_PATH")"
  git clone --branch "$CONTENT_AI_BRANCH" "$CONTENT_AI_REPO_URL" "$CONTENT_AI_REPO_PATH"
else
  git -C "$CONTENT_AI_REPO_PATH" fetch origin "$CONTENT_AI_BRANCH" --prune
  git -C "$CONTENT_AI_REPO_PATH" checkout "$CONTENT_AI_BRANCH"
  git -C "$CONTENT_AI_REPO_PATH" pull --ff-only origin "$CONTENT_AI_BRANCH"
fi

if [ ! -f "$PIPELINE_PROJECT_PATH/requirements.txt" ]; then
  echo "TFS Autonomous Pipeline project was not found at $PIPELINE_PROJECT_PATH." >&2
  exit 1
fi

if [ ! -x "$PIPELINE_VENV/bin/python" ]; then
  python3 -m venv "$PIPELINE_VENV"
fi

"$PIPELINE_VENV/bin/python" -m pip install --upgrade pip
"$PIPELINE_VENV/bin/python" -m pip install -r "$PIPELINE_PROJECT_PATH/requirements.txt"

CONTENT_AI_PIPELINE_PROJECT_PATH="$PIPELINE_PROJECT_PATH" \
CONTENT_AI_TARGET_REPOSITORY="$(infer_target_repository)" \
CONTENT_AI_TARGET_WORKSPACE="$TARGET_WORKSPACE" \
TFS_AUTONOMOUS_PIPELINE_PORT="$PIPELINE_PORT" \
"$PIPELINE_VENV/bin/python" - <<'PY'
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


project_path = Path(os.environ["CONTENT_AI_PIPELINE_PROJECT_PATH"])
target_workspace = os.environ["CONTENT_AI_TARGET_WORKSPACE"]
target_repository = os.environ["CONTENT_AI_TARGET_REPOSITORY"]
pipeline_port = os.environ.get("TFS_AUTONOMOUS_PIPELINE_PORT", "8010")

env_path = project_path / ".env"
env_example_path = project_path / ".env.example"
if not env_path.exists() and env_example_path.exists():
    shutil.copyfile(env_example_path, env_path)


def read_env_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def write_env_values(path: Path, values: dict[str, str]) -> None:
    lines = read_env_lines(path)
    seen: set[str] = set()
    updated_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            updated_lines.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in values:
            updated_lines.append(f"{key}={json.dumps(values[key])}")
            seen.add(key)
        else:
            updated_lines.append(line)
    for key, value in values.items():
        if key not in seen:
            updated_lines.append(f"{key}={json.dumps(value)}")
    path.write_text("\n".join(updated_lines).rstrip() + "\n", encoding="utf-8")


write_env_values(
    env_path,
    {
        "DOC_AUTOMATION_SERVER_HOST": "0.0.0.0",
        "DOC_AUTOMATION_SERVER_PORT": pipeline_port,
        "DOC_AUTOMATION_SERVER_AUTO_PORT": "false",
        "DOC_AUTOMATION_FINAL_REPORTS_PATH": f"{target_workspace}/.automation-reports",
        "DOC_AUTOMATION_CONTEXT_CAPTURE_WORKSPACE_SCAN_ROOTS_JSON": json.dumps([target_workspace]),
    },
)

base_config_path = project_path / "config" / "tfs_dashboard.json"
local_config_path = project_path / "config" / "tfs_dashboard.local.json"
source_config_path = local_config_path if local_config_path.exists() else base_config_path
config = json.loads(source_config_path.read_text(encoding="utf-8"))
portals = config.get("portals") or []
matched_portal = ""
for portal in portals:
    if str(portal.get("repository") or "").strip() == target_repository:
        portal["copilot_workspace_path"] = target_workspace
        portal["auth_mode"] = "Git Credentials"
        matched_portal = target_repository
        break
if not matched_portal and portals:
    portals[0]["copilot_workspace_path"] = target_workspace
    portals[0]["auth_mode"] = "Git Credentials"
    matched_portal = str(portals[0].get("repository") or "")
if matched_portal:
    config["DEFAULT_PORTAL"] = matched_portal
local_config_path.parent.mkdir(parents=True, exist_ok=True)
local_config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

try:
    subprocess.run(
        ["git", "-C", target_workspace, "rev-parse", "--is-inside-work-tree"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    git_dir = subprocess.run(
        ["git", "-C", target_workspace, "rev-parse", "--absolute-git-dir"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
except (OSError, subprocess.CalledProcessError):
    git_dir = ""
if git_dir:
    exclude_path = Path(git_dir) / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude_path.read_text(encoding="utf-8", errors="replace") if exclude_path.exists() else ""
    additions = ["/.agents/content-ai/", "/.automation-context/", "/.automation-reports/"]
    with exclude_path.open("a", encoding="utf-8") as handle:
        for addition in additions:
            if addition not in existing:
                handle.write(f"\n{addition}\n")
PY

bash "$PIPELINE_PROJECT_PATH/scripts/sync-content-ai-assets.sh" "$TARGET_WORKSPACE"

mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/tfs-autonomous-pipeline" <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd "$PIPELINE_PROJECT_PATH"
case "\${1:-dashboard}" in
  dashboard)
    exec "$PIPELINE_VENV/bin/python" -m uvicorn main:app --host "\${TFS_AUTONOMOUS_PIPELINE_HOST:-0.0.0.0}" --port "\${TFS_AUTONOMOUS_PIPELINE_PORT:-8010}"
    ;;
  worker)
    exec "$PIPELINE_VENV/bin/python" run_worker.py
    ;;
  stop)
    pkill -f "uvicorn main:app" || true
    pkill -f "$PIPELINE_PROJECT_PATH/run_worker.py" || true
    ;;
  sync-assets)
    exec bash "$PIPELINE_PROJECT_PATH/scripts/sync-content-ai-assets.sh" "\${2:-$TARGET_WORKSPACE}"
    ;;
  *)
    echo "Usage: tfs-autonomous-pipeline {dashboard|worker|stop|sync-assets}" >&2
    exit 2
    ;;
esac
EOF
chmod +x "$HOME/.local/bin/tfs-autonomous-pipeline"

echo "TFS Autonomous Pipeline bootstrap completed for $TARGET_WORKSPACE"
