#!/usr/bin/env bash
set -euo pipefail

# Lightweight post-create setup for devcontainer images that already include
# the Content AI project and its dependencies. This script configures the
# opened target repository; it does not clone from Git or reinstall packages
# unless explicitly told to repair a missing virtual environment.

TARGET_WORKSPACE="${CONTENT_AI_TARGET_WORKSPACE:-}"
if [ -z "$TARGET_WORKSPACE" ]; then
  if git -C "$PWD" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    TARGET_WORKSPACE="$PWD"
  elif [ -d "/app" ]; then
    TARGET_WORKSPACE="/app"
  else
    TARGET_WORKSPACE="$PWD"
  fi
fi
TARGET_WORKSPACE="$(cd "$TARGET_WORKSPACE" && pwd)"

CONTENT_AI_IMAGE_REPO_PATH="${CONTENT_AI_IMAGE_REPO_PATH:-/opt/content-ai/CM-AI-Content-Skills}"
CONTENT_AI_REPO_PATH="${CONTENT_AI_REPO_PATH:-/workspaces/CM-AI-Content-Skills}"
CONTENT_AI_BRANCH="${CONTENT_AI_BRANCH:-main}"
CONTENT_AI_SETTINGS_PATH="${CONTENT_AI_SETTINGS_PATH:-/workspaces/.content-ai-settings/tfs-doc-automation-mvp}"
CONTENT_AI_TFS_HOST="${CONTENT_AI_TFS_HOST:-tfs-product.cmf.criticalmanufacturing.com}"
CONTENT_AI_MARKDOWNLINT_IMAGE="${CONTENT_AI_MARKDOWNLINT_IMAGE:-proxy.criticalmanufacturing.io/davidanson/markdownlint-cli2:v0.12.1}"
CONTENT_AI_PREPULL_MARKDOWNLINT_IMAGE="${CONTENT_AI_PREPULL_MARKDOWNLINT_IMAGE:-true}"
CONTENT_AI_POST_CREATE_REPAIR_MISSING_VENV="${CONTENT_AI_POST_CREATE_REPAIR_MISSING_VENV:-false}"
CONTENT_AI_AUTO_STASH_ON_UPDATE="${CONTENT_AI_AUTO_STASH_ON_UPDATE:-true}"

PIPELINE_PROJECT_PATH="$CONTENT_AI_REPO_PATH/projects/tfs-doc-automation-mvp"
if [ -x "/opt/content-ai/venvs/tfs-doc-automation-mvp/bin/python" ]; then
  PIPELINE_VENV="${TFS_AUTONOMOUS_PIPELINE_VENV:-/opt/content-ai/venvs/tfs-doc-automation-mvp}"
else
  PIPELINE_VENV="${TFS_AUTONOMOUS_PIPELINE_VENV:-$HOME/.venvs/tfs-doc-automation-mvp}"
fi
PIPELINE_PYTHON="$PIPELINE_VENV/bin/python"
PIPELINE_PORT="${TFS_AUTONOMOUS_PIPELINE_PORT:-7000}"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
NPM_CONFIG_PREFIX="${NPM_CONFIG_PREFIX:-$HOME/.npm-global}"
export CODEX_HOME
export NPM_CONFIG_PREFIX
export PATH="$HOME/.local/bin:$NPM_CONFIG_PREFIX/bin:/usr/local/share/nvm/current/bin:$PATH"

mkdir -p "$CONTENT_AI_SETTINGS_PATH"
SETUP_LOG="$CONTENT_AI_SETTINGS_PATH/post-create.log"
exec > >(tee -a "$SETUP_LOG") 2>&1

log() {
  printf '[content-ai-post-create] %s\n' "$*"
}

warn() {
  printf '[content-ai-post-create][warning] %s\n' "$*" >&2
}

die() {
  printf '[content-ai-post-create][error] %s\n' "$*" >&2
  exit 1
}

ensure_writable_directory() {
  local path="$1"
  mkdir -p "$path" 2>/dev/null || {
    if command -v sudo >/dev/null 2>&1; then
      sudo mkdir -p "$path"
      sudo chown -R "$(id -u):$(id -g)" "$path"
    else
      return 1
    fi
  }
  if [ ! -w "$path" ] && command -v sudo >/dev/null 2>&1; then
    sudo chown -R "$(id -u):$(id -g)" "$path"
  fi
}

ensure_project_copy() {
  if [ -f "$PIPELINE_PROJECT_PATH/requirements.txt" ]; then
    log "Using Content AI project at $CONTENT_AI_REPO_PATH"
    return 0
  fi

  if [ ! -f "$CONTENT_AI_IMAGE_REPO_PATH/projects/tfs-doc-automation-mvp/requirements.txt" ]; then
    die "Content AI project was not found. Expected image seed at $CONTENT_AI_IMAGE_REPO_PATH or writable checkout at $CONTENT_AI_REPO_PATH."
  fi

  if [ -d "$CONTENT_AI_REPO_PATH" ] && [ -n "$(find "$CONTENT_AI_REPO_PATH" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
    die "CONTENT_AI_REPO_PATH exists but does not contain the pipeline project: $CONTENT_AI_REPO_PATH"
  fi

  ensure_writable_directory "$(dirname "$CONTENT_AI_REPO_PATH")" || die "Cannot create parent directory for $CONTENT_AI_REPO_PATH"
  log "Seeding writable Content AI project from $CONTENT_AI_IMAGE_REPO_PATH to $CONTENT_AI_REPO_PATH"
  mkdir -p "$CONTENT_AI_REPO_PATH"
  cp -a "$CONTENT_AI_IMAGE_REPO_PATH"/. "$CONTENT_AI_REPO_PATH"/
}

ensure_pipeline_python() {
  if [ -x "$PIPELINE_PYTHON" ]; then
    log "Using pipeline Python at $PIPELINE_PYTHON"
    return 0
  fi

  if [ "$CONTENT_AI_POST_CREATE_REPAIR_MISSING_VENV" != "true" ]; then
    die "Pipeline virtual environment was not found at $PIPELINE_VENV. Rebuild the image or set CONTENT_AI_POST_CREATE_REPAIR_MISSING_VENV=true."
  fi

  log "Repairing missing pipeline virtual environment at $PIPELINE_VENV"
  python3 -m venv "$PIPELINE_VENV"
  "$PIPELINE_PYTHON" -m pip install --upgrade pip
  "$PIPELINE_PYTHON" -m pip install -r "$PIPELINE_PROJECT_PATH/requirements.txt"
}

infer_target_repository() {
  if [ -n "${CONTENT_AI_TARGET_REPOSITORY:-}" ]; then
    printf "%s" "$CONTENT_AI_TARGET_REPOSITORY"
    return
  fi
  if git -C "$TARGET_WORKSPACE" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    local remote_url
    remote_url="$(git -C "$TARGET_WORKSPACE" config --get remote.origin.url || true)"
    local remote_name="${remote_url##*/}"
    remote_name="${remote_name%.git}"
    if [ -n "$remote_name" ]; then
      printf "%s" "$remote_name"
      return
    fi
  fi
  basename "$TARGET_WORKSPACE"
}

set_git_store_helper() {
  if ! command -v git >/dev/null 2>&1; then
    return 0
  fi
  git config --global --unset-all credential.helper >/dev/null 2>&1 || true
  git config --global credential.helper store
  git config --global credential.useHttpPath true
}

restore_persisted_git_credentials() {
  local persisted_credentials_path="$CONTENT_AI_SETTINGS_PATH/git-credentials"
  if [ ! -f "$persisted_credentials_path" ]; then
    warn "No persisted TFS Git credential store was found yet."
    return 0
  fi
  cp "$persisted_credentials_path" "$HOME/.git-credentials"
  chmod 600 "$HOME/.git-credentials" || true
  set_git_store_helper
  log "Restored persisted TFS Git credentials."
}

git_credentials_are_available() {
  set_git_store_helper
  local credential_output
  credential_output="$(printf "url=https://%s/\n\n" "$CONTENT_AI_TFS_HOST" | timeout 10 git credential fill 2>/dev/null || true)"
  printf "%s\n" "$credential_output" | grep -q '^password='
}

configure_tfs_git_credentials() {
  if ! command -v git >/dev/null 2>&1; then
    warn "Git was not found; skipping TFS credential preflight."
    return 0
  fi

  restore_persisted_git_credentials
  if git_credentials_are_available; then
    log "TFS Git credentials are available for $CONTENT_AI_TFS_HOST."
    return 0
  fi

  warn "TFS Git credentials are not available in this container yet. Configure them from the dashboard Settings page or mount CONTENT_AI_HOST_GIT_CREDENTIALS_PATH."
}

write_runtime_files() {
  local target_repository="$1"
  CONTENT_AI_PIPELINE_PROJECT_PATH="$PIPELINE_PROJECT_PATH" \
  CONTENT_AI_SETTINGS_PATH="$CONTENT_AI_SETTINGS_PATH" \
  CONTENT_AI_TARGET_REPOSITORY="$target_repository" \
  CONTENT_AI_TARGET_WORKSPACE="$TARGET_WORKSPACE" \
  TFS_AUTONOMOUS_PIPELINE_PORT="$PIPELINE_PORT" \
  "$PIPELINE_PYTHON" - <<'PY'
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


project_path = Path(os.environ["CONTENT_AI_PIPELINE_PROJECT_PATH"])
settings_path = Path(os.environ["CONTENT_AI_SETTINGS_PATH"])
target_workspace = os.environ["CONTENT_AI_TARGET_WORKSPACE"]
target_repository = os.environ["CONTENT_AI_TARGET_REPOSITORY"]
pipeline_port = os.environ.get("TFS_AUTONOMOUS_PIPELINE_PORT", "7000")
settings_path.mkdir(parents=True, exist_ok=True)

env_path = project_path / ".env"
env_example_path = project_path / ".env.example"
persisted_env_path = settings_path / ".env"
if not env_path.exists():
    if persisted_env_path.exists():
        shutil.copyfile(persisted_env_path, env_path)
    elif env_example_path.exists():
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
        "DOC_AUTOMATION_TFS_VERIFY_SSL": os.environ.get("CONTENT_AI_TFS_VERIFY_SSL", "false"),
        "DOC_AUTOMATION_TFS_CA_BUNDLE_PATH": os.environ.get("CONTENT_AI_TFS_CA_BUNDLE_PATH", ""),
        "DOC_AUTOMATION_EXECUTION_RUNTIME": "devcontainer",
        "DOC_AUTOMATION_FINAL_REPORTS_PATH": f"{target_workspace}/.automation-reports",
        "DOC_AUTOMATION_CONTEXT_CAPTURE_WORKSPACE_SCAN_ROOTS_JSON": json.dumps([target_workspace]),
    },
)
shutil.copyfile(env_path, persisted_env_path)


def load_json(path: Path, default: dict) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def default_portal(repository: str) -> dict:
    return {
        "base_url": os.environ.get("CONTENT_AI_TFS_BASE_URL", "https://tfs-product.cmf.criticalmanufacturing.com/Products"),
        "project": os.environ.get("CONTENT_AI_TFS_PROJECT", "Product"),
        "repository": repository,
        "work_item_project": os.environ.get("CONTENT_AI_WORK_ITEM_PROJECT", "Product"),
        "work_item_team": "",
        "work_item_area_path": os.environ.get("CONTENT_AI_WORK_ITEM_AREA_PATH", "Product\\Development"),
        "copilot_workspace_path": target_workspace,
        "team": "",
        "api_version": "6.0",
        "branch_chain": [],
        "lookback_days": 7,
        "max_prs_per_branch": 150,
        "verify_work_items_via_api": True,
        "cherry_pick_skip_labels": ["No CP", "no-cp", "not to cp"],
        "auth_mode": "Git Credentials",
    }


base_config_path = project_path / "config" / "tfs_dashboard.json"
local_config_path = project_path / "config" / "tfs_dashboard.local.json"
persisted_config_path = settings_path / "tfs_dashboard.local.json"
if not local_config_path.exists() and persisted_config_path.exists():
    local_config_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(persisted_config_path, local_config_path)

source_config_path = local_config_path if local_config_path.exists() else base_config_path
config = load_json(source_config_path, {"DEFAULT_PORTAL": target_repository, "portals": [default_portal(target_repository)]})
portals = config.get("portals") or []
if not portals:
    portals = [default_portal(target_repository)]

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

config["portals"] = portals
if matched_portal:
    config["DEFAULT_PORTAL"] = matched_portal

local_config_path.parent.mkdir(parents=True, exist_ok=True)
local_config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
shutil.copyfile(local_config_path, persisted_config_path)

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
    additions = ["/.agents/content-ai/", "/AGENTS.md", "/.automation-context/", "/.automation-reports/"]
    with exclude_path.open("a", encoding="utf-8") as handle:
        for addition in additions:
            if addition not in existing:
                handle.write(f"\n{addition}\n")
PY
}

sync_content_ai_assets() {
  CONTENT_AI_REPO_PATH="$CONTENT_AI_REPO_PATH" \
  CONTENT_AI_TARGET_WORKSPACE="$TARGET_WORKSPACE" \
  bash "$PIPELINE_PROJECT_PATH/scripts/sync-content-ai-assets.sh" "$TARGET_WORKSPACE"
}

prepare_docker_config() {
  local docker_config_path="${CONTENT_AI_DOCKER_CONFIG_PATH:-$CONTENT_AI_SETTINGS_PATH/docker-config}"
  mkdir -p "$docker_config_path"
  if [ ! -f "$docker_config_path/config.json" ]; then
    printf '{}\n' > "$docker_config_path/config.json"
  fi
  log "Prepared isolated Docker config at $docker_config_path"

  if [ "$CONTENT_AI_PREPULL_MARKDOWNLINT_IMAGE" = "true" ] && command -v docker >/dev/null 2>&1; then
    if DOCKER_CONFIG="$docker_config_path" timeout 120 docker pull "$CONTENT_AI_MARKDOWNLINT_IMAGE" >/dev/null 2>&1; then
      log "Markdownlint Docker image is available."
    else
      warn "Could not pre-pull markdownlint image. Push fallback will still retry with an isolated DOCKER_CONFIG."
    fi
  fi
}

check_codex_cli() {
  if ! command -v codex >/dev/null 2>&1; then
    warn "Codex CLI was not found on PATH. The dashboard provider preflight will report this if Codex CLI is selected."
    return 0
  fi
  if timeout 45 codex doctor --json >/dev/null 2>&1; then
    log "Codex CLI doctor completed successfully."
  else
    warn "Codex CLI doctor needs attention. Use dashboard Settings to complete authentication if needed."
  fi
}

ensure_persisted_github_copilot_home() {
  local persisted_home backup_path timestamp
  persisted_home="$CONTENT_AI_SETTINGS_PATH/copilot-home"

  if [ -L "$HOME/.copilot" ]; then
    return 0
  fi

  mkdir -p "$persisted_home"
  chmod 700 "$persisted_home" || true
  if [ -d "$HOME/.copilot" ]; then
    cp -a "$HOME/.copilot/." "$persisted_home/"
    timestamp="$(date -u +"%Y%m%dT%H%M%SZ")"
    backup_path="$HOME/.copilot.local-backup-$timestamp"
    mv "$HOME/.copilot" "$backup_path"
    log "Migrated the native GitHub Copilot CLI state to $persisted_home (backup: $backup_path)."
  fi
  ln -s "$persisted_home" "$HOME/.copilot"
}

ensure_github_copilot_cli() {
  if [ "${TFS_AUTONOMOUS_INSTALL_GITHUB_COPILOT_CLI:-true}" != "true" ]; then
    return 0
  fi
  if command -v copilot >/dev/null 2>&1; then
    log "GitHub Copilot CLI is available."
    return 0
  fi
  if ! command -v npm >/dev/null 2>&1; then
    warn "GitHub Copilot CLI was not found and npm is unavailable. The dashboard provider preflight will report this if GitHub Copilot CLI is selected."
    return 0
  fi
  log "Installing GitHub Copilot CLI into $NPM_CONFIG_PREFIX"
  npm install -g @github/copilot
}

install_vscode_copilot_bridge() {
  local bridge_directory="$PIPELINE_PROJECT_PATH/vscode-copilot-bridge"
  local bridge_vsix="$bridge_directory/content-ai-pipeline-bridge-0.1.0.vsix"
  local extension_directory="$HOME/.vscode-server/extensions/criticalmanufacturing.cmf-content-ai-pipeline-bridge-0.1.0"

  if [ ! -f "$bridge_directory/package.json" ] || [ ! -f "$bridge_directory/extension.js" ]; then
    warn "Content AI VS Code Copilot bridge source was not found at $bridge_directory."
    return 0
  fi

  mkdir -p "$(dirname "$extension_directory")"
  rm -rf "$extension_directory"
  mkdir -p "$extension_directory"
  cp "$bridge_directory/package.json" "$bridge_directory/extension.js" "$bridge_directory/README.md" "$extension_directory/"
  log "Installed Content AI VS Code Copilot bridge files at $extension_directory"

  # When invoked by a connected VS Code remote session, the CLI can register the
  # VSIX immediately. The copied extension files remain a reliable fallback for
  # the next devcontainer reconnect if the Remote CLI socket is not available.
  if [ -n "${VSCODE_IPC_HOOK_CLI:-}" ] && command -v code >/dev/null 2>&1 && [ -f "$bridge_vsix" ]; then
    if timeout 20 code --install-extension "$bridge_vsix" --force >/dev/null 2>&1; then
      log "Registered Content AI VS Code Copilot bridge through the VS Code Remote CLI."
    else
      warn "Could not register the bridge through the Remote CLI. It will be discovered on the next VS Code remote reconnect."
    fi
  fi
}

write_wrappers() {
  mkdir -p "$HOME/.local/bin"
  local wrapper="$HOME/.local/bin/tfs-autonomous-pipeline"
  cat > "$wrapper" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export CONTENT_AI_REPO_PATH="\${CONTENT_AI_REPO_PATH:-$CONTENT_AI_REPO_PATH}"
export CONTENT_AI_BRANCH="\${CONTENT_AI_BRANCH:-$CONTENT_AI_BRANCH}"
export CONTENT_AI_SETTINGS_PATH="\${CONTENT_AI_SETTINGS_PATH:-$CONTENT_AI_SETTINGS_PATH}"
export CONTENT_AI_TARGET_WORKSPACE="\${CONTENT_AI_TARGET_WORKSPACE:-$TARGET_WORKSPACE}"
export CODEX_HOME="\${CODEX_HOME:-$CODEX_HOME}"
export NPM_CONFIG_PREFIX="\${NPM_CONFIG_PREFIX:-$NPM_CONFIG_PREFIX}"
export PATH="\$HOME/.local/bin:\$NPM_CONFIG_PREFIX/bin:/usr/local/share/nvm/current/bin:\$PATH"
cd "$PIPELINE_PROJECT_PATH"
sync_project() {
  if [ ! -d "\$CONTENT_AI_REPO_PATH/.git" ]; then
    echo "Content AI runtime copy is not a Git checkout at \$CONTENT_AI_REPO_PATH. Continuing with the image-provided version. Rebuild the image or rerun post-create from a Git-backed runtime copy to update the tool." >&2
    return 0
  fi
  if ! git -C "\$CONTENT_AI_REPO_PATH" remote get-url origin >/dev/null 2>&1; then
    echo "Content AI runtime copy has no origin remote. Continuing with the current local version." >&2
    return 0
  fi
  echo "Syncing Content AI runtime copy before starting the pipeline..."
  git -C "\$CONTENT_AI_REPO_PATH" fetch origin "\$CONTENT_AI_BRANCH" --prune
  if [ -n "\$(git -C "\$CONTENT_AI_REPO_PATH" status --porcelain --untracked-files=all)" ]; then
    if [ "\${CONTENT_AI_AUTO_STASH_ON_UPDATE:-true}" != "true" ]; then
      git -C "\$CONTENT_AI_REPO_PATH" status --short --untracked-files=all >&2 || true
      echo "Content AI runtime copy has local changes. Run 'tfs-autonomous-pipeline sync-project' after reviewing them, or set CONTENT_AI_AUTO_STASH_ON_UPDATE=true." >&2
      return 1
    fi
    backup_dir="\${CONTENT_AI_SETTINGS_PATH:-$CONTENT_AI_SETTINGS_PATH}/backups"
    timestamp="\$(date -u +"%Y%m%dT%H%M%SZ")"
    mkdir -p "\$backup_dir"
    git -C "\$CONTENT_AI_REPO_PATH" status --short --untracked-files=all > "\$backup_dir/content-ai-pre-wrapper-update-status-\$timestamp.txt" || true
    git -C "\$CONTENT_AI_REPO_PATH" diff > "\$backup_dir/content-ai-pre-wrapper-update-worktree-\$timestamp.patch" || true
    git -C "\$CONTENT_AI_REPO_PATH" diff --cached > "\$backup_dir/content-ai-pre-wrapper-update-index-\$timestamp.patch" || true
    git -C "\$CONTENT_AI_REPO_PATH" stash push -u -m "content-ai auto-stash before wrapper update \$timestamp"
    echo "Local Content AI runtime changes were stashed before starting the pipeline."
  fi
  git -C "\$CONTENT_AI_REPO_PATH" checkout "\$CONTENT_AI_BRANCH"
  git -C "\$CONTENT_AI_REPO_PATH" pull --ff-only origin "\$CONTENT_AI_BRANCH"
}
case "\${1:-dashboard}" in
  dashboard)
    sync_project
    exec "$PIPELINE_PYTHON" -m uvicorn main:app --host "\${TFS_AUTONOMOUS_PIPELINE_HOST:-0.0.0.0}" --port "\${TFS_AUTONOMOUS_PIPELINE_PORT:-$PIPELINE_PORT}"
    ;;
  worker)
    sync_project
    exec "$PIPELINE_PYTHON" run_worker.py
    ;;
  stop)
    pkill -f "uvicorn main:app" || true
    pkill -f "$PIPELINE_PROJECT_PATH/run_worker.py" || true
    ;;
  sync-assets)
    exec bash "$PIPELINE_PROJECT_PATH/scripts/sync-content-ai-assets.sh" "\${2:-$TARGET_WORKSPACE}"
    ;;
  sync-project)
    sync_project
    ;;
  doctor)
    echo "Project: $PIPELINE_PROJECT_PATH"
    echo "Python: $PIPELINE_PYTHON"
    echo "Settings: \$CONTENT_AI_SETTINGS_PATH"
    "$PIPELINE_PYTHON" - <<'PY'
from doc_automation.config import load_app_config, load_runtime_settings
print("Default portal:", load_app_config().get("DEFAULT_PORTAL"))
print("Provider:", load_runtime_settings().get("copilot_provider"))
PY
    ;;
  *)
    echo "Usage: tfs-autonomous-pipeline {dashboard|worker|stop|sync-assets|sync-project|doctor}" >&2
    exit 2
    ;;
esac
EOF
  chmod +x "$wrapper"
  log "Created wrapper at $wrapper"
}

run_health_check() {
  "$PIPELINE_PYTHON" - <<'PY'
import importlib

for module_name in ["doc_automation.config", "doc_automation.services", "doc_automation.web"]:
    importlib.import_module(module_name)
print("Python import health check passed.")
PY
}

log "Starting Content AI post-create setup."
log "Target workspace: $TARGET_WORKSPACE"
log "Persistent settings: $CONTENT_AI_SETTINGS_PATH"

ensure_project_copy
ensure_pipeline_python
target_repository="$(infer_target_repository)"
log "Target repository: $target_repository"

configure_tfs_git_credentials
write_runtime_files "$target_repository"
sync_content_ai_assets
prepare_docker_config
check_codex_cli
ensure_persisted_github_copilot_home
ensure_github_copilot_cli
install_vscode_copilot_bridge
write_wrappers
run_health_check

log "Content AI post-create setup completed. Log: $SETUP_LOG"
