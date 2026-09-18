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

# One checkout: this tools repository (the pipeline itself). The shared assets
# (skills, subagents, guardrails) are NOT a checkout the pipeline needs — they are an
# APM package that sync-content-ai-assets.sh installs straight into the portal from
# CONTENT_AI_APM_DEPENDENCY. The image seed copy defaults to the tools copy this
# script ships in (the script lives at <tools-copy>/tfs-doc-automation-mvp/scripts/,
# resolved through the /usr/local/bin symlink). The writable runtime copy lives under
# one shared parent folder, provided via CONTENT_AI_WORKSPACE_ROOT; when the variable
# is not set, a warning is logged and /workspaces is used as default.
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]:-$0}")")" && pwd)"
SCRIPT_TOOLS_COPY="$(cd "$SCRIPT_DIR/../.." && pwd)"
CONTENT_AI_TOOLS_IMAGE_REPO_PATH="${CONTENT_AI_TOOLS_IMAGE_REPO_PATH:-$SCRIPT_TOOLS_COPY}"

if [ -z "${CONTENT_AI_WORKSPACE_ROOT:-}" ]; then
  echo "[content-ai-post-create][warning] CONTENT_AI_WORKSPACE_ROOT is not set; defaulting the repositories parent folder to /workspaces." >&2
  CONTENT_AI_WORKSPACE_ROOT="/workspaces"
fi

CONTENT_AI_TOOLS_REPO_PATH="${CONTENT_AI_TOOLS_REPO_PATH:-$CONTENT_AI_WORKSPACE_ROOT/CM-AI-Content-Tools}"
CONTENT_AI_TOOLS_BRANCH="${CONTENT_AI_TOOLS_BRANCH:-main}"
CONTENT_AI_SETTINGS_PATH="${CONTENT_AI_SETTINGS_PATH:-$CONTENT_AI_WORKSPACE_ROOT/.content-ai-settings/tfs-doc-automation-mvp}"
CONTENT_AI_LEGACY_SETTINGS_PATH="${CONTENT_AI_LEGACY_SETTINGS_PATH:-$CONTENT_AI_WORKSPACE_ROOT/.content-ai-settings/tfs-doc-automation-mvp}"
CONTENT_AI_COPILOT_CLI_HOST="${CONTENT_AI_COPILOT_CLI_HOST:-}"
CONTENT_AI_TFS_HOST="${CONTENT_AI_TFS_HOST:-tfs-product.cmf.criticalmanufacturing.com}"
CONTENT_AI_MARKDOWNLINT_IMAGE="${CONTENT_AI_MARKDOWNLINT_IMAGE:-proxy.criticalmanufacturing.io/davidanson/markdownlint-cli2:v0.12.1}"
CONTENT_AI_PREPULL_MARKDOWNLINT_IMAGE="${CONTENT_AI_PREPULL_MARKDOWNLINT_IMAGE:-true}"
CONTENT_AI_POST_CREATE_REPAIR_MISSING_VENV="${CONTENT_AI_POST_CREATE_REPAIR_MISSING_VENV:-false}"
CONTENT_AI_AUTO_STASH_ON_UPDATE="${CONTENT_AI_AUTO_STASH_ON_UPDATE:-true}"

PIPELINE_PROJECT_PATH="$CONTENT_AI_TOOLS_REPO_PATH/tfs-doc-automation-mvp"
if [ -x "/opt/content-ai/venvs/tfs-doc-automation-mvp/bin/python" ]; then
  PIPELINE_VENV="${TFS_AUTONOMOUS_PIPELINE_VENV:-/opt/content-ai/venvs/tfs-doc-automation-mvp}"
else
  PIPELINE_VENV="${TFS_AUTONOMOUS_PIPELINE_VENV:-$HOME/.venvs/tfs-doc-automation-mvp}"
fi
PIPELINE_PYTHON="$PIPELINE_VENV/bin/python"
PIPELINE_PORT="${TFS_AUTONOMOUS_PIPELINE_PORT:-7001}"
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

# Bring the runtime copy up to the commit this image ships.
#
# The runtime copy lives on the parent mount, outside the container, and survives
# every rebuild. It used to be refreshed by pulling from GitHub before each start;
# that made the branch, not the image, the thing that decided which version ran, and
# it required network egress on every start. Now that the pull is gone, this is what
# keeps the copy current — without it the copy is seeded once and never again, and the
# tool stays pinned to whatever commit this machine first saw no matter how many
# images ship afterwards.
#
# No stamp file is needed: the image seed and the copy made from it are both Git
# checkouts, so their HEADs are directly comparable.
refresh_runtime_copy() {
  local label="$1" image_path="$2" runtime_path="$3" branch="$4"
  local image_sha runtime_sha

  [ -d "$image_path/.git" ] || return 0
  image_sha="$(git -C "$image_path" rev-parse HEAD 2>/dev/null || true)"
  [ -n "$image_sha" ] || return 0

  runtime_sha="$(git -C "$runtime_path" rev-parse HEAD 2>/dev/null || true)"
  if [ -z "$runtime_sha" ]; then
    warn "$label runtime copy at $runtime_path is not a Git checkout, so it cannot be compared with the image. Remove it and re-run to take the image version ($image_sha)."
    return 0
  fi

  [ "$image_sha" != "$runtime_sha" ] || return 0

  log "Image ships $label $image_sha, runtime copy is at $runtime_sha - refreshing from the image"
  if git -C "$runtime_path" fetch -q --depth 1 --update-shallow \
        "$image_path" HEAD 2>/dev/null &&
     git -C "$runtime_path" reset -q --hard FETCH_HEAD 2>/dev/null; then
    git -C "$runtime_path" checkout -q -B "$branch" 2>/dev/null || true
    log "$label runtime copy refreshed to $image_sha"
  else
    warn "Could not refresh the $label runtime copy from the image seed. Continuing with $runtime_sha."
  fi
}

ensure_runtime_copy() {
  local label="$1" image_path="$2" runtime_path="$3" branch="$4" probe="$5"

  if [ -e "$runtime_path/$probe" ]; then
    log "Using $label at $runtime_path"
    refresh_runtime_copy "$label" "$image_path" "$runtime_path" "$branch"
    return 0
  fi

  if [ ! -e "$image_path/$probe" ]; then
    die "$label was not found. Expected image seed at $image_path or writable checkout at $runtime_path."
  fi

  if [ -d "$runtime_path" ] && [ -n "$(find "$runtime_path" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
    die "$runtime_path exists but does not contain the $label (missing $probe)."
  fi

  ensure_writable_directory "$(dirname "$runtime_path")" || die "Cannot create parent directory for $runtime_path"
  log "Seeding writable $label from $image_path to $runtime_path"
  mkdir -p "$runtime_path"
  cp -a "$image_path"/. "$runtime_path"/
}

ensure_project_copy() {
  ensure_runtime_copy "Content AI tools checkout" \
    "$CONTENT_AI_TOOLS_IMAGE_REPO_PATH" "$CONTENT_AI_TOOLS_REPO_PATH" "$CONTENT_AI_TOOLS_BRANCH" \
    "tfs-doc-automation-mvp/requirements.txt"
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

trust_target_workspace() {
  if ! command -v git >/dev/null 2>&1 || [ ! -d "$TARGET_WORKSPACE" ]; then
    return 0
  fi
  local resolved_workspace
  resolved_workspace="$(cd "$TARGET_WORKSPACE" && pwd -P)"
  git config --global --add safe.directory "$resolved_workspace" >/dev/null 2>&1 || true
  log "Trusted target workspace for Git: $resolved_workspace"
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
  CONTENT_AI_COPILOT_CLI_HOST="$CONTENT_AI_COPILOT_CLI_HOST" \
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
pipeline_port = os.environ.get("TFS_AUTONOMOUS_PIPELINE_PORT", "7001")
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


def read_copilot_host(path: Path) -> str:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        config = json.loads("\n".join(line for line in raw.splitlines() if not line.lstrip().startswith("//")))
    except Exception:
        return ""
    account = config.get("lastLoggedInUser") or {}
    host = str(account.get("host") or "").strip().rstrip("/")
    if host and "://" not in host:
        host = f"https://{host}"
    return host


runtime_values = {
    "DOC_AUTOMATION_SERVER_HOST": "0.0.0.0",
    "DOC_AUTOMATION_SERVER_PORT": pipeline_port,
    "DOC_AUTOMATION_SERVER_AUTO_PORT": "false",
    # Deliberately "false", and deliberately different from
    # DEFAULT_RUNTIME_SETTINGS in doc_automation/config.py, which is True.
    # This script also runs outside the CM devcontainer, where the CM root CA is
    # not in the trust store, and defaulting to verification on would fail every
    # TFS call with a certificate error in exactly the environments that have no
    # convenient fix. The caller that can guarantee the CA decides instead:
    # content-ai-ctl passes CONTENT_AI_TFS_VERIFY_SSL=true, because the image it
    # starts trusts the CM root CA OS-wide and exports REQUESTS_CA_BUNDLE.
    # Do not "fix" this to true — the safe-looking change breaks host use.
    "DOC_AUTOMATION_TFS_VERIFY_SSL": os.environ.get("CONTENT_AI_TFS_VERIFY_SSL", "false"),
    "DOC_AUTOMATION_TFS_CA_BUNDLE_PATH": os.environ.get("CONTENT_AI_TFS_CA_BUNDLE_PATH", ""),
    "DOC_AUTOMATION_EXECUTION_RUNTIME": "devcontainer",
    "DOC_AUTOMATION_FINAL_REPORTS_PATH": f"{target_workspace}/.automation-reports",
    "DOC_AUTOMATION_CONTEXT_CAPTURE_WORKSPACE_SCAN_ROOTS_JSON": json.dumps([target_workspace]),
}
copilot_host = os.environ.get("CONTENT_AI_COPILOT_CLI_HOST", "").strip()
if not copilot_host:
    copilot_host = read_copilot_host(settings_path / "copilot-home" / "config.json")
if copilot_host:
    runtime_values["DOC_AUTOMATION_COPILOT_CLI_HOST"] = copilot_host


write_env_values(
    env_path,
    runtime_values,
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
    additions = ["/.automation-context/", "/.automation-reports/"]
    with exclude_path.open("a", encoding="utf-8") as handle:
        for addition in additions:
            if addition not in existing:
                handle.write(f"\n{addition}\n")
PY
}

sync_content_ai_assets() {
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

copilot_home_is_authenticated() {
  local home_path="$1"
  [ -f "$home_path/config.json" ] || return 1
  grep -Eq '"(lastLoggedInUser|copilotTokens)"[[:space:]]*:' "$home_path/config.json"
}

ensure_persisted_github_copilot_home() {
  local persisted_home legacy_home backup_path timestamp current_target
  persisted_home="$CONTENT_AI_SETTINGS_PATH/copilot-home"
  legacy_home="$CONTENT_AI_LEGACY_SETTINGS_PATH/copilot-home"

  mkdir -p "$persisted_home"
  chmod 700 "$persisted_home" || true
  if [ "$legacy_home" != "$persisted_home" ] && \
     copilot_home_is_authenticated "$legacy_home" && \
     ! copilot_home_is_authenticated "$persisted_home"; then
    cp -a "$legacy_home/." "$persisted_home/"
    log "Migrated the persisted GitHub Copilot CLI session from the legacy settings path."
  fi

  if [ -L "$HOME/.copilot" ]; then
    current_target="$(readlink -f "$HOME/.copilot" 2>/dev/null || true)"
    if [ "$current_target" = "$(readlink -f "$persisted_home")" ]; then
      return 0
    fi
    rm "$HOME/.copilot"
  elif [ -d "$HOME/.copilot" ]; then
    cp -a "$HOME/.copilot/." "$persisted_home/"
    timestamp="$(date -u +"%Y%m%dT%H%M%SZ")"
    backup_path="$HOME/.copilot.local-backup-$timestamp"
    mv "$HOME/.copilot" "$backup_path"
    log "Migrated the native GitHub Copilot CLI state to $persisted_home (backup: $backup_path)."
  fi
  ln -s "$persisted_home" "$HOME/.copilot"
}

ensure_github_copilot_cli() {
  if [ "${TFS_AUTONOMOUS_INSTALL_GITHUB_COPILOT_CLI:-false}" != "true" ]; then
    return 0
  fi
  if command -v copilot >/dev/null 2>&1; then
    log "GitHub Copilot CLI is available."
    return 0
  fi
  if ! command -v npx >/dev/null 2>&1 && ! command -v npm >/dev/null 2>&1; then
    warn "GitHub Copilot CLI was not found and npx/npm is unavailable. The dashboard provider preflight will report this if GitHub Copilot CLI is selected."
    return 0
  fi
  log "GitHub Copilot CLI will be executed on-demand via npx (@github/copilot@latest) when used."
}

# publisher.name and version from the bridge manifest, which is what VS Code uses to
# name an installed extension directory. Fails loudly rather than returning a partial
# path: every caller builds a filesystem path from this.
bridge_identity() {
  local manifest="$1" field="$2"
  python3 - "$manifest" "$field" <<'PYBRIDGE' || { warn "Could not read $field from $manifest."; return 1; }
import json
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
field = sys.argv[2]
if field == "id":
    print(f"{manifest['publisher']}.{manifest['name']}")
else:
    print(manifest[field])
PYBRIDGE
}

install_vscode_copilot_bridge() {
  local bridge_directory="$PIPELINE_PROJECT_PATH/vscode-copilot-bridge"

  if [ ! -f "$bridge_directory/package.json" ] || [ ! -f "$bridge_directory/extension.js" ]; then
    warn "Content AI VS Code Copilot bridge source was not found at $bridge_directory."
    return 0
  fi

  # Both names carry the version, and VS Code keys the directory on publisher.name-version.
  # Reading them from the manifest means a bump cannot leave this looking for a package
  # the build no longer writes, or installing beside the previous copy instead of over it.
  local bridge_id bridge_version
  bridge_id="$(bridge_identity "$bridge_directory/package.json" id)" || return 0
  bridge_version="$(bridge_identity "$bridge_directory/package.json" version)" || return 0
  local bridge_vsix="$bridge_directory/content-ai-pipeline-bridge-$bridge_version.vsix"
  local extension_directory="$HOME/.vscode-server/extensions/$bridge_id-$bridge_version"

  mkdir -p "$(dirname "$extension_directory")"
  # Earlier versions too, not just this one: VS Code loads the highest version it finds,
  # so a leftover directory from a previous version is a stale extension waiting to win.
  rm -rf "$HOME/.vscode-server/extensions/$bridge_id-"*
  mkdir -p "$extension_directory"
  cp "$bridge_directory/package.json" "$bridge_directory/extension.js" "$bridge_directory/README.md" "$extension_directory/"
  log "Installed Content AI VS Code Copilot bridge files at $extension_directory"

  # The VSIX install below overwrites the files copied above, so a VSIX that is older
  # than the bridge source would silently reinstate a stale extension. Rebuild it from
  # the current source first to keep the two in step.
  local bridge_builder="$PIPELINE_PROJECT_PATH/scripts/build-vscode-copilot-bridge.sh"
  if [ -x "$bridge_builder" ]; then
    if bash "$bridge_builder" >/dev/null 2>&1; then
      log "Rebuilt the Content AI VS Code Copilot bridge VSIX from source."
    else
      warn "Could not rebuild the bridge VSIX from source; the existing package may be stale."
    fi
  fi

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
export CONTENT_AI_TOOLS_REPO_PATH="\${CONTENT_AI_TOOLS_REPO_PATH:-$CONTENT_AI_TOOLS_REPO_PATH}"
export CONTENT_AI_TOOLS_BRANCH="\${CONTENT_AI_TOOLS_BRANCH:-$CONTENT_AI_TOOLS_BRANCH}"
export CONTENT_AI_SETTINGS_PATH="\${CONTENT_AI_SETTINGS_PATH:-$CONTENT_AI_SETTINGS_PATH}"
export CONTENT_AI_TARGET_WORKSPACE="\${CONTENT_AI_TARGET_WORKSPACE:-$TARGET_WORKSPACE}"
export CODEX_HOME="\${CODEX_HOME:-$CODEX_HOME}"
export NPM_CONFIG_PREFIX="\${NPM_CONFIG_PREFIX:-$NPM_CONFIG_PREFIX}"
export PATH="\$HOME/.local/bin:\$NPM_CONFIG_PREFIX/bin:/usr/local/share/nvm/current/bin:\$PATH"
cd "$PIPELINE_PROJECT_PATH"
case "\${1:-dashboard}" in
  dashboard)
    exec "$PIPELINE_PYTHON" -m uvicorn main:app --host "\${TFS_AUTONOMOUS_PIPELINE_HOST:-0.0.0.0}" --port "\${TFS_AUTONOMOUS_PIPELINE_PORT:-$PIPELINE_PORT}"
    ;;
  worker)
    exec "$PIPELINE_PYTHON" run_worker.py
    ;;
  stop)
    pkill -f "uvicorn main:app" || true
    pkill -f "$PIPELINE_PROJECT_PATH/run_worker.py" || true
    ;;
  sync-assets)
    exec bash "$PIPELINE_PROJECT_PATH/scripts/sync-content-ai-assets.sh" "\${2:-$TARGET_WORKSPACE}"
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
    echo "Usage: tfs-autonomous-pipeline {dashboard|worker|stop|sync-assets|doctor}" >&2
    exit 2
    ;;
esac
EOF
  chmod +x "$wrapper"
  log "Created wrapper at $wrapper"
}

run_health_check() {
  # doc_automation is imported from the project directory, not installed into the
  # venv — run the check from there so it also works when the script is invoked
  # from a target repository workspace.
  cd "$PIPELINE_PROJECT_PATH"
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
trust_target_workspace
target_repository="$(infer_target_repository)"
log "Target repository: $target_repository"

configure_tfs_git_credentials
ensure_persisted_github_copilot_home
write_runtime_files "$target_repository"
sync_content_ai_assets
prepare_docker_config
check_codex_cli
ensure_github_copilot_cli
install_vscode_copilot_bridge
write_wrappers
run_health_check

log "Content AI post-create setup completed. Log: $SETUP_LOG"
